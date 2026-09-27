from __future__ import annotations

from datetime import datetime
from typing import Protocol

from jev_open.domain import ChatContext, ContextEnvelope, OpenRequest


class ChatContextProvider(Protocol):
    """Adapter interface for one IM platform."""

    provider_id: str

    def supports(self, request: OpenRequest) -> bool: ...

    async def resolve(self, request: OpenRequest, deadline: datetime) -> ChatContext | None: ...


class ContextModule(Protocol):
    """Assemble all non-action evidence for an intercepted request.

    The implementation hides provider selection, target normalization, chat
    correlation, path labels, provenance lookup, validation, and time budgets.
    """

    async def assemble(self, request: OpenRequest, deadline: datetime) -> ContextEnvelope: ...
