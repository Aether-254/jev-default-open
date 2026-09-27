"""Bounded OneBot 11 message normalization for a trusted QQ transport.

This module parses already-authenticated OneBot events.  It does not open a
socket, read QQ files, or decide whether a peer is authenticated.  The caller
must supply a HMACed account identifier and a source PID after those checks
have completed in the transport layer.
"""

from __future__ import annotations

import re
from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from jev_open.domain import ChatContext, ChatMessage, OpenRequest

from .validation import target_matches_message, validated_chat

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_ACCOUNT_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class OneBotEventError(ValueError):
    """Raised when an event is not a bounded OneBot message event."""


class _OneBotMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    post_type: str = "message"
    message_type: str
    self_id: int | str
    user_id: int | str
    group_id: int | str | None = None
    message_id: int | str
    time: int | None = Field(default=None, ge=0)
    message: str | list[dict[str, Any]]


def _account_hash(value: str, account_hasher: Callable[[str], str]) -> str:
    result = account_hasher(value)
    if not isinstance(result, str) or _ACCOUNT_HASH_RE.fullmatch(result) is None:
        raise OneBotEventError("The account hasher must return a lowercase 64-character HMAC")
    return result


def _conversation_id(event: _OneBotMessage) -> str:
    if event.message_type == "group":
        if event.group_id is None:
            raise OneBotEventError("Group event has no group_id")
        return f"group:{event.group_id}"
    return f"private:{event.user_id}"


def _timestamp(event: _OneBotMessage, captured_at: datetime | None) -> datetime:
    if captured_at is not None:
        if captured_at.tzinfo is None:
            raise OneBotEventError("captured_at must be timezone-aware")
        return captured_at.astimezone(UTC)
    if event.time is None:
        return datetime.now(UTC)
    return datetime.fromtimestamp(event.time, tz=UTC)


def _safe_text(value: object, limit: int = 2_000) -> str | None:
    if not isinstance(value, str):
        return None
    if len(value) > limit or any(
        (ord(char) < 32 and char not in "\r\n\t") or char == "\ufffd" for char in value
    ):
        return None
    return value


def _segment_message(
    segment: Mapping[str, Any], *, target: str | None = None
) -> tuple[str | None, str | None, Path | None]:
    segment_type = segment.get("type")
    data = segment.get("data")
    if not isinstance(segment_type, str) or not isinstance(data, Mapping):
        return None, None, None
    if segment_type == "text":
        text = _safe_text(data.get("text"))
        url = next((match.group(0) for match in _URL_RE.finditer(text or "")), None)
        if target and target == text.strip():
            url = target if urlsplit(target).scheme else None
        return text, url, None
    if segment_type in {"share", "json", "xml"}:
        url = data.get("url") or data.get("jumpUrl") or data.get("jump_url")
        url = url if isinstance(url, str) and len(url) <= 32_768 else None
        return None, url, None
    if segment_type == "file":
        file_value = data.get("file") or data.get("url") or data.get("path")
        name = data.get("name") or data.get("file_name")
        text = _safe_text(name, 256)
        attachment = None
        if isinstance(file_value, str) and target and file_value == target:
            if len(file_value) <= 32_768 and (
                (len(file_value) >= 2 and file_value[1] == ":") or file_value.startswith("\\\\")
            ):
                attachment = Path(file_value)
        return text, None, attachment
    return None, None, None


def _message_from_event(event: _OneBotMessage, *, target: str | None = None) -> ChatMessage:
    if isinstance(event.message, str):
        text = _safe_text(event.message)
        url = (
            target
            if target and text and text.strip() == target and urlsplit(target).scheme
            else None
        )
        return ChatMessage(
            message_id=str(event.message_id),
            sender=str(event.user_id),
            timestamp=_timestamp(event, None),
            message_type="text",
            text=text,
            url=url,
        )
    texts: list[str] = []
    url = None
    attachment = None
    message_type = "unknown"
    for segment in event.message[:32]:
        if not isinstance(segment, Mapping):
            raise OneBotEventError("Message segments must be objects")
        text_part, url_part, attachment_part = _segment_message(segment, target=target)
        if text_part:
            texts.append(text_part)
            message_type = "text"
        if url_part and url_part == target:
            url = url_part
            message_type = "link"
        if attachment_part is not None:
            attachment = attachment_part
            message_type = "file"
    return ChatMessage(
        message_id=str(event.message_id),
        sender=str(event.user_id),
        timestamp=_timestamp(event, None),
        message_type=message_type,
        text="".join(texts) or None,
        attachment_path=attachment,
        url=url,
    )


class OneBotMessageBuffer:
    """Collect a bounded current conversation window from OneBot events."""

    def __init__(self, account_hasher: Callable[[str], str], *, max_messages: int = 10) -> None:
        if not 1 <= max_messages <= 10:
            raise ValueError("max_messages must be between one and ten")
        self._account_hasher = account_hasher
        self._max_messages = max_messages
        self._events: dict[
            tuple[int, str, str], deque[tuple[_OneBotMessage, ChatMessage, datetime]]
        ] = defaultdict(lambda: deque(maxlen=max_messages))

    def ingest(
        self,
        payload: Mapping[str, Any],
        *,
        source_pid: int,
        captured_at: datetime | None = None,
    ) -> str:
        if source_pid <= 0:
            raise OneBotEventError("source_pid must be positive")
        try:
            event = _OneBotMessage.model_validate(dict(payload))
        except (ValidationError, TypeError, ValueError) as exc:
            raise OneBotEventError("Invalid OneBot message event") from exc
        if event.post_type != "message" or event.message_type not in {"private", "group"}:
            raise OneBotEventError("Only private and group message events are accepted")
        account = _account_hash(str(event.self_id), self._account_hasher)
        timestamp = _timestamp(event, captured_at)
        conversation = _conversation_id(event)
        self._events[(source_pid, account, conversation)].append(
            (event, _message_from_event(event), timestamp)
        )
        return conversation

    def context_for_target(
        self,
        request: OpenRequest,
        *,
        source_pid: int,
        account_id_hash: str,
        conversation_id: str,
        captured_at: datetime,
        conversation_title: str | None = None,
    ) -> ChatContext | None:
        if not _ACCOUNT_HASH_RE.fullmatch(account_id_hash) or source_pid <= 0:
            return None
        if captured_at.tzinfo is None:
            return None
        entries = self._events.get((source_pid, account_id_hash, conversation_id))
        if not entries:
            return None
        messages = []
        target_id = None
        for event, _, timestamp in entries:
            message = _message_from_event(event, target=request.target)
            if abs((timestamp - captured_at).total_seconds()) > 30:
                continue
            messages.append(message)
            if target_matches_message(request.target, message):
                target_id = message.message_id
        if target_id is None:
            return None
        context = ChatContext(
            provider="qq",
            account_id_hash=account_id_hash,
            conversation_id=conversation_id,
            conversation_title=conversation_title,
            target_message_id=target_id,
            messages=tuple(messages[-self._max_messages :]),
            acquisition="internal",
            confidence=1.0,
        )
        return validated_chat(context, request)

    def clear(self) -> None:
        self._events.clear()
