from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from jev_open.domain import ChatContext, ContextEnvelope, FileTarget, OpenDecision, PreferenceRule


class StateModule(Protocol):
    """Persist encrypted state without exposing storage details to callers."""

    async def find_preference(self, context: ContextEnvelope) -> PreferenceRule | None: ...

    async def save_preference(self, rule: PreferenceRule) -> None: ...

    async def record_decision(
        self,
        context: ContextEnvelope,
        decision: OpenDecision,
        final_action_id: str | None,
        *,
        final_scene_id: str | None = None,
        status: str | None = None,
    ) -> None: ...

    async def list_history(
        self,
        *,
        limit: int = 1000,
        offset: int = 0,
        query: str | None = None,
    ) -> list[dict[str, Any]]: ...

    async def export_history(self, path: Path, *, confirmed: bool = False) -> int: ...

    async def clear_history(self) -> None: ...

    async def list_preferences(self) -> list[PreferenceRule]: ...

    async def delete_preference(self, rule_id: str) -> None: ...

    async def get_setting(self, name: str, default: Any = None) -> Any: ...

    async def set_setting(self, name: str, value: Any) -> None: ...

    async def get_exact_cache(
        self,
        context: ContextEnvelope,
        *,
        model: str,
        configuration: dict[str, Any] | None = None,
    ) -> OpenDecision | None: ...

    async def set_exact_cache(
        self,
        context: ContextEnvelope,
        decision: OpenDecision,
        *,
        model: str,
        ttl_seconds: int = 86400,
        configuration: dict[str, Any] | None = None,
    ) -> None: ...

    async def clear_all(self) -> None: ...

    async def prune_provenance(self, *, now: datetime | None = None) -> int: ...

    async def remember_provenance(self, target: FileTarget, chat: ChatContext) -> None: ...

    async def restore_provenance(
        self, target: FileTarget, enabled_providers: set[str],
    ) -> ChatContext | None: ...

    async def clear_provider_data(self, provider: str) -> None: ...
