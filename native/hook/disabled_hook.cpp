#include "protocol.hpp"

// Default builds deliberately export no interception implementation.
extern "C" __declspec(dllexport) LRESULT CALLBACK HookProc(
    int code, WPARAM w_param, LPARAM l_param) {
    return CallNextHookEx(nullptr, code, w_param, l_param);
}

extern "C" __declspec(dllexport) BOOL WINAPI JevHookIsEnabled() {
    return FALSE;
}
