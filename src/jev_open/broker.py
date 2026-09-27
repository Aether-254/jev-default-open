from __future__ import annotations

from dataclasses import dataclass

from .actions.interface import OpenActionModule
from .context.interface import ContextModule
from .decision.interface import DecisionModule
from .interception.interface import InterceptionModule
from .persistence.interface import StateModule
from .ui.interface import UserInterfaceModule


@dataclass(slots=True)
class BrokerApplication:
    """Top-level coordinator with no platform-specific implementation details."""

    interception: InterceptionModule
    context: ContextModule
    actions: OpenActionModule
    decision: DecisionModule
    state: StateModule
    ui: UserInterfaceModule

    def run(self) -> int:
        """Run the broker lifecycle.

        # TODO: Validate onboarding state before enabling interception.
        # TODO: Start IPC, native host, provider bridges, and the Qt event loop.
        # TODO: Ensure stop_hook executes on normal and exceptional shutdown.
        """
        raise NotImplementedError("TODO: run broker lifecycle")
