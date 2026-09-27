from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes

SERVER_NAME = "JevDefaultOpen-v1"
PIPE_PATH = rf"\\.\pipe\{SERVER_NAME}"
MAX_MESSAGE_BYTES = 32_768


def forward_open_target(target: str) -> bool:
    if os.name != "nt" or not target or len(target) > 16_384:
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(PIPE_PATH, 0xC0000000, 0, None, 3, 0, None)
    invalid = wintypes.HANDLE(-1).value
    if handle == invalid:
        return False
    try:
        payload = json.dumps({"target": target}, ensure_ascii=False).encode("utf-8") + b"\n"
        if len(payload) > MAX_MESSAGE_BYTES:
            return False
        written = wintypes.DWORD()
        if not kernel32.WriteFile(handle, payload, len(payload), ctypes.byref(written), None):
            return False
        response = ctypes.create_string_buffer(16)
        received = wintypes.DWORD()
        if not kernel32.ReadFile(
            handle, response, len(response), ctypes.byref(received), None
        ):
            return False
        return response.raw[: received.value].startswith(b"OK\n")
    finally:
        kernel32.CloseHandle(handle)
