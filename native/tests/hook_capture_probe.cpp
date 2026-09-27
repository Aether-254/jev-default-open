#include <windows.h>
#include <shellapi.h>

#include <iostream>

namespace {

constexpr wchar_t kWindowClass[] = L"JevHookCaptureProbe";
constexpr UINT_PTR kTimer = 1;

LRESULT CALLBACK WindowProc(HWND window, UINT message, WPARAM w_param, LPARAM l_param) {
    if (message == WM_CREATE) {
        SetTimer(window, kTimer, 500, nullptr);
        return 0;
    }
    if (message == WM_TIMER && w_param == kTimer) {
        KillTimer(window, kTimer);
        if (wcsstr(GetCommandLineW(), L"--ex-noasync") != nullptr) {
            SHELLEXECUTEINFOW info{};
            info.cbSize = sizeof(info);
            info.fMask = SEE_MASK_FLAG_NO_UI | SEE_MASK_NOASYNC;
            info.hwnd = window;
            info.lpVerb = L"open";
            info.lpFile = L"C:\\jev-hook-capture-probe-does-not-exist.txt";
            info.nShow = SW_SHOWNORMAL;
            const BOOL result = ShellExecuteExW(&info);
            std::cout << "SHELLEXECUTE_EX_RESULT " << result << ' '
                      << reinterpret_cast<INT_PTR>(info.hInstApp) << std::endl;
        } else {
            const auto result = ShellExecuteW(
                window,
                L"open",
                L"C:\\jev-hook-capture-probe-does-not-exist.txt",
                nullptr,
                nullptr,
                SW_SHOWNORMAL);
            std::cout << "SHELLEXECUTE_RESULT "
                      << reinterpret_cast<INT_PTR>(result) << std::endl;
        }
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(window, message, w_param, l_param);
}

}  // namespace

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR, int) {
    WNDCLASSW window_class{};
    window_class.lpfnWndProc = WindowProc;
    window_class.hInstance = instance;
    window_class.lpszClassName = kWindowClass;
    if (!RegisterClassW(&window_class)) {
        return static_cast<int>(GetLastError());
    }
    if (!CreateWindowExW(
            0, kWindowClass, kWindowClass, 0, 0, 0, 0, 0,
            nullptr, nullptr, instance, nullptr)) {
        return static_cast<int>(GetLastError());
    }
    MSG message{};
    while (GetMessageW(&message, nullptr, 0, 0) > 0) {
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }
    return 0;
}
