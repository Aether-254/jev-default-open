from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from jev_open.domain import ChatContext, OpenRequest

from .validation import validated_chat


class VerifiedVisibleContextReader(Protocol):
    """Version-specific reader, restricted to a proven message-list subtree.

    A concrete reader must verify HWND ownership against source_pid, resolve an
    explicit account/conversation/message identity, and stop at its deadline.
    Generic top-level window traversal is intentionally not a supported reader.
    """

    async def read(self, request: OpenRequest, deadline: datetime) -> ChatContext | None: ...


async def visible_text_context(
    request: OpenRequest,
    *,
    provider: str,
    deadline: datetime | None = None,
    reader: VerifiedVisibleContextReader | None = None,
    max_messages: int = 10,
) -> ChatContext | None:
    if reader is None or not request.foreground_hwnd or deadline is None:
        return None
    if datetime.now(UTC) >= deadline or max_messages < 1:
        return None
    context = validated_chat(await reader.read(request, deadline), request)
    if context is None or context.provider != provider or len(context.messages) > max_messages:
        return None
    return context
