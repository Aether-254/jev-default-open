from datetime import UTC, datetime
from pathlib import Path

import pytest

from jev_open.context.providers.qq_onebot import OneBotEventError, OneBotMessageBuffer
from jev_open.domain import OpenRequest, OpenVerb


def request(target: str) -> OpenRequest:
    return OpenRequest(
        request_id="qq-onebot-test",
        source_pid=321,
        source_executable=Path("C:/Program Files/Tencent/QQNT/QQ.exe"),
        verb=OpenVerb.OPEN,
        target=target,
        captured_at=datetime(2026, 9, 27, 8, 0, tzinfo=UTC),
    )


def hasher(value: str) -> str:
    assert value == "10001"
    return "a" * 64


def event(message_id: int, message: object) -> dict:
    return {
        "post_type": "message",
        "message_type": "private",
        "self_id": 10001,
        "user_id": 42,
        "message_id": message_id,
        "time": 1790496000,
        "message": message,
    }


def test_onebot_buffer_builds_bounded_target_anchored_context():
    buffer = OneBotMessageBuffer(hasher, max_messages=3)
    buffer.ingest(event(1, [{"type": "text", "data": {"text": "before"}}]), source_pid=321)
    buffer.ingest(
        event(2, [{"type": "text", "data": {"text": "https://example.invalid/report"}}]),
        source_pid=321,
    )
    buffer.ingest(
        event(
            3,
            [{
                "type": "share",
                "data": {"url": "https://example.invalid/report", "title": "Report"},
            }],
        ),
        source_pid=321,
    )

    context = buffer.context_for_target(
        request("https://example.invalid/report"),
        source_pid=321,
        account_id_hash="a" * 64,
        conversation_id="private:42",
        captured_at=datetime(2026, 9, 27, 8, 0, tzinfo=UTC),
    )

    assert context is not None
    assert context.provider == "qq"
    assert context.target_message_id == "3"
    assert len(context.messages) == 3
    assert context.messages[-1].url == "https://example.invalid/report"


def test_onebot_buffer_requires_exact_file_anchor():
    buffer = OneBotMessageBuffer(hasher)
    buffer.ingest(
        event(
            7,
            [{"type": "file", "data": {"file": r"C:\\Synthetic\\report.csv"}}],
        ),
        source_pid=321,
    )

    context = buffer.context_for_target(
        request(r"C:\\Synthetic\\report.csv"),
        source_pid=321,
        account_id_hash="a" * 64,
        conversation_id="private:42",
        captured_at=datetime(2026, 9, 27, 8, 0, tzinfo=UTC),
    )

    assert context is not None
    assert context.target_message_id == "7"
    assert context.messages[0].attachment_path == Path(r"C:\\Synthetic\\report.csv")


@pytest.mark.parametrize(
    "payload",
    [
        {"post_type": "notice"},
        {"post_type": "message", "message_type": "group", "self_id": 10001,
         "user_id": 42, "message_id": 1, "time": 1790496000, "message": "x"},
    ],
)
def test_onebot_buffer_rejects_non_message_or_unanchored_event(payload):
    buffer = OneBotMessageBuffer(hasher)
    with pytest.raises(OneBotEventError):
        buffer.ingest(payload, source_pid=321)


def test_onebot_buffer_rejects_untrusted_account_hasher():
    buffer = OneBotMessageBuffer(lambda _: "raw-account")
    with pytest.raises(OneBotEventError):
        buffer.ingest(event(1, "message"), source_pid=321)
