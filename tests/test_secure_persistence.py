from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jev_open.domain import (
    ChatContext,
    ChatMessage,
    ContextEnvelope,
    FileTarget,
    OpenAction,
    OpenDecision,
    OpenRequest,
    OpenVerb,
    PreferenceRule,
    SceneDecision,
    UrlTarget,
)
from jev_open.persistence import sqlcipher
from jev_open.persistence.dpapi import protect, unprotect
from jev_open.persistence.preferences import create_preference, rule_matches
from jev_open.persistence.sqlcipher import SQLCipherStateModule, StateSecurityError


def sample_context(*, path: str = "C:/Synthetic/Finance/report.csv") -> ContextEnvelope:
    now = datetime.now(UTC)
    return ContextEnvelope(
        request=OpenRequest(
            request_id="synthetic-request",
            source_pid=101,
            source_executable=Path("C:/IM/QQ.exe"),
            verb=OpenVerb.OPEN,
            target=path,
            captured_at=now,
        ),
        target=FileTarget(path=Path(path), extension=".csv", mime_type="text/csv", size=20),
        source_application="QQ",
        source_account_hash="account-a",
        chat_context=ChatContext(
            provider="qq",
            account_id_hash="account-a",
            conversation_id="conversation-a",
            conversation_title="Synthetic Finance",
            confidence=1,
            acquisition="internal",
            messages=(
                ChatMessage(
                    message_id="m1",
                    message_type="text",
                    text="SYNTHETIC_PRIVATE_MARKER_982137 budget review",
                ),
            ),
        ),
        path_labels=("finance",),
        open_actions=(
            OpenAction(
                action_id="excel",
                application_id="excel",
                display_name="Excel",
                capability_description="Finance",
                invocation_kind="executable",
                invocation_data={"executable": "C:/Synthetic/excel.exe"},
            ),
            OpenAction(
                action_id="code",
                application_id="code",
                display_name="Code",
                capability_description="Analysis",
                invocation_kind="executable",
                invocation_data={"executable": "C:/Synthetic/code.exe"},
            ),
        ),
    )


def sample_decision() -> OpenDecision:
    return OpenDecision(
        source="jev",
        action_id="excel",
        probabilities={"excel": 0.9, "code": 0.1},
        confidence=0.8,
        latency_ms=20,
        scene=SceneDecision(scene_id="finance", probabilities={"finance": 1}),
    )


@pytest.fixture
def state(tmp_path: Path) -> SQLCipherStateModule:
    if os.name != "nt":
        pytest.skip("DPAPI integration requires Windows")
    return SQLCipherStateModule(tmp_path / "state.db")


def test_key_publish_does_not_replace_existing_file(tmp_path: Path) -> None:
    source = tmp_path / "new.key"
    destination = tmp_path / "state.db.key"
    source.write_bytes(b"new")
    destination.write_bytes(b"existing")

    with pytest.raises(FileExistsError):
        sqlcipher._move_no_replace(source, destination)

    assert destination.read_bytes() == b"existing"


@pytest.mark.asyncio
async def test_database_is_whole_file_encrypted_and_key_is_dpapi_wrapped(
    state: SQLCipherStateModule,
    tmp_path: Path,
) -> None:
    await state.record_decision(sample_context(), sample_decision(), "excel")
    payload = (tmp_path / "state.db").read_bytes()
    assert not payload.startswith(b"SQLite format 3")
    for forbidden in (b"SYNTHETIC_PRIVATE_MARKER", b"decision_history", b"conversation-a"):
        assert forbidden not in payload
    key_blob = (tmp_path / "state.db.key").read_bytes()
    key = unprotect(key_blob)
    assert len(key) == 32 and key not in key_blob
    assert state.cipher_version.startswith("4.")
    with sqlite3.connect(tmp_path / "state.db") as plaintext:
        with pytest.raises(sqlite3.DatabaseError):
            plaintext.execute("SELECT * FROM sqlite_master").fetchall()
    reopened = SQLCipherStateModule(tmp_path / "state.db")
    assert len(await reopened.list_history()) == 1


def test_wrong_key_rejected_without_resetting_database(state: SQLCipherStateModule, tmp_path: Path):
    from sqlcipher3 import dbapi2

    path = tmp_path / "state.db"
    before = path.read_bytes()
    connection = dbapi2.connect(str(path))
    try:
        connection.execute("PRAGMA key='wrong-test-key'")
        with pytest.raises(dbapi2.DatabaseError):
            connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
    finally:
        connection.close()
    (tmp_path / "state.db.key").write_bytes(protect(b"wrong" * 6 + b"!!"))
    with pytest.raises(StateSecurityError, match="Encrypted state"):
        SQLCipherStateModule(path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("key_contents", [None, b"not-a-dpapi-key"])
def test_missing_or_corrupt_key_never_creates_replacement(
    state: SQLCipherStateModule,
    tmp_path: Path,
    key_contents: bytes | None,
) -> None:
    path, key_path = tmp_path / "state.db", tmp_path / "state.db.key"
    before = path.read_bytes()
    if key_contents is None:
        key_path.unlink()
    else:
        key_path.write_bytes(key_contents)
    with pytest.raises(StateSecurityError):
        SQLCipherStateModule(path)
    assert path.read_bytes() == before
    assert not key_path.exists() if key_contents is None else key_path.read_bytes() == key_contents


def test_plaintext_existing_database_rejected_untouched(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE legacy(value TEXT)")
    before = path.read_bytes()
    with pytest.raises(StateSecurityError, match="plaintext SQLite"):
        SQLCipherStateModule(path)
    assert path.read_bytes() == before
    assert not (tmp_path / "state.db.key").exists()


def test_plain_sqlite_driver_cannot_impersonate_sqlcipher(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sqlcipher.importlib, "import_module", lambda name: sqlite3)
    with pytest.raises(StateSecurityError, match="does not provide SQLCipher"):
        SQLCipherStateModule(tmp_path / "state.db")
    assert not (tmp_path / "state.db").exists()


def test_missing_sqlcipher_is_error_not_plaintext_fallback(monkeypatch, tmp_path: Path) -> None:
    def unavailable(name):
        raise ImportError(name)

    monkeypatch.setattr(sqlcipher.importlib, "import_module", unavailable)
    with pytest.raises(StateSecurityError, match="SQLCipher is required"):
        SQLCipherStateModule(tmp_path / "state.db")
    assert not (tmp_path / "state.db").exists()


@pytest.mark.asyncio
async def test_history_tracks_corrections_outcomes_search_and_export(state, tmp_path: Path) -> None:
    context, decision = sample_context(), sample_decision()
    await state.record_decision(context, decision, None, status="awaiting_confirmation")
    await state.record_decision(
        context, decision, "code", final_scene_id="data_analysis", status="launched"
    )
    records = await state.list_history(query="budget")
    assert len(records) == 1
    assert records[0]["decision"]["action_id"] == "excel"
    assert records[0]["decision"]["scene"]["scene_id"] == "finance"
    assert records[0]["final_action_id"] == "code"
    assert records[0]["final_scene_id"] == "data_analysis"
    assert records[0]["status"] == "launched"
    export = tmp_path / "export.json"
    with pytest.raises(PermissionError):
        await state.export_history(export)
    assert not export.exists()
    assert await state.export_history(export, confirmed=True) == 1
    assert json.loads(export.read_text(encoding="utf-8")) == records
    with pytest.raises(ValueError):
        await state.export_history(tmp_path / "state.db", confirmed=True)
    await state.clear_history()
    assert await state.list_history() == []


@pytest.mark.asyncio
async def test_history_retains_only_newest_1000_and_no_duplicate_request(state) -> None:
    context, decision = sample_context(), sample_decision()
    for index in range(1002):
        instance = context.model_copy(
            update={
                "request": context.request.model_copy(update={"request_id": f"request-{index}"})
            }
        )
        await state.record_decision(instance, decision, "excel")
    records = await state.list_history()
    assert len(records) == 1000
    assert records[0]["request_id"] == "request-1001"
    assert records[-1]["request_id"] == "request-2"
    assert len(await state.list_history(limit=10, offset=995)) == 5


@pytest.mark.asyncio
async def test_settings_credentials_clear_and_account_hash_are_scoped(
    state, tmp_path: Path
) -> None:
    for name, value in [
        ("TYPESAFE_API_KEY", "never-write-me"),
        ("application_config", {"providers": [{"oauth_token": "secret"}]}),
        ("application_config", {"nested": {"password": "secret"}}),
    ]:
        with pytest.raises(ValueError, match="Credential"):
            await state.set_setting(name, value)
    assert await state.get_setting("missing", {"enabled": False}) == {"enabled": False}
    await state.set_setting("application_config", {"profile_labels": {"work": ["finance"]}})
    assert await state.get_setting("application_config") == {
        "profile_labels": {"work": ["finance"]}
    }
    first = state.hash_account("qq", "synthetic-user")
    assert first != state.hash_account("wechat", "synthetic-user")
    assert first != state.hash_account("qq", "other-user")
    assert first == SQLCipherStateModule(tmp_path / "state.db").hash_account("qq", "synthetic-user")
    await state.clear_all()
    assert await state.get_setting("application_config") is None


@pytest.mark.asyncio
async def test_concurrent_writes_are_serialized_per_database(state) -> None:
    await asyncio.gather(*(state.set_setting(f"setting-{i}", i) for i in range(12)))
    assert await asyncio.gather(*(state.get_setting(f"setting-{i}") for i in range(12))) == list(
        range(12)
    )


@pytest.mark.asyncio
async def test_exact_cache_includes_context_candidates_model_configuration_and_invalidates(state):
    context, decision = sample_context(), sample_decision()
    configuration = {"endpoint": "https://one.test", "scenes": {"finance": "budget review"}}
    await state.set_exact_cache(context, decision, model="jev-test", configuration=configuration)
    new_request = context.model_copy(
        update={
            "request": context.request.model_copy(
                update={"request_id": "next", "captured_at": datetime.now(UTC), "source_pid": 102}
            )
        }
    )
    hit = await state.get_exact_cache(new_request, model="jev-test", configuration=configuration)
    assert hit and hit.source == "exact_cache"
    assert await state.get_exact_cache(context, model="other", configuration=configuration) is None
    assert await state.get_exact_cache(context, model="jev-test", configuration={}) is None
    changed_context = context.model_copy(update={"path_labels": ("personal",)})
    assert (
        await state.get_exact_cache(changed_context, model="jev-test", configuration=configuration)
        is None
    )
    changed_action = context.open_actions[0].model_copy(
        update={"capability_description": "changed"}
    )
    changed_context = context.model_copy(update={"open_actions": (changed_action,)})
    assert (
        await state.get_exact_cache(changed_context, model="jev-test", configuration=configuration)
        is None
    )
    changed_chat = context.chat_context.model_copy(
        update={"messages": (ChatMessage(message_type="text", text="different context"),)}
    )
    changed_context = context.model_copy(update={"chat_context": changed_chat})
    assert (
        await state.get_exact_cache(changed_context, model="jev-test", configuration=configuration)
        is None
    )
    await state.set_setting("profile_labels", {"excel": ["work"]})
    assert (
        await state.get_exact_cache(context, model="jev-test", configuration=configuration) is None
    )


@pytest.mark.asyncio
async def test_cache_expiry_and_preference_updates(state) -> None:
    context, decision = sample_context(), sample_decision()
    key = state._cache_key(context, "jev-test")
    state._set_exact_cache_sync(
        key, decision.model_dump_json(), (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    )
    assert await state.get_exact_cache(context, model="jev-test") is None
    await state.set_exact_cache(context, decision, model="jev-test")
    rule = create_preference(context, "code", "exact_target")
    await state.save_preference(rule)
    assert await state.get_exact_cache(context, model="jev-test") is None
    assert await state.find_preference(context) == rule
    await state.delete_preference(rule.rule_id)
    assert await state.find_preference(context) is None


@pytest.mark.asyncio
async def test_clean_shutdown_markers_preserve_persistent_cache_across_reopen(state, tmp_path):
    context, decision = sample_context(), sample_decision()
    await state.set_exact_cache(context, decision, model="jev-test")
    await state.set_setting("clean_shutdown", False)
    assert await state.get_exact_cache(context, model="jev-test") is not None
    await state.set_setting("clean_shutdown", True)
    reopened = SQLCipherStateModule(tmp_path / "state.db")
    assert await reopened.get_setting("clean_shutdown") is True
    await reopened.set_setting("clean_shutdown", False)
    hit = await reopened.get_exact_cache(context, model="jev-test")
    assert hit and hit.source == "exact_cache" and hit.action_id == "excel"
    await reopened.set_setting("profile_labels", {"excel": ["work"]})
    assert await reopened.get_exact_cache(context, model="jev-test") is None


def rule(scope: str, selector: dict[str, str]) -> PreferenceRule:
    now = datetime.now(UTC)
    return PreferenceRule(
        rule_id="rule",
        scope=scope,
        selector=selector,
        action_id="excel",
        created_at=now,
        updated_at=now,
    )


@pytest.mark.parametrize(
    "scope",
    [
        "exact_target",
        "conversation",
        "path_label",
        "path_prefix",
        "provider",
        "extension_or_mime",
        "url_domain",
        "global",
    ],
)
def test_empty_selectors_never_match(scope: str) -> None:
    assert not rule_matches(rule(scope, {}), sample_context())


def test_windows_path_normalization_and_prefix_segment_containment() -> None:
    context = sample_context()
    assert rule_matches(
        rule("exact_target", {"target": r"c:\synthetic\finance\..\Finance\REPORT.csv"}), context
    )
    selector = {"kind": "file", "verb": "open", "path": "c:/synthetic/finance"}
    assert rule_matches(rule("path_prefix", selector), context)
    assert not rule_matches(rule("path_prefix", {**selector, "path": "c:/synthetic/fin"}), context)
    assert not rule_matches(rule("path_prefix", {**selector, "path": ""}), context)
    assert not rule_matches(rule("path_prefix", {**selector, "path": "c:"}), context)


def test_conversation_and_provider_rules_require_account_provider_type_and_verb() -> None:
    context = sample_context()
    for scope in ("conversation", "provider"):
        preference = create_preference(context, "excel", scope)
        assert rule_matches(preference, context)
        for field in ("provider", "account_id_hash", "kind", "verb"):
            incomplete = {key: value for key, value in preference.selector.items() if key != field}
            assert not rule_matches(preference.model_copy(update={"selector": incomplete}), context)
        for update in ({"provider": "wechat"}, {"account_id_hash": "different-account"}):
            other = context.model_copy(
                update={"chat_context": context.chat_context.model_copy(update=update)}
            )
            assert not rule_matches(preference, other)
        edited = context.model_copy(
            update={"request": context.request.model_copy(update={"verb": OpenVerb.EDIT})}
        )
        assert not rule_matches(preference, edited)


def test_invalid_optional_extension_does_not_match_missing_mime() -> None:
    context = sample_context().model_copy(
        update={
            "target": FileTarget(
                path=Path("C:/Synthetic/report.csv"), extension=".csv", mime_type=None
            )
        }
    )
    assert not rule_matches(
        rule("extension_or_mime", {"kind": "file", "verb": "open", "extension": ".xlsx"}), context
    )
    assert not rule_matches(
        rule("extension_or_mime", {"kind": "file", "verb": "open", "mime": ""}), context
    )


def test_global_rules_are_type_specific_and_candidate_checked() -> None:
    context = sample_context()
    preference = create_preference(context, "excel", "global")
    assert rule_matches(preference, context)
    url = context.model_copy(
        update={
            "target": UrlTarget(url="https://example.test/", scheme="https", host="example.test")
        }
    )
    assert not rule_matches(preference, url)
    missing_action = context.model_copy(update={"open_actions": (context.open_actions[1],)})
    assert not rule_matches(preference, missing_action)


@pytest.mark.asyncio
async def test_most_specific_prefix_and_then_latest_rule_win(state) -> None:
    context = sample_context()
    broad = create_preference(context, "code", "path_prefix", path="C:/Synthetic")
    narrow = create_preference(context, "excel", "path_prefix", path="C:/Synthetic/Finance")
    broad = broad.model_copy(update={"updated_at": narrow.updated_at + timedelta(seconds=1)})
    await state.save_preference(broad)
    await state.save_preference(narrow)
    assert await state.find_preference(context) == narrow
    exact = create_preference(context, "code", "exact_target")
    await state.save_preference(exact)
    assert await state.find_preference(context) == exact


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalidation",
    [
        "clear_history",
        "clear_provider",
        "setting",
        "save_preference",
        "delete_preference",
        "clear_all",
    ],
)
async def test_invalidation_retires_already_queued_cache_writers(
    state,
    monkeypatch,
    invalidation,
) -> None:
    context, decision = sample_context(), sample_decision()
    rule = create_preference(context, "excel", "exact_target")
    await state.save_preference(rule)
    await state.set_exact_cache(context, decision, model="jev-test")
    entered, release = threading.Event(), threading.Event()
    original = state._set_exact_cache_sync

    def queued_writer(*args):
        entered.set()
        if not release.wait(timeout=10):
            raise AssertionError("Test failed to release queued cache writer")
        original(*args)

    monkeypatch.setattr(state, "_set_exact_cache_sync", queued_writer)
    pending = asyncio.create_task(state.set_exact_cache(context, decision, model="jev-test"))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        if invalidation == "clear_history":
            await state.clear_history()
        elif invalidation == "clear_provider":
            await state.clear_provider_data("qq")
        elif invalidation == "setting":
            await state.set_setting("profile_labels", {"excel": ["work"]})
        elif invalidation == "save_preference":
            await state.save_preference(rule)
        elif invalidation == "delete_preference":
            await state.delete_preference(rule.rule_id)
        else:
            await state.clear_all()
        assert await state.get_exact_cache(context, model="jev-test") is None
    finally:
        release.set()
        await pending
    assert await state.get_exact_cache(context, model="jev-test") is None
    # A genuinely new result after the barrier may populate the new epoch.
    await state.set_exact_cache(context, decision, model="jev-test")
    assert await state.get_exact_cache(context, model="jev-test") is not None


@pytest.mark.asyncio
async def test_history_cache_barrier_preserves_existing_file_provenance(
    state, tmp_path, monkeypatch
):
    path = tmp_path / "origin.csv"
    path.write_bytes(b"synthetic origin data")
    target = FileTarget(path=path, extension=".csv")
    chat = ChatContext(
        provider="qq",
        account_id_hash="account",
        conversation_id="conversation",
        target_message_id="message",
        acquisition="internal",
        confidence=1,
        messages=(ChatMessage(message_id="message", message_type="file", attachment_path=path),),
    )
    await state.remember_provenance(target, chat)
    entered, release = threading.Event(), threading.Event()
    original = state._set_exact_cache_sync

    def queued_writer(*args):
        entered.set()
        if not release.wait(timeout=10):
            raise AssertionError("Test failed to release queued cache writer")
        original(*args)

    monkeypatch.setattr(state, "_set_exact_cache_sync", queued_writer)
    pending = asyncio.create_task(
        state.set_exact_cache(sample_context(), sample_decision(), model="test")
    )
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        await state.clear_history()
    finally:
        release.set()
        await pending
    assert await state.get_exact_cache(sample_context(), model="test") is None
    restored = await state.restore_provenance(target, {"qq"})
    assert restored and restored.conversation_id == "conversation"


@pytest.mark.asyncio
async def test_cache_writer_does_not_block_event_loop_while_worker_holds_storage_lock(state):
    held, release, timed_out = threading.Event(), threading.Event(), threading.Event()
    heartbeat = asyncio.Event()

    def hold_storage_lock():
        with state._lock:
            held.set()
            if not release.wait(timeout=5):
                timed_out.set()

    holder = asyncio.create_task(asyncio.to_thread(hold_storage_lock))
    pending = None
    try:
        assert await asyncio.to_thread(held.wait, 5)
        pending = asyncio.create_task(
            state.set_exact_cache(sample_context(), sample_decision(), model="test")
        )

        def loop_heartbeat():
            heartbeat.set()
            release.set()

        # create_task enqueued the writer first: its initial step must yield without the lock.
        asyncio.get_running_loop().call_soon(loop_heartbeat)
        await asyncio.gather(pending, holder)
        assert heartbeat.is_set()
        assert not timed_out.is_set(), "The event loop waited for the worker's synchronous lock"
        assert await state.get_exact_cache(sample_context(), model="test") is not None
    finally:
        release.set()
        await holder
        if pending is not None:
            await pending


@pytest.mark.asyncio
@pytest.mark.parametrize("purge", ["provider", "all"])
async def test_cancelled_provenance_worker_cannot_repopulate_purged_provider(
    state,
    tmp_path,
    monkeypatch,
    purge,
):
    path = tmp_path / "origin.csv"
    path.write_bytes(b"synthetic delayed origin")
    target = FileTarget(path=path, extension=".csv")
    chat = ChatContext(
        provider="qq",
        account_id_hash="account",
        conversation_id="conversation",
        target_message_id="message",
        acquisition="internal",
        confidence=1,
        messages=(ChatMessage(message_id="message", message_type="file", attachment_path=path),),
    )
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    fingerprint = sqlcipher.fingerprint_file
    remember = state._remember_provenance_sync

    def delayed_fingerprint(value):
        entered.set()
        if not release.wait(timeout=10):
            raise AssertionError("Test failed to release fingerprint worker")
        return fingerprint(value)

    def tracked_remember(*args):
        try:
            return remember(*args)
        finally:
            finished.set()

    monkeypatch.setattr(sqlcipher, "fingerprint_file", delayed_fingerprint)
    monkeypatch.setattr(state, "_remember_provenance_sync", tracked_remember)
    pending = asyncio.create_task(state.remember_provenance(target, chat))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        if purge == "provider":
            await state.clear_provider_data("qq")
        else:
            await state.clear_all()
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 5)
    assert await state.restore_provenance(target, {"qq"}) is None
    # Explicitly collected new origin after purge remains supported.
    await state.remember_provenance(target, chat)
    assert await state.restore_provenance(target, {"qq"}) is not None
