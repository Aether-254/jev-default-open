from __future__ import annotations

import ntpath
import re

from jev_open.domain import ChatContext, ChatMessage, OpenRequest

_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def target_matches_message(target: str, message: ChatMessage) -> bool:
    """Require a full target reference, never a basename or substring match."""
    is_drive = len(target) >= 2 and target[0].isalpha() and target[1] == ":"
    if _URI_SCHEME.match(target) and not is_drive:
        return message.url == target
    if message.attachment_path is None:
        return False
    return ntpath.normcase(ntpath.normpath(str(message.attachment_path))) == ntpath.normcase(
        ntpath.normpath(target)
    )


def _readable(value: str | None, limit: int) -> bool:
    if value is None:
        return True
    return len(value) <= limit and not any(
        (ord(char) < 32 and char not in "\r\n\t") or char == "\ufffd" for char in value
    )


def validated_chat(context: ChatContext | None, request: OpenRequest) -> ChatContext | None:
    """The external seam rejects ambiguous, unbounded, or unanchored contexts."""
    if context is None or not context.account_id_hash or not context.conversation_id:
        return None
    if not context.target_message_id or not 1 <= len(context.messages) <= 10:
        return None
    if not _readable(context.conversation_title, 256):
        return None
    seen: set[str] = set()
    anchor = None
    for message in context.messages:
        if not message.message_id or message.message_id in seen:
            return None
        seen.add(message.message_id)
        if not _readable(message.text, 2_000) or not _readable(message.sender, 256):
            return None
        if message.timestamp is not None and message.timestamp.tzinfo is None:
            return None
        if message.message_id == context.target_message_id:
            anchor = message
    if anchor is None or not target_matches_message(request.target, anchor):
        return None
    return context
