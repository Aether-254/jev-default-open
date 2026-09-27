from __future__ import annotations

from datetime import datetime
from typing import Protocol

from jev_open.domain import ContextEnvelope, OpenDecision


class DecisionModule(Protocol):
    """Decide Scene and Open Action for one fully assembled context.

    Invariants:
    - Never exceed the caller-provided deadline.
    - Return only action IDs present in the ContextEnvelope.
    - Skip open_action judgment when fewer than two actions exist.
    - Never substitute a fuzzy cache hit for an unavailable Jev request.
    """

    async def decide(self, context: ContextEnvelope, deadline: datetime) -> OpenDecision: ...
