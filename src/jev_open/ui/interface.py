from __future__ import annotations

from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from typing import Any, Protocol

from jev_open.domain import ConfirmationResult, ContextEnvelope, OpenDecision


class UserInterfaceModule(Protocol):
    """Present onboarding, pending decisions, corrections, and recovery state."""

    def run(self) -> int: ...

    def bind(
        self, *, submit: Callable[[Coroutine[Any, Any, Any]], Future[Any]],
        broker: Any, state: Any, ready_check: Callable[[], bool | None] | None = None,
    ) -> None: ...

    def cancel_pending(self) -> None: ...

    async def enqueue(
        self, context: ContextEnvelope, decision: OpenDecision
    ) -> ConfirmationResult: ...

    async def show_recovery_report(self) -> None: ...
