#include "protocol.hpp"

#include <nlohmann/json.hpp>
#include <shellapi.h>

#include <atomic>
#include <array>
#include <condition_variable>
#include <cstring>
#include <deque>
#include <filesystem>
#include <iostream>
#include <mutex>
#include <memory>
#include <string>
#include <thread>

namespace {

constexpr UINT kCommand = WM_APP + 1;
constexpr UINT kExitHost = WM_APP + 2;
HHOOK g_hook = nullptr;
HMODULE g_hook_module = nullptr;
std::atomic<bool> g_running{true};
std::atomic<bool> g_output_done{false};
std::atomic<bool> g_reader_done{false};
std::atomic<bool> g_abandon_output{false};
std::atomic<HANDLE> g_output_thread{nullptr};
std::atomic<HANDLE> g_reader_thread{nullptr};
std::mutex g_output_mutex;
std::condition_variable g_output_ready;
std::deque<nlohmann::json> g_output;
std::mutex g_command_mutex;
std::deque<nlohmann::json> g_commands;
bool g_test_mode = false;

class CompletionGuard {
public:
    CompletionGuard(std::atomic<bool>& done, std::atomic<HANDLE>& thread) : done_(done) {
        HANDLE handle = nullptr;
        ready_ = DuplicateHandle(GetCurrentProcess(), GetCurrentThread(), GetCurrentProcess(),
                                 &handle, 0, FALSE, DUPLICATE_SAME_ACCESS) != FALSE;
        thread = handle;
    }
    ~CompletionGuard() { done_ = true; }
    CompletionGuard(const CompletionGuard&) = delete;
    CompletionGuard& operator=(const CompletionGuard&) = delete;
    bool ready() const { return ready_; }

private:
    std::atomic<bool>& done_;
    bool ready_ = false;
};

bool WriteMessage(nlohmann::json message) {
    std::lock_guard lock(g_output_mutex);
    if (!g_running || g_output.size() >= 64) {
        return false;
    }
    g_output.push_back(std::move(message));
    g_output_ready.notify_one();
    return true;
}

void OutputWriter(HWND window) {
    CompletionGuard completed(g_output_done, g_output_thread);
    if (!completed.ready()) {
        PostMessageW(window, kExitHost, 0, 0);
        return;
    }
    try {
        for (;;) {
            nlohmann::json message;
            {
                std::unique_lock lock(g_output_mutex);
                g_output_ready.wait(lock, [] { return !g_running || !g_output.empty(); });
                if (g_abandon_output || g_output.empty()) {
                    return;
                }
                message = std::move(g_output.front());
                g_output.pop_front();
            }
            if (g_abandon_output) {
                return;
            }
            const auto line = message.dump() + '\n';
            std::size_t offset = 0;
            while (offset < line.size()) {
                DWORD written = 0;
                if (g_abandon_output || !WriteFile(GetStdHandle(STD_OUTPUT_HANDLE),
                    line.data() + offset, static_cast<DWORD>(line.size() - offset),
                    &written, nullptr) || written == 0) {
                    PostMessageW(window, kExitHost, 0, 0);
                    return;
                }
                offset += written;
            }
        }
    } catch (const std::exception&) {
    }
    PostMessageW(window, kExitHost, 0, 0);
}

void JoinSynchronousWorker(std::thread& worker, const std::atomic<bool>& done,
                           const std::atomic<HANDLE>& thread) {
    // A single cancellation can race with the worker entering its next pipe I/O.
    while (!done) {
        if (const HANDLE handle = thread.load(); handle != nullptr) {
            CancelSynchronousIo(handle);
        }
        Sleep(5);
    }
    worker.join();
    if (const HANDLE handle = thread.load(); handle != nullptr) {
        CloseHandle(handle);
    }
}

std::filesystem::path HookDllPath() {
    std::wstring buffer(32768, L'\0');
    const DWORD length = GetModuleFileNameW(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
    if (length == 0 || length >= buffer.size()) {
        return {};
    }
    buffer.resize(length);
    return std::filesystem::path(buffer).parent_path() / L"open_hook.dll";
}

void StopHook() {
    if (g_hook != nullptr) {
        UnhookWindowsHookEx(g_hook);
        g_hook = nullptr;
    }
    if (g_hook_module != nullptr) {
        FreeLibrary(g_hook_module);
        g_hook_module = nullptr;
    }
}

bool StartHook() {
    if (g_hook != nullptr) {
        return true;
    }
    if (g_test_mode || !JEV_ENABLE_EXPERIMENTAL_HOOK) {
        SetLastError(ERROR_NOT_SUPPORTED);
        return false;
    }
    const auto dll_path = HookDllPath();
    g_hook_module = LoadLibraryExW(dll_path.c_str(), nullptr, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR |
                                                           LOAD_LIBRARY_SEARCH_SYSTEM32);
    if (g_hook_module == nullptr) {
        return false;
    }
    const auto hook_proc = reinterpret_cast<HOOKPROC>(GetProcAddress(g_hook_module, "HookProc"));
    if (hook_proc == nullptr) {
        StopHook();
        return false;
    }
    g_hook = SetWindowsHookExW(WH_GETMESSAGE, hook_proc, g_hook_module, 0);
    if (g_hook == nullptr) {
        const DWORD error = GetLastError();
        StopHook();
        SetLastError(error);
        return false;
    }
    return true;
}

std::wstring Utf8ToWide(const std::string& value) {
    if (value.empty()) {
        return {};
    }
    const int length = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value.data(),
                                          static_cast<int>(value.size()), nullptr, 0);
    if (length <= 0) {
        throw std::invalid_argument("Invalid UTF-8");
    }
    std::wstring result(static_cast<std::size_t>(length), L'\0');
    MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value.data(),
                        static_cast<int>(value.size()), result.data(), length);
    return result;
}

bool ValidateSource(const nlohmann::json& payload, DWORD expected_pid) {
    if (payload.value("source_pid", DWORD{0}) != expected_pid) {
        return false;
    }
    DWORD source_session = 0;
    DWORD host_session = 0;
    if (!ProcessIdToSessionId(expected_pid, &source_session) ||
        !ProcessIdToSessionId(GetCurrentProcessId(), &host_session) ||
        source_session != host_session) {
        return false;
    }
    const HANDLE process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, expected_pid);
    if (process == nullptr) {
        return false;
    }
    std::wstring path(32768, L'\0');
    DWORD length = static_cast<DWORD>(path.size());
    const BOOL queried = QueryFullProcessImageNameW(process, 0, path.data(), &length);
    CloseHandle(process);
    if (!queried) {
        return false;
    }
    path.resize(length);
    const auto claimed = Utf8ToWide(payload.value("source_executable", ""));
    return _wcsicmp(path.c_str(), claimed.c_str()) == 0;
}

bool ReceiveOffer(const COPYDATASTRUCT* copy) {
    if (g_hook == nullptr || copy == nullptr ||
        copy->dwData != jev_open::kCopyDataOpenRequest || copy->lpData == nullptr ||
        copy->cbData == 0 || copy->cbData > jev_open::kMaxRequestBytes) {
        return false;
    }
    try {
        auto message = nlohmann::json::parse(
            static_cast<const char*>(copy->lpData),
            static_cast<const char*>(copy->lpData) + copy->cbData);
        if (message.value("type", "") != "open_request" ||
            message.value("protocol_version", 0U) != jev_open::kProtocolVersion) {
            return false;
        }
        const auto name = Utf8ToWide(message.at("ownership").get<std::string>());
        if (name.size() > 128 || name.rfind(jev_open::kOwnershipPrefix, 0) != 0) {
            return false;
        }
        const HANDLE mapping = OpenFileMappingW(FILE_MAP_READ, FALSE, name.c_str());
        if (mapping == nullptr) {
            return false;
        }
        const auto* header = static_cast<const jev_open::OwnershipHeader*>(
            MapViewOfFile(mapping, FILE_MAP_READ, 0, 0, jev_open::kMappingBytes));
        bool valid = false;
        if (header != nullptr) {
            const auto now = GetTickCount64();
            valid = header->version == jev_open::kProtocolVersion &&
                    header->state == jev_open::kPending &&
                    now < header->deadline_ms &&
                    header->deadline_ms - now <= 1000 &&
                    header->payload_length == copy->cbData &&
                    std::memcmp(header + 1, copy->lpData, copy->cbData) == 0 &&
                    ValidateSource(message.at("payload"), header->source_pid);
            UnmapViewOfFile(header);
        }
        CloseHandle(mapping);
        // This only acknowledges transport. The broker owns nothing until its CAS.
        if (!valid) {
            return false;
        }
        message["transport_payload"] = std::string(
            static_cast<const char*>(copy->lpData), copy->cbData);
        return WriteMessage(std::move(message));
    } catch (const std::exception&) {
        return false;
    }
}

void HandleCommand(const nlohmann::json& command) {
    const auto id = command.value("command_id", "");
    const auto name = command.value("command", "");
    nlohmann::json reply{{"type", "command_result"}, {"command_id", id},
                         {"command", name}, {"success", true}};
    if (name == "start_hook") {
        reply["success"] = StartHook();
        reply["hook_active"] = g_hook != nullptr;
        if (!reply["success"].get<bool>()) {
            reply["win32"] = GetLastError();
        }
    } else if (name == "stop_hook" || name == "emergency_stop") {
        StopHook();
        reply["hook_active"] = false;
    } else if (name == "status") {
        reply["hook_active"] = g_hook != nullptr;
        reply["experimental_hook_available"] = JEV_ENABLE_EXPERIMENTAL_HOOK != 0;
    } else if (name == "exit") {
        StopHook();
        reply["hook_active"] = false;
        WriteMessage(std::move(reply));
        PostQuitMessage(0);
        return;
    } else {
        reply["success"] = false;
        reply["error"] = "unsupported_command";
    }
    WriteMessage(std::move(reply));
}

LRESULT CALLBACK WindowProc(HWND window, UINT message, WPARAM w_param, LPARAM l_param) {
    if (message == WM_COPYDATA) {
        return ReceiveOffer(reinterpret_cast<const COPYDATASTRUCT*>(l_param)) ? TRUE : FALSE;
    }
    if (message == kCommand) {
        nlohmann::json command;
        {
            std::lock_guard lock(g_command_mutex);
            if (g_commands.empty()) {
                return 0;
            }
            command = std::move(g_commands.front());
            g_commands.pop_front();
        }
        try {
            HandleCommand(command);
        } catch (const std::exception&) {
            WriteMessage({{"type", "error"}, {"operation", "command"}});
        }
        return 0;
    }
    if (message == WM_HOTKEY && w_param == jev_open::kEmergencyHotkeyId) {
        StopHook();
        WriteMessage({{"type", "status"}, {"hook_active", false}, {"reason", "emergency_hotkey"}});
        return 0;
    }
    if (message == kExitHost) {
        StopHook();
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(window, message, w_param, l_param);
}

void CommandReader(HWND window) {
    CompletionGuard completed(g_reader_done, g_reader_thread);
    if (!completed.ready()) {
        PostMessageW(window, kExitHost, 0, 0);
        return;
    }
    std::array<char, 4096> input{};
    std::string line;
    while (g_running) {
        DWORD count = 0;
        if (!ReadFile(GetStdHandle(STD_INPUT_HANDLE), input.data(),
            static_cast<DWORD>(input.size()), &count, nullptr) || count == 0) {
            break;
        }
        for (DWORD index = 0; index < count && g_running; ++index) {
            if (input[index] != '\n') {
                if (line.size() >= jev_open::kMaxRequestBytes) {
                    PostMessageW(window, kExitHost, 0, 0);
                    return;
                }
                line += input[index];
                continue;
            }
            try {
                auto command = nlohmann::json::parse(line);
                line.clear();
                if (!command.is_object() || !command.contains("command_id")) {
                    continue;
                }
                {
                    std::lock_guard lock(g_command_mutex);
                    if (g_commands.size() >= 64) {
                        PostMessageW(window, kExitHost, 0, 0);
                        return;
                    }
                    g_commands.push_back(std::move(command));
                }
                if (!PostMessageW(window, kCommand, 0, 0)) {
                    return;
                }
            } catch (const std::exception&) {
                line.clear();
                WriteMessage({{"type", "error"}, {"operation", "parse_command"}});
            }
        }
    }
    PostMessageW(window, kExitHost, 0, 0);
}

}  // namespace

int main(int argc, char** argv) {
    std::string nonce;
    bool self_test = false;
    for (int i = 1; i < argc; ++i) {
        if (std::string(argv[i]) == "--nonce" && i + 1 < argc) {
            nonce = argv[++i];
        } else if (std::string(argv[i]) == "--test-mode") {
            g_test_mode = true;
        } else if (std::string(argv[i]) == "--self-test") {
            self_test = true;
        }
    }
    if (self_test) {
        const auto hook_path = HookDllPath();
        std::error_code hook_error;
        std::error_code helper_error;
        const bool hook_present = !hook_path.empty() &&
            std::filesystem::is_regular_file(hook_path, hook_error);
        const bool helper_present = !hook_path.empty() && std::filesystem::is_regular_file(
            hook_path.parent_path() / L"native_claim.dll", helper_error);
        std::cout << nlohmann::json{
            {"protocol_version", jev_open::kProtocolVersion},
            {"experimental_hook_enabled", JEV_ENABLE_EXPERIMENTAL_HOOK != 0},
            {"test_mode_supported", true},
            {"ownership_helper", helper_present},
            {"hook_dll", hook_present}
        }.dump() << std::endl;
        return std::cout ? 0 : ERROR_WRITE_FAULT;
    }
    if (nonce.size() < 16 || nonce.size() > 128) {
        return ERROR_INVALID_PARAMETER;
    }
    if (FindWindowW(jev_open::kHostWindowClass, jev_open::kHostWindowTitle) != nullptr) {
        return ERROR_ALREADY_EXISTS;
    }
    WNDCLASSW window_class{};
    window_class.lpfnWndProc = WindowProc;
    window_class.hInstance = GetModuleHandleW(nullptr);
    window_class.lpszClassName = jev_open::kHostWindowClass;
    if (RegisterClassW(&window_class) == 0) {
        return static_cast<int>(GetLastError());
    }
    const HWND window = CreateWindowExW(0, jev_open::kHostWindowClass,
        jev_open::kHostWindowTitle, 0, 0, 0, 0, 0, nullptr, nullptr,
        window_class.hInstance, nullptr);
    if (window == nullptr) {
        return static_cast<int>(GetLastError());
    }
    if (!g_test_mode && !RegisterHotKey(window, jev_open::kEmergencyHotkeyId,
        MOD_CONTROL | MOD_ALT | MOD_SHIFT | MOD_NOREPEAT, VK_F12)) {
        DestroyWindow(window);
        return static_cast<int>(GetLastError());
    }
    std::thread writer(OutputWriter, window);
    WriteMessage({{"type", "hello"}, {"protocol_version", jev_open::kProtocolVersion},
                  {"process_id", GetCurrentProcessId()}, {"nonce", nonce},
                  {"test_mode", g_test_mode}});
    std::thread reader(CommandReader, window);
    MSG message{};
    while (GetMessageW(&message, nullptr, 0, 0) > 0) {
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }
    StopHook();
    g_running = false;
    g_output_ready.notify_all();
    JoinSynchronousWorker(reader, g_reader_done, g_reader_thread);
    const auto drain_deadline = GetTickCount64() + 100;
    while (!g_output_done && GetTickCount64() < drain_deadline) {
        Sleep(5);
    }
    g_abandon_output = true;
    g_output_ready.notify_all();
    JoinSynchronousWorker(writer, g_output_done, g_output_thread);
    if (!g_test_mode) {
        UnregisterHotKey(window, jev_open::kEmergencyHotkeyId);
    }
    DestroyWindow(window);
    return 0;
}
