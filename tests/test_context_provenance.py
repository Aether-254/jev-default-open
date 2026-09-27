from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jev_open.context.module import DefaultContextModule
from jev_open.domain import ChatContext, ChatMessage, OpenRequest, OpenVerb


def request(path: Path, source: str = "explorer.exe") -> OpenRequest:
    return OpenRequest(
        request_id="provenance-test",
        source_pid=42,
        source_executable=Path("C:/Fixture") / source,
        target=str(path),
        verb=OpenVerb.OPEN,
        captured_at=datetime.now(UTC),
    )


def chat_for(path: Path) -> ChatContext:
    return ChatContext(
        provider="qq",
        account_id_hash="a" * 64,
        conversation_id="fixture-chat",
        target_message_id="m-1",
        messages=(ChatMessage(message_id="m-1", message_type="file", attachment_path=path),),
        acquisition="internal",
        confidence=1,
    )


class Store:
    def __init__(self, context=None):
        self.context = context
        self.remembered = []
        self.restored = []

    async def remember_provenance(self, target, chat):
        self.remembered.append((target, chat))

    async def restore_provenance(self, target, enabled_providers):
        self.restored.append((target, enabled_providers))
        return self.context


@pytest.mark.asyncio
async def test_exact_explorer_file_restores_enabled_provider_only(tmp_path):
    target = tmp_path / "report.csv"
    target.write_text("synthetic", encoding="utf-8")
    original = chat_for(target)
    store = Store(original)
    result = await DefaultContextModule((), provenance=store, enabled_providers=("qq",)).assemble(
        request(target), datetime.now(UTC) + timedelta(seconds=1)
    )
    assert store.restored[0][1] == {"qq"}
    assert result.chat_context.account_id_hash == "a" * 64
    assert "not live chat context" in result.chat_context.warnings[-1]
    assert original.warnings == ()
    assert store.remembered == []


@pytest.mark.asyncio
@pytest.mark.parametrize("source,enabled", [("other.exe", ("qq",)), ("explorer.exe", ())])
async def test_restore_never_queries_for_other_source_or_disabled_provider(
    tmp_path, source, enabled
):
    target = tmp_path / "report.csv"
    store = Store(chat_for(target))
    result = await DefaultContextModule((), provenance=store, enabled_providers=enabled).assemble(
        request(target, source), datetime.now(UTC) + timedelta(seconds=1)
    )
    assert result.chat_context is None
    assert store.restored == []


@pytest.mark.asyncio
async def test_store_cannot_return_other_target_or_disabled_provider(tmp_path):
    target = tmp_path / "report.csv"
    for wrong in (
        chat_for(tmp_path / "other.csv"),
        chat_for(target).model_copy(update={"provider": "wechat"}),
    ):
        result = await DefaultContextModule(
            (), provenance=Store(wrong), enabled_providers=("qq",)
        ).assemble(request(target), datetime.now(UTC) + timedelta(seconds=1))
        assert result.chat_context is None


@pytest.mark.asyncio
async def test_remember_only_newly_validated_enabled_provider_file(tmp_path):
    target = tmp_path / "report.csv"
    verified = chat_for(target)

    class Provider:
        provider_id = "qq"

        def supports(self, request):
            return True

        async def resolve(self, request, deadline):
            return verified

    store = Store()
    result = await DefaultContextModule(
        (Provider(),), provenance=store, enabled_providers=("qq",)
    ).assemble(request(target, "QQ.exe"), datetime.now(UTC) + timedelta(seconds=1))
    assert result.chat_context == verified
    assert store.remembered[0][1] == verified
    assert store.restored == []


@pytest.mark.asyncio
async def test_restore_timeout_cancels_auxiliary_work(tmp_path):
    target = tmp_path / "report.csv"
    cancelled = asyncio.Event()

    class SlowStore(Store):
        async def restore_provenance(self, target, enabled_providers):
            try:
                await asyncio.sleep(60)
            finally:
                cancelled.set()

    result = await DefaultContextModule(
        (), provenance=SlowStore(), enabled_providers=("qq",)
    ).assemble(request(target), datetime.now(UTC) + timedelta(seconds=1))
    assert result.chat_context is None
    assert cancelled.is_set()
