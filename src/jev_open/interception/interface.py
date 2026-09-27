from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from jev_open.domain import OpenRequest

RequestHandler = Callable[[OpenRequest], Awaitable[None]]


class InterceptionModule(Protocol):
    """Own the native hook lifecycle and deliver validated requests."""

    async def start(self, handler: RequestHandler) -> None: ...

    async def stop(self) -> None: ...

    async def emergency_stop(self) -> None: ...

    async def fallback(self, request: OpenRequest) -> None: ...

    def is_active(self) -> bool: ...
