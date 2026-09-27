from __future__ import annotations

from typing import Protocol

from jev_open.domain import ContextEnvelope, OpenDecision


class UserInterfaceModule(Protocol):
    """Present onboarding, pending decisions, corrections, and recovery state."""

    def run(self) -> int: ...

    async def enqueue(self, context: ContextEnvelope, decision: OpenDecision) -> None: ...

    async def show_recovery_report(self) -> None: ...
