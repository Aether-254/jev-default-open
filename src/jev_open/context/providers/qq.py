from __future__ import annotations

import collections
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from jev_open.domain import ChatContext, ChatMessage, OpenRequest

from .validation import target_matches_message, validated_chat


class QQTrustedAdapter(Protocol):
    """Version-specific main-process reader supplied by a QQ integration.

    The project does not ship an implementation.  Implementations must resolve
    one clicked message and its bounded neighboring messages without trusting
    renderer-provided account or conversation identifiers.
    """

    client_version: str

    async def resolve_click(
        self, click: dict[str, str], *, signal: object
    ) -> dict[str, object]: ...


class QQAuthenticatedTransport(Protocol):
    """Authenticated event source injected by a platform-specific bridge.

    ``is_authenticated`` must perform the live peer/session check on every
    event.  A fixed-name unauthenticated pipe cannot satisfy this interface.
    """

    transport_id: str

    def is_authenticated(self) -> bool: ...

    def start(self, receiver: Callable[[dict[str, object], int], bool]) -> None: ...

    def stop(self) -> None: ...


class _BridgeEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1, max_length=32_768)
    source_pid: int = Field(gt=0, strict=True)
    account_id_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    conversation_id: str = Field(min_length=1, max_length=256)
    conversation_title: str | None = Field(default=None, max_length=256)
    target_message_id: str = Field(min_length=1, max_length=256)
    captured_at: datetime
    messages: tuple[ChatMessage, ...] = Field(min_length=1, max_length=10)


class QQContextProvider:
    """Consume correlated events from an explicitly authenticated QQ adapter.

    Construction never starts a listener, scans an account, or launches qqcli.
    The transport must authenticate its peer before calling ``ingest_event``.
    There is intentionally no unauthenticated fixed-name named-pipe server.
    """

    provider_id = "qq"

    def __init__(
        self,
        qqcli_path: Path | None = None,
        *,
        enabled: bool = False,
        transport: QQAuthenticatedTransport | None = None,
    ) -> None:
        self._enabled = enabled
        self._transport = transport
        self._events: collections.deque[_BridgeEvent] = collections.deque(maxlen=128)
        self._lock = threading.Lock()
        self.status = "requires_authenticated_bridge" if enabled else "disabled"
        # Keep the old argument compatible; unscoped account-wide search is not safe.
        self._unverified_qqcli_path = qqcli_path

    def supports(self, request: OpenRequest) -> bool:
        return self._enabled and request.source_executable.name.casefold() == "qq.exe"

    def start(self) -> None:
        self._enabled = True
        self.status = "requires_authenticated_bridge"
        if self._transport is not None:
            try:
                self._transport.start(self.ingest_authenticated_event)
            except Exception:
                self.status = "requires_authenticated_bridge"

    def stop(self) -> None:
        self._enabled = False
        self.status = "disabled"
        with self._lock:
            self._events.clear()
        if self._transport is not None:
            try:
                self._transport.stop()
            except Exception:
                pass

    def ingest_authenticated_event(
        self, payload: dict[str, object], peer_pid: int
    ) -> bool:
        """Receive an event only through the currently bound live transport.

        The callback is deliberately narrower than ``ingest_event``: callers
        cannot provide an ``authenticated`` boolean.  The transport's current
        authentication state is checked immediately before validation.
        """
        transport = self._transport
        try:
            authenticated = transport is not None and transport.is_authenticated()
        except Exception:
            authenticated = False
        return self.ingest_event(payload, peer_pid=peer_pid, authenticated=authenticated)

    def ingest_event(
        self, payload: dict[str, object], *, peer_pid: int, authenticated: bool = False
    ) -> bool:
        """Accept only events whose transport has verified the local QQ peer.

        ``authenticated`` is not a wire field: it is supplied by the verified
        transport after checking the pipe peer PID, user SID, and session nonce.
        """
        if not self._enabled or not authenticated:
            return False
        try:
            event = _BridgeEvent.model_validate(payload)
        except (ValidationError, TypeError, ValueError):
            return False
        if event.source_pid != peer_pid or event.captured_at.tzinfo is None:
            return False
        if abs((datetime.now(UTC) - event.captured_at).total_seconds()) > 30:
            return False
        anchor = next(
            (
                message
                for message in event.messages
                if message.message_id == event.target_message_id
            ),
            None,
        )
        if anchor is None or not target_matches_message(event.target, anchor):
            return False
        with self._lock:
            if not self._enabled:
                return False
            self._events.append(event)
        self.status = "ready"
        return True

    async def resolve(self, request: OpenRequest, deadline: datetime) -> ChatContext | None:
        if not self.supports(request) or datetime.now(UTC) >= deadline:
            return None
        if request.captured_at.tzinfo is None:
            return None
        now = datetime.now(UTC)
        with self._lock:
            self._events = collections.deque(
                (
                    event
                    for event in self._events
                    if (now - event.captured_at).total_seconds() <= 30
                ),
                maxlen=128,
            )
            matches = [
                event
                for event in self._events
                if event.target == request.target
                and event.source_pid == request.source_pid
                and abs((event.captured_at - request.captured_at).total_seconds()) <= 2
            ]
        identities = {
            (event.account_id_hash, event.conversation_id, event.target_message_id)
            for event in matches
        }
        if len(identities) != 1:
            return None
        event = matches[-1]
        context = ChatContext(
            provider=self.provider_id,
            account_id_hash=event.account_id_hash,
            conversation_id=event.conversation_id,
            conversation_title=event.conversation_title,
            target_message_id=event.target_message_id,
            messages=event.messages,
            acquisition="internal",
            confidence=1.0,
        )
        return validated_chat(context, request)
