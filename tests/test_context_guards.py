from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jev_open.context.module import DefaultContextModule, conversation_label_key
from jev_open.context.providers.qq import QQContextProvider
from jev_open.context.providers.skeletons import (
    DingTalkContextProvider,
    FeishuContextProvider,
    SlackContextProvider,
)
from jev_open.context.providers.wechat import WeChatContextProvider
from jev_open.domain import ChatContext, ChatMessage, OpenRequest, OpenVerb
from jev_open.domain.errors import UnsupportedTarget


def request(target: str, **changes) -> OpenRequest:
    values = dict(
        request_id="context-guard",
        source_pid=10,
        source_executable=Path("C:/QQ/QQ.exe"),
        target=target,
        captured_at=datetime.now(UTC),
        verb=OpenVerb.OPEN,
    )
    values.update(changes)
    return OpenRequest(**values)


def event_for(value: OpenRequest, **changes) -> dict[str, object]:
    payload = dict(
        target=value.target,
        source_pid=value.source_pid,
        account_id_hash="a" * 64,
        conversation_id="conversation-a",
        conversation_title="Synthetic messages",
        target_message_id="message-1",
        captured_at=value.captured_at.isoformat(),
        messages=[
            dict(
                message_id="message-1",
                message_type="link",
                url=value.target,
                text="Synthetic target link",
            )
        ],
    )
    payload.update(changes)
    return payload


@pytest.mark.asyncio
async def test_qq_requires_explicit_enable_and_authenticated_peer():
    value = request("https://example.test/report")
    provider = QQContextProvider(qqcli_path=Path("C:/untrusted/qqcli.exe"))
    assert provider.status == "disabled"
    assert not provider.supports(value)
    assert not provider.ingest_event(event_for(value), peer_pid=10, authenticated=True)
    provider.start()
    assert not provider.ingest_event(event_for(value), peer_pid=10)
    assert not provider.ingest_event(event_for(value), peer_pid=999, authenticated=True)
    assert provider.ingest_event(event_for(value), peer_pid=10, authenticated=True)
    context = await provider.resolve(value, datetime.now(UTC) + timedelta(seconds=1))
    assert context and context.conversation_id == "conversation-a"
    assert context.messages[0].url == value.target
    provider.stop()
    assert await provider.resolve(value, datetime.now(UTC) + timedelta(seconds=1)) is None


@pytest.mark.asyncio
async def test_qq_rejects_ambiguous_conversation_and_source_pid():
    value = request("https://example.test/shared")
    provider = QQContextProvider(enabled=True)
    assert provider.ingest_event(event_for(value), peer_pid=10, authenticated=True)
    wrong_pid = value.model_copy(update={"source_pid": 99})
    assert await provider.resolve(wrong_pid, datetime.now(UTC) + timedelta(seconds=1)) is None
    assert provider.ingest_event(
        event_for(value, conversation_id="conversation-b"), peer_pid=10, authenticated=True
    )
    assert await provider.resolve(value, datetime.now(UTC) + timedelta(seconds=1)) is None


@pytest.mark.asyncio
async def test_qq_replay_transport_is_the_only_callback_that_can_mark_events_authenticated():
    class ReplayTransport:
        transport_id = "fixture-qq-bridge"

        def __init__(self, authenticated: bool):
            self.authenticated = authenticated
            self.receiver = None

        def is_authenticated(self) -> bool:
            return self.authenticated

        def start(self, receiver):
            self.receiver = receiver

        def stop(self):
            self.receiver = None

        def replay(self, payload, peer_pid):
            assert self.receiver is not None
            return self.receiver(payload, peer_pid)

    value = request("https://example.test/replay")
    transport = ReplayTransport(authenticated=False)
    provider = QQContextProvider(enabled=True, transport=transport)
    provider.start()
    assert not transport.replay(event_for(value), value.source_pid)
    transport.authenticated = True
    assert transport.replay(event_for(value), value.source_pid)
    context = await provider.resolve(value, datetime.now(UTC) + timedelta(seconds=1))
    assert context and context.conversation_id == "conversation-a"
    provider.stop()
    assert transport.receiver is None


@pytest.mark.parametrize(
    "update",
    [
        {"captured_at": "2000-01-01T00:00:00+00:00"},
        {"captured_at": "2026-01-01T00:00:00"},
        {"account_id_hash": "account-plaintext"},
        {"conversation_id": ""},
        {"target_message_id": "absent"},
        {"messages": []},
        {"unexpected": "ignored-field-is-not-allowed"},
        {"source_pid": "10"},
    ],
)
def test_qq_event_validation(update):
    value = request("https://example.test/shared")
    provider = QQContextProvider(enabled=True)
    assert not provider.ingest_event(event_for(value, **update), peer_pid=10, authenticated=True)


@pytest.mark.asyncio
async def test_disabled_wechat_never_calls_reader():
    class Reader:
        async def read(self, *args):
            raise AssertionError("Reader must not run")

    value = request("https://example.test/", source_executable=Path("C:/WeChat.exe"))
    provider = WeChatContextProvider(reader=Reader())
    assert not provider.supports(value)
    assert await provider.resolve(value, datetime.now(UTC) + timedelta(seconds=1)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider", [FeishuContextProvider, SlackContextProvider, DingTalkContextProvider]
)
async def test_unimplemented_providers_do_not_read(provider):
    adapter = provider()
    assert adapter.status == "not_implemented"
    assert await adapter.resolve(request("https://example.test"), datetime.now(UTC)) is None


@pytest.mark.asyncio
async def test_context_relative_path_uses_request_working_directory(tmp_path):
    path = tmp_path / "finance" / "report.csv"
    path.parent.mkdir()
    path.write_text("synthetic", encoding="utf-8")
    context = await DefaultContextModule(
        (), path_labels={str(tmp_path): ("parent",), str(path.parent): ("finance",)}
    ).assemble(
        request("report.csv", working_directory=path.parent),
        datetime.now(UTC) + timedelta(seconds=1),
    )
    assert context.target.path == path
    assert context.path_labels == ("finance",)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target, verb",
    [
        ("https://example.test/edit", OpenVerb.EDIT),
        ("ms-settings:privacy", OpenVerb.OPEN),
        ("C:/file.exe", OpenVerb.OPEN),
        ("https://example.test/\x00", OpenVerb.OPEN),
    ],
)
async def test_context_rejects_unsupported_targets(target, verb):
    with pytest.raises(UnsupportedTarget):
        await DefaultContextModule(()).assemble(
            request(target, verb=verb), datetime.now(UTC) + timedelta(seconds=1)
        )


@pytest.mark.asyncio
async def test_context_cancels_provider_at_deadline():
    cancelled = asyncio.Event()

    class SlowProvider:
        provider_id = "slow"

        def supports(self, value):
            return True

        async def resolve(self, value, deadline):
            try:
                await asyncio.sleep(60)
            finally:
                cancelled.set()

    result = await DefaultContextModule((SlowProvider(),)).assemble(
        request("https://example.test"), datetime.now(UTC) + timedelta(milliseconds=20)
    )
    assert result.chat_context is None
    assert cancelled.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("broken", ["account", "conversation", "anchor", "oversize", "duplicate"])
async def test_context_rejects_unbound_or_excess_messages(broken):
    value = request("https://example.test/context")
    messages = [ChatMessage(message_id="target", message_type="link", url=value.target)]
    fields = dict(
        provider="fixture",
        account_id_hash="hash",
        conversation_id="conversation",
        target_message_id="target",
        messages=tuple(messages),
        acquisition="internal",
        confidence=1,
    )
    if broken == "account":
        fields["account_id_hash"] = ""
    if broken == "conversation":
        fields["conversation_id"] = None
    if broken == "anchor":
        fields["target_message_id"] = "wrong"
    if broken == "oversize":
        fields["messages"] = tuple(
            messages
            + [ChatMessage(message_id=str(i), message_type="text", text="test") for i in range(10)]
        )
    if broken == "duplicate":
        fields["messages"] = tuple(messages * 2)

    class Provider:
        provider_id = "fixture"

        def supports(self, value):
            return True

        async def resolve(self, value, deadline):
            return ChatContext(**fields)

    result = await DefaultContextModule((Provider(),)).assemble(
        value, datetime.now(UTC) + timedelta(seconds=1)
    )
    assert result.chat_context is None


@pytest.mark.asyncio
async def test_conversation_labels_are_account_scoped():
    value = request("https://example.test/context")
    provider = QQContextProvider(enabled=True)
    provider.ingest_event(event_for(value), peer_pid=10, authenticated=True)
    result = await DefaultContextModule(
        (provider,),
        conversation_labels={
            conversation_label_key("qq", "a" * 64, "conversation-a"): ("Finance",),
            conversation_label_key("qq", "b" * 64, "conversation-a"): ("Personal",),
        },
    ).assemble(value, datetime.now(UTC) + timedelta(seconds=1))
    assert result.chat_context.conversation_labels == ("Finance",)
    assert conversation_label_key("qq:a", "b", "c") != conversation_label_key("qq", "a:b", "c")


@pytest.mark.asyncio
async def test_remote_path_does_not_read_metadata(monkeypatch):
    from jev_open.context import module

    def forbid_read(path):
        raise AssertionError("Remote metadata must not be probed")

    monkeypatch.setattr(module, "_file_metadata", forbid_read)
    with pytest.raises(UnsupportedTarget, match="Remote"):
        await DefaultContextModule(()).assemble(
            request(r"\\server\share\report.csv"), datetime.now(UTC) + timedelta(seconds=1)
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["supports", "resolve", "validation"])
async def test_broken_optional_provider_keeps_path_context(failure, monkeypatch):
    from jev_open.context import module

    class BrokenProvider:
        provider_id = "broken"

        def supports(self, value):
            if failure == "supports":
                raise RuntimeError("Synthetic provider probe failure")
            return True

        async def resolve(self, value, deadline):
            if failure == "resolve":
                raise RuntimeError("Synthetic provider read failure")
            return None

    if failure == "validation":

        def broken_validation(context, value):
            raise RuntimeError("Synthetic validation failure")

        monkeypatch.setattr(module, "validated_chat", broken_validation)

    value = request("https://example.test/report?exact=%2f")
    result = await DefaultContextModule((BrokenProvider(),)).assemble(
        value, datetime.now(UTC) + timedelta(seconds=1)
    )
    assert result.chat_context is None
    assert result.target.url == value.target
    assert result.source_application == "QQ"


@pytest.mark.asyncio
async def test_optional_provider_does_not_swallow_external_cancellation():
    class CancelledProvider:
        provider_id = "cancelled"

        def supports(self, value):
            return True

        async def resolve(self, value, deadline):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await DefaultContextModule((CancelledProvider(),)).assemble(
            request("https://example.test"), datetime.now(UTC) + timedelta(seconds=1)
        )
