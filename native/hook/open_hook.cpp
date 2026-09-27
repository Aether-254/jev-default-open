#include "protocol.hpp"

#include <shellapi.h>
#include <windows.h>

namespace {

// TODO: Store Detours trampolines for ShellExecuteW and ShellExecuteExW.
// TODO: Keep DllMain minimal and perform safe attach/detach transactions.
// TODO: Filter verbs, dangerous targets, system URIs, and synchronous flags.
// TODO: Send a bounded IPC request and wait no more than 50ms for ACK.
// TODO: Call the original API on every parsing, IPC, timeout, or broker error.
// TODO: Return a valid success shape only after the broker accepts ownership.

}  // namespace

extern "C" __declspec(dllexport) LRESULT CALLBACK HookProc(
    int code,
    WPARAM w_param,
    LPARAM l_param
) {
    return CallNextHookEx(nullptr, code, w_param, l_param);
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID reserved) {
    (void)instance;
    (void)reason;
    (void)reserved;
    return TRUE;
}
