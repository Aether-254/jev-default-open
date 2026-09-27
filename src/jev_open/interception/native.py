from __future__ import annotations

from .interface import RequestHandler


class NativeInterceptionModule:
    def __init__(self) -> None:
        self._active = False

    async def start(self, handler: RequestHandler) -> None:
        # TODO: Start the session-scoped named pipe before installing hooks.
        # TODO: Spawn native_host.exe and complete a signed capability handshake.
        # TODO: Register Ctrl+Alt+Shift+F12 with the native host.
        # TODO: ACK only after a request is decoded, validated, and accepted.
        raise NotImplementedError("TODO: start native interception")

    async def stop(self) -> None:
        # TODO: Ask native host to unhook and verify completion.
        raise NotImplementedError("TODO: stop native interception")

    async def emergency_stop(self) -> None:
        # TODO: Use the independent emergency control channel.
        raise NotImplementedError("TODO: emergency stop")

    def is_active(self) -> bool:
        return self._active
