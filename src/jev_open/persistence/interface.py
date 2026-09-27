from __future__ import annotations

from typing import Protocol

from jev_open.domain import ContextEnvelope, OpenDecision, PreferenceRule


class StateModule(Protocol):
    """Persist encrypted state without exposing storage details to callers."""

    async def find_preference(self, context: ContextEnvelope) -> PreferenceRule | None: ...

    async def save_preference(self, rule: PreferenceRule) -> None: ...

    async def record_decision(
        self, context: ContextEnvelope, decision: OpenDecision, final_action_id: str | None
    ) -> None: ...

    async def clear_all(self) -> None: ...
