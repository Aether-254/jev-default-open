from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from jev_open.domain import ChatContext, ChatMessage, FileTarget
from jev_open.persistence import provenance
from jev_open.persistence.provenance import fingerprint_file
from jev_open.persistence.sqlcipher import SQLCipherStateModule


@pytest.fixture
def state(tmp_path: Path):
    if os.name != "nt":
        pytest.skip("DPAPI integration requires Windows")
    return SQLCipherStateModule(tmp_path / "state.db")


def target_for(path: Path) -> FileTarget:
    return FileTarget(path=path, extension=path.suffix)


def fixture_chat(
    path: Path,
    *,
    provider: str = "qq",
    account: str = "account-a",
    conversation: str = "conversation-a",
) -> ChatContext:
    return ChatContext(
        provider=provider,
        account_id_hash=account,
        conversation_id=conversation,
        target_message_id="attachment-1",
        acquisition="internal",
        confidence=1,
        conversation_labels=("finance",),
        messages=(
            ChatMessage(
                message_id="attachment-1",
                message_type="file",
                attachment_path=path,
                text="SYNTHETIC FILE ORIGIN",
            ),
        ),
    )


@pytest.mark.asyncio
async def test_provenance_is_encrypted_and_requires_current_provider_consent(state, tmp_path: Path):
    path = tmp_path / "report.csv"
    path.write_bytes(b"synthetic sample data")
    target = target_for(path)
    chat = fixture_chat(path)
    await state.remember_provenance(target, chat)
    assert await state.restore_provenance(target, enabled_providers=set()) is None
    assert await state.restore_provenance(target, enabled_providers={"wechat"}) is None
    restored = await state.restore_provenance(target, enabled_providers={"qq"})
    assert restored and restored.messages == chat.messages
    assert restored.conversation_labels == ("finance",)
    assert any("not live chat" in warning for warning in restored.warnings)
    assert b"SYNTHETIC FILE ORIGIN" not in (tmp_path / "state.db").read_bytes()


@pytest.mark.asyncio
async def test_provenance_does_not_open_target_when_all_providers_disabled(state, monkeypatch):
    def unexpected(_):
        raise AssertionError("Must not inspect file contents without provider consent")

    monkeypatch.setattr("jev_open.persistence.sqlcipher.fingerprint_file", unexpected)
    assert (
        await state.restore_provenance(target_for(Path("C:/Synthetic/report.csv")), set()) is None
    )


@pytest.mark.asyncio
async def test_moved_file_restores_only_exact_fingerprint_and_updates_anchor(state, tmp_path: Path):
    original = tmp_path / "original.csv"
    original.write_bytes(b"synthetic file to move")
    chat = fixture_chat(original)
    await state.remember_provenance(target_for(original), chat)
    moved = tmp_path / "renamed.csv"
    original.rename(moved)
    restored = await state.restore_provenance(target_for(moved), {"qq"})
    assert restored and restored.messages[0].attachment_path == moved
    assert chat.messages[0].attachment_path == original
    assert await state.prune_provenance(now=datetime.now(UTC) + timedelta(days=31)) == 0


@pytest.mark.asyncio
async def test_changed_content_or_metadata_never_reuses_origin(state, tmp_path: Path):
    path = tmp_path / "report.csv"
    path.write_bytes(b"original")
    metadata = path.stat()
    await state.remember_provenance(target_for(path), fixture_chat(path))
    path.write_bytes(b"modified")
    os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
    assert await state.restore_provenance(target_for(path), {"qq"}) is None
    path.write_bytes(b"original")
    os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1_000_000_000))
    assert await state.restore_provenance(target_for(path), {"qq"}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "second",
    [
        {"account": "other-account"},
        {"conversation": "other-conversation"},
        {"provider": "wechat"},
    ],
)
async def test_ambiguous_fingerprint_is_not_arbitrarily_bound(state, tmp_path: Path, second):
    path = tmp_path / "report.csv"
    path.write_bytes(b"synthetic shared attachment")
    target = target_for(path)
    await state.remember_provenance(target, fixture_chat(path))
    await state.remember_provenance(target, fixture_chat(path, **second))
    assert await state.restore_provenance(target, {"qq", "wechat"}) is None
    if second.get("provider") == "wechat":
        assert (await state.restore_provenance(target, {"qq"})).provider == "qq"


@pytest.mark.asyncio
async def test_repeated_same_message_is_not_ambiguous(state, tmp_path: Path):
    path = tmp_path / "report.csv"
    path.write_bytes(b"synthetic attachment")
    for _ in range(2):
        await state.remember_provenance(target_for(path), fixture_chat(path))
    assert await state.restore_provenance(target_for(path), {"qq"}) is not None


@pytest.mark.asyncio
async def test_unanchored_or_unidentified_chat_is_never_recorded(state, tmp_path: Path):
    path = tmp_path / "report.csv"
    path.write_bytes(b"synthetic attachment")
    for chat in (
        fixture_chat(path).model_copy(update={"account_id_hash": ""}),
        fixture_chat(path).model_copy(update={"target_message_id": None}),
        fixture_chat(tmp_path / "other.csv"),
    ):
        with pytest.raises(ValueError, match="anchored"):
            await state.remember_provenance(target_for(path), chat)
    assert await state.restore_provenance(target_for(path), {"qq"}) is None


@pytest.mark.parametrize(
    "path", [r"\\host\share\report.csv", r"\\.\PhysicalDrive0", "relative.csv"]
)
def test_remote_device_and_relative_paths_do_not_stat_or_open(path, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("Must not inspect unsafe path")

    monkeypatch.setattr(Path, "lstat", unexpected)
    monkeypatch.setattr(Path, "open", unexpected)
    assert fingerprint_file(target_for(Path(path))) is None


@pytest.mark.parametrize("attributes", [0x400, 0x1000, 0x40000, 0x400000])
def test_cloud_placeholder_and_reparse_attributes_prevent_content_read(
    tmp_path, monkeypatch, attributes
):
    path = tmp_path / "placeholder.csv"

    def mocked_lstat(self):
        return SimpleNamespace(st_mode=0x8000, st_file_attributes=attributes)

    def unexpected(*args, **kwargs):
        raise AssertionError("Placeholder contents must not be hydrated")

    monkeypatch.setattr(Path, "lstat", mocked_lstat)
    monkeypatch.setattr(Path, "open", unexpected)
    assert fingerprint_file(target_for(path)) is None


def test_fingerprint_reads_only_first_last_64k(tmp_path: Path):
    path = tmp_path / "large.csv"
    path.write_bytes(b"A" * 65536 + b"B" * 65536 + b"C" * 65536)
    fingerprint = fingerprint_file(target_for(path))
    assert fingerprint and fingerprint.size == 196608
    import hashlib

    assert fingerprint.head_sha256 == hashlib.sha256(b"A" * 65536).hexdigest()
    assert fingerprint.tail_sha256 == hashlib.sha256(b"C" * 65536).hexdigest()


@pytest.mark.asyncio
async def test_missing_index_pruned_after_30days_without_directory_enumeration(
    state, tmp_path, monkeypatch
):
    path = tmp_path / "report.csv"
    path.write_bytes(b"synthetic attachment")
    await state.remember_provenance(target_for(path), fixture_chat(path))
    path.unlink()
    inspected = []
    original = provenance.known_path_exists

    def only_known_path(value):
        inspected.append(value)
        assert value.casefold() == str(path).casefold()
        return original(value)

    def unexpected(*args, **kwargs):
        raise AssertionError("Never scan directories")

    monkeypatch.setattr("jev_open.persistence.sqlcipher.known_path_exists", only_known_path)
    monkeypatch.setattr(Path, "iterdir", unexpected)
    monkeypatch.setattr(Path, "rglob", unexpected)
    now = datetime.now(UTC)
    assert await state.prune_provenance(now=now) == 0
    assert await state.prune_provenance(now=now + timedelta(days=29)) == 0
    assert await state.prune_provenance(now=now + timedelta(days=30, seconds=1)) == 1
    assert len(inspected) == 3


@pytest.mark.asyncio
async def test_clear_provider_data_preserves_other_provider(state, tmp_path: Path):
    first, second = tmp_path / "qq.csv", tmp_path / "wechat.csv"
    first.write_bytes(b"qq data")
    second.write_bytes(b"wechat data")
    await state.remember_provenance(target_for(first), fixture_chat(first))
    await state.remember_provenance(target_for(second), fixture_chat(second, provider="wechat"))
    await state.clear_provider_data("qq")
    assert await state.restore_provenance(target_for(first), {"qq"}) is None
    assert await state.restore_provenance(target_for(second), {"wechat"}) is not None
