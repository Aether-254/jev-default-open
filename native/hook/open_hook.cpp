#include "protocol.hpp"

#include <detours.h>
#include <objbase.h>
#include <shellapi.h>
#include <windows.h>

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <cwctype>
#include <iomanip>
#include <set>
#include <sstream>
#include <string>

namespace {

using ShellExecuteWFn = HINSTANCE(WINAPI*)(
    HWND, LPCWSTR, LPCWSTR, LPCWSTR, LPCWSTR, INT
);
using ShellExecuteExWFn = BOOL(WINAPI*)(SHELLEXECUTEINFOW*);

ShellExecuteWFn g_shell_execute_w = ShellExecuteW;
ShellExecuteExWFn g_shell_execute_ex_w = ShellExecuteExW;
thread_local bool g_inside_hook = false;

class ReentryGuard {
public:
    ReentryGuard() { g_inside_hook = true; }
    ~ReentryGuard() { g_inside_hook = false; }
    ReentryGuard(const ReentryGuard&) = delete;
    ReentryGuard& operator=(const ReentryGuard&) = delete;
};

std::wstring Lower(std::wstring value) {
    std::transform(value.begin(), value.end(), value.begin(),
                   [](wchar_t ch) { return static_cast<wchar_t>(towlower(ch)); });
    return value;
}

std::wstring CurrentExecutable() {
    std::wstring buffer(32768, L'\0');
    DWORD length = static_cast<DWORD>(buffer.size());
    if (!QueryFullProcessImageNameW(GetCurrentProcess(), 0, buffer.data(), &length)) {
        return {};
    }
    buffer.resize(length);
    return buffer;
}

bool IsExcludedProcess() {
    const std::wstring path = Lower(CurrentExecutable());
    const auto slash = path.find_last_of(L"\\/");
    const std::wstring name = slash == std::wstring::npos ? path : path.substr(slash + 1);
    return path.empty() || name == L"native_host.exe" || name == L"python.exe" ||
           name == L"pythonw.exe";
}

bool IsEligibleVerb(LPCWSTR verb) {
    if (verb == nullptr || *verb == L'\0') {
        return true;
    }
    const std::wstring value = Lower(verb);
    return value == L"open" || value == L"edit";
}

bool IsUri(const std::wstring& target) {
    const auto colon = target.find(L':');
    if (colon == std::wstring::npos) {
        return false;
    }
    return !(colon == 1 && iswalpha(target[0]));
}

bool IsDangerousTarget(const std::wstring& target) {
    if (target.empty()) {
        return true;
    }
    if (IsUri(target)) {
        const auto colon = target.find(L':');
        const std::wstring scheme = Lower(target.substr(0, colon));
        return scheme != L"http" && scheme != L"https";
    }
    // Do not perform filesystem I/O on an intercepted application's thread.
    // Network, device and relative paths keep their original Shell semantics.
    if (target.size() < 3 || !iswalpha(target[0]) || target[1] != L':' ||
        (target[2] != L'\\' && target[2] != L'/')) {
        return true;
    }
    const auto dot = target.find_last_of(L'.');
    const auto slash = target.find_last_of(L"\\/");
    if (dot == std::wstring::npos || (slash != std::wstring::npos && dot < slash)) {
        return true;
    }
    static const std::set<std::wstring> blocked = {
        L".bat", L".cmd", L".com", L".cpl", L".dll", L".exe", L".js",
        L".jse", L".lnk", L".msi", L".msix", L".ps1", L".scr", L".sys",
        L".vbs", L".wsf", L".url", L".hta", L".reg", L".msc", L".appx",
        L".appxbundle", L".msixbundle", L".msp", L".wsb", L".website"
    };
    return blocked.contains(Lower(target.substr(dot)));
}

std::string WideToUtf8(const std::wstring& value) {
    if (value.empty()) {
        return {};
    }
    const int length = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value.data(),
                                            static_cast<int>(value.size()), nullptr, 0,
                                            nullptr, nullptr);
    if (length <= 0) {
        return {};
    }
    std::string result(static_cast<std::size_t>(length), '\0');
    WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value.data(),
                        static_cast<int>(value.size()), result.data(), length,
                        nullptr, nullptr);
    return result;
}

std::string JsonEscape(const std::wstring& value) {
    const std::string utf8 = WideToUtf8(value);
    std::ostringstream output;
    for (const unsigned char character : utf8) {
        switch (character) {
        case '"': output << "\\\""; break;
        case '\\': output << "\\\\"; break;
        case '\b': output << "\\b"; break;
        case '\f': output << "\\f"; break;
        case '\n': output << "\\n"; break;
        case '\r': output << "\\r"; break;
        case '\t': output << "\\t"; break;
        default:
            if (character < 0x20) {
                output << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                       << static_cast<int>(character);
            } else {
                output << character;
            }
        }
    }
    return output.str();
}

std::string Timestamp() {
    SYSTEMTIME time{};
    GetSystemTime(&time);
    std::ostringstream output;
    output << std::setfill('0') << std::setw(4) << time.wYear << '-'
           << std::setw(2) << time.wMonth << '-' << std::setw(2) << time.wDay << 'T'
           << std::setw(2) << time.wHour << ':' << std::setw(2) << time.wMinute << ':'
           << std::setw(2) << time.wSecond << '.' << std::setw(3) << time.wMilliseconds << 'Z';
    return output.str();
}

std::string RequestId() {
    GUID id{};
    if (CoCreateGuid(&id) != S_OK) {
        return {};
    }
    wchar_t text[40]{};
    if (StringFromGUID2(id, text, 40) == 0) {
        return {};
    }
    return WideToUtf8(std::wstring(text + 1, 36));
}

bool ForwardRequest(
    HWND owner,
    LPCWSTR verb,
    LPCWSTR target,
    LPCWSTR parameters,
    LPCWSTR directory,
    int show,
    std::uint64_t flags
) {
    const HWND host = FindWindowW(jev_open::kHostWindowClass, jev_open::kHostWindowTitle);
    if (host == nullptr) {
        return false;
    }
    const auto request_id = RequestId();
    if (request_id.empty()) {
        return false;
    }
    const std::wstring ownership = std::wstring(jev_open::kOwnershipPrefix) +
        std::to_wstring(GetCurrentProcessId()) + L"-" +
        std::wstring(request_id.begin(), request_id.end());
    const std::wstring executable = CurrentExecutable();
    const std::wstring verb_value = verb == nullptr || *verb == L'\0' ? L"open" : Lower(verb);
    std::wstring effective_directory = directory == nullptr ? L"" : directory;
    if (effective_directory.empty()) {
        effective_directory.resize(32768);
        const DWORD length = GetCurrentDirectoryW(static_cast<DWORD>(effective_directory.size()),
                                                  effective_directory.data());
        if (length == 0 || length >= effective_directory.size()) {
            return false;
        }
        effective_directory.resize(length);
    }
    std::ostringstream json;
    json << "{\"type\":\"open_request\",\"protocol_version\":2,\"ownership\":\""
         << JsonEscape(ownership) << "\",\"payload\":{"
         << "\"request_id\":\"" << request_id << "\","
         << "\"source_pid\":" << GetCurrentProcessId() << ','
         << "\"source_executable\":\"" << JsonEscape(executable) << "\","
         << "\"foreground_hwnd\":" << reinterpret_cast<std::uintptr_t>(GetForegroundWindow()) << ','
         << "\"verb\":\"" << JsonEscape(verb_value) << "\","
         << "\"target\":\"" << JsonEscape(target == nullptr ? L"" : target) << "\","
         << "\"parameters\":\"" << JsonEscape(parameters == nullptr ? L"" : parameters) << "\","
         << "\"working_directory\":";
    json << "\"" << JsonEscape(effective_directory) << "\",";
    json << "\"show_command\":" << show << ','
         << "\"shell_execute_flags\":" << flags << ','
         << "\"captured_at\":\"" << Timestamp() << "\"}}";
    const std::string payload = json.str();
    if (payload.size() > jev_open::kMaxRequestBytes) {
        return false;
    }
    const HANDLE mapping = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0,
        static_cast<DWORD>(jev_open::kMappingBytes), ownership.c_str());
    if (mapping == nullptr) {
        return false;
    }
    if (GetLastError() == ERROR_ALREADY_EXISTS) {
        CloseHandle(mapping);
        return false;
    }
    auto* state = static_cast<jev_open::OwnershipHeader*>(
        MapViewOfFile(mapping, FILE_MAP_ALL_ACCESS, 0, 0, jev_open::kMappingBytes));
    if (state == nullptr) {
        CloseHandle(mapping);
        return false;
    }
    state->version = jev_open::kProtocolVersion;
    state->source_pid = GetCurrentProcessId();
    state->deadline_ms = GetTickCount64() + jev_open::kAckDeadlineMs;
    state->state = jev_open::kPending;
    state->payload_length = static_cast<DWORD>(payload.size());
    std::memcpy(state + 1, payload.data(), payload.size());
    COPYDATASTRUCT copy{};
    copy.dwData = jev_open::kCopyDataOpenRequest;
    copy.cbData = static_cast<DWORD>(payload.size());
    copy.lpData = const_cast<char*>(payload.data());
    DWORD_PTR response = 0;
    SendMessageTimeoutW(
        host,
        WM_COPYDATA,
        reinterpret_cast<WPARAM>(owner),
        reinterpret_cast<LPARAM>(&copy),
        SMTO_ABORTIFHUNG | SMTO_BLOCK | SMTO_ERRORONEXIT,
        jev_open::kAckDeadlineMs,
        &response
    );
    while (state->state == jev_open::kPending && GetTickCount64() < state->deadline_ms) {
        Sleep(1);
    }
    const LONG previous = InterlockedCompareExchange(&state->state,
        jev_open::kCancelled, jev_open::kPending);
    UnmapViewOfFile(state);
    CloseHandle(mapping);
    return previous == jev_open::kAccepted;
}

HINSTANCE WINAPI HookedShellExecuteW(
    HWND owner,
    LPCWSTR verb,
    LPCWSTR file,
    LPCWSTR parameters,
    LPCWSTR directory,
    INT show
) {
    if (g_inside_hook || IsExcludedProcess() || !IsEligibleVerb(verb) ||
        file == nullptr || (parameters != nullptr && *parameters != L'\0') ||
        IsDangerousTarget(file) ||
        (IsUri(file) && verb != nullptr && Lower(verb) == L"edit")) {
        return g_shell_execute_w(owner, verb, file, parameters, directory, show);
    }
    ReentryGuard guard;
    if (ForwardRequest(owner, verb, file, parameters, directory, show, 0)) {
        return reinterpret_cast<HINSTANCE>(static_cast<INT_PTR>(33));
    }
    return g_shell_execute_w(owner, verb, file, parameters, directory, show);
}

BOOL WINAPI HookedShellExecuteExW(SHELLEXECUTEINFOW* info) {
    if (info == nullptr || info->cbSize != sizeof(SHELLEXECUTEINFOW) ||
        g_inside_hook || IsExcludedProcess() ||
        !IsEligibleVerb(info->lpVerb) || info->lpFile == nullptr ||
        (info->lpParameters != nullptr && *info->lpParameters != L'\0') ||
        IsDangerousTarget(info->lpFile) ||
        (IsUri(info->lpFile) && info->lpVerb != nullptr && Lower(info->lpVerb) == L"edit")) {
        return g_shell_execute_ex_w(info);
    }
    // Electron/Chromium callers commonly add NOASYNC to keep ShellExecuteExW
    // on the calling thread. It does not request an output process handle, so
    // an accepted broker handoff can preserve the caller-visible contract.
    constexpr ULONG supported_flags = SEE_MASK_FLAG_NO_UI | SEE_MASK_NOASYNC;
    if ((info->fMask & ~supported_flags) != 0) {
        return g_shell_execute_ex_w(info);
    }
    ReentryGuard guard;
    if (ForwardRequest(
            info->hwnd,
            info->lpVerb,
            info->lpFile,
            info->lpParameters,
            info->lpDirectory,
            info->nShow,
            info->fMask)) {
        info->hInstApp = reinterpret_cast<HINSTANCE>(static_cast<INT_PTR>(33));
        info->hProcess = nullptr;
        return TRUE;
    }
    return g_shell_execute_ex_w(info);
}

bool AttachHooks() {
    if (DetourTransactionBegin() != NO_ERROR) {
        return false;
    }
    if (DetourUpdateThread(GetCurrentThread()) != NO_ERROR ||
        DetourAttach(reinterpret_cast<PVOID*>(&g_shell_execute_w),
                     reinterpret_cast<PVOID>(HookedShellExecuteW)) != NO_ERROR ||
        DetourAttach(reinterpret_cast<PVOID*>(&g_shell_execute_ex_w),
                     reinterpret_cast<PVOID>(HookedShellExecuteExW)) != NO_ERROR) {
        DetourTransactionAbort();
        return false;
    }
    return DetourTransactionCommit() == NO_ERROR;
}

void DetachHooks() {
    if (DetourTransactionBegin() != NO_ERROR) {
        return;
    }
    DetourUpdateThread(GetCurrentThread());
    DetourDetach(reinterpret_cast<PVOID*>(&g_shell_execute_w),
                 reinterpret_cast<PVOID>(HookedShellExecuteW));
    DetourDetach(reinterpret_cast<PVOID*>(&g_shell_execute_ex_w),
                 reinterpret_cast<PVOID>(HookedShellExecuteExW));
    DetourTransactionCommit();
}

}  // namespace

extern "C" __declspec(dllexport) LRESULT CALLBACK HookProc(
    int code,
    WPARAM w_param,
    LPARAM l_param
) {
    return CallNextHookEx(nullptr, code, w_param, l_param);
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID reserved) {
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        (void)instance;
        if (IsExcludedProcess()) {
            return TRUE;
        }
        DetourRestoreAfterWith();
        return AttachHooks() ? TRUE : FALSE;
    }
    if (reason == DLL_PROCESS_DETACH && reserved == nullptr && !IsExcludedProcess()) {
        DetachHooks();
    }
    (void)reserved;
    return TRUE;
}
