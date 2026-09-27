from __future__ import annotations

from datetime import datetime

from jev_open.domain import ContextEnvelope, OpenDecision


class JevDecisionModule:
    """Build typed Jev questions and apply local decision policy."""

    def __init__(self, *, model: str, api_key: str, base_url: str) -> None:
        self._model = model
        self._api_key = api_key
        self._base_url = base_url

    async def decide(self, context: ContextEnvelope, deadline: datetime) -> OpenDecision:
        # TODO: Check exact-context cache before making a network request.
        # TODO: Build independent scene and open_action Choice questions.
        # TODO: Omit open_action when only one compatible action exists.
        # TODO: Enforce 0.70 top probability and 0.15 margin UI policy.
        # TODO: Handle 401/422 without retry and bounded 429/529 retry.
        raise NotImplementedError("TODO: evaluate ContextEnvelope with Jev")
