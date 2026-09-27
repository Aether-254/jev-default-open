from __future__ import annotations

from typing import Protocol

from jev_open.domain import ContextEnvelope, OpenAction, OpenTarget


class OpenActionModule(Protocol):
    """Discover compatible actions and invoke exactly one selected action."""

    async def discover(self, target: OpenTarget) -> tuple[OpenAction, ...]: ...

    async def launch(self, action: OpenAction, context: ContextEnvelope) -> None: ...
