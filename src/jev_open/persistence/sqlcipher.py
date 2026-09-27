from __future__ import annotations

from jev_open.domain import ContextEnvelope, OpenDecision, PreferenceRule


class SQLCipherStateModule:
    """SQLCipher storage with a DPAPI-protected per-user key."""

    async def find_preference(self, context: ContextEnvelope) -> PreferenceRule | None:
        # TODO: Match rules from most specific scope to global scope.
        raise NotImplementedError("TODO: find preference")

    async def save_preference(self, rule: PreferenceRule) -> None:
        # TODO: Upsert the rule within the Windows-user and IM-account scope.
        raise NotImplementedError("TODO: save preference")

    async def record_decision(
        self, context: ContextEnvelope, decision: OpenDecision, final_action_id: str | None
    ) -> None:
        # TODO: Store the full encrypted audit record.
        # TODO: Trim history to the newest 1000 rows in the same transaction.
        raise NotImplementedError("TODO: record decision")

    async def clear_all(self) -> None:
        # TODO: Close handles, erase the DB and DPAPI key blob, then recreate state.
        raise NotImplementedError("TODO: clear encrypted state")
