from __future__ import annotations

from jev_open.domain import ContextEnvelope, OpenDecision


class QtUserInterfaceModule:
    def run(self) -> int:
        # TODO: Create QApplication, tray menu, onboarding, and pending window.
        raise NotImplementedError("TODO: run Qt interface")

    async def enqueue(self, context: ContextEnvelope, decision: OpenDecision) -> None:
        # TODO: Aggregate for 500ms and coalesce identical pending requests.
        # TODO: Show independent Scene and Open Action corrections.
        # TODO: Offer explicit preference scope after the user selects Remember.
        raise NotImplementedError("TODO: enqueue confirmation row")

    async def show_recovery_report(self) -> None:
        # TODO: Explain the prior crash, current hook state, and required self-tests.
        raise NotImplementedError("TODO: show recovery report")
