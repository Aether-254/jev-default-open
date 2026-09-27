from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jev_open.context.module import DefaultContextModule
from jev_open.domain import ChatContext, ChatMessage, OpenRequest, OpenVerb


def request(path: Path) -> OpenRequest:
    return OpenRequest(
        request_id="provider-test",
        source_pid=10,
        source_executable=Path("C:/Program Files/Tencent/QQNT/QQ.exe"),
        verb=OpenVerb.OPEN,
        target=str(path),
        captured_at=datetime.now(UTC),
    )


class Provider:
    provider_id = "fixture"

    def __init__(self, context: ChatContext | None) -> None:
        self.context = context

    def supports(self, value):
        return True

    async def resolve(self, value, deadline):
        return self.context


@pytest.mark.asyncio
async def test_provider_rejects_unreadable_visible_context(tmp_path: Path) -> None:
    target = tmp_path / "report.csv"
    target.write_text("x", encoding="utf-8")
    context = ChatContext(
        provider="fixture",
        account_id_hash="hash",
        messages=(ChatMessage(message_type="image"),),
        acquisition="uia",
        confidence=0.3,
    )
    result = await DefaultContextModule((Provider(context),)).assemble(
        request(target), datetime.now(UTC) + timedelta(seconds=1)
    )
    assert result.chat_context is None


@pytest.mark.asyncio
async def test_provider_preserves_ten_nearby_messages(tmp_path: Path) -> None:
    target = tmp_path / "report.csv"
    target.write_text("x", encoding="utf-8")
    messages = tuple(
        ChatMessage(
            message_id=str(index),
            message_type="text",
            text=str(index),
            attachment_path=target if index == 5 else None,
        )
        for index in range(10)
    )
    context = ChatContext(
        provider="fixture",
        account_id_hash="hash",
        conversation_id="test-conversation",
        target_message_id="5",
        messages=messages,
        acquisition="database",
        confidence=0.9,
    )
    result = await DefaultContextModule((Provider(context),)).assemble(
        request(target), datetime.now(UTC) + timedelta(seconds=1)
    )
    assert result.chat_context and len(result.chat_context.messages) == 10


def test_provider_warning_is_part_of_typed_context() -> None:
    context = ChatContext(
        provider="fixture",
        account_id_hash="hash",
        acquisition="internal",
        confidence=0.5,
        warnings=("Unknown client version; compatibility mode used.",),
    )
    assert "compatibility mode" in context.warnings[0]
