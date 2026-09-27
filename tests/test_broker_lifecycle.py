from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from jev_open.broker import BrokerApplication
from jev_open.domain import (
    ChatContext,
    ChatMessage,
    ConfirmationResult,
    ContextEnvelope,
    FileTarget,
    OpenAction,
    OpenDecision,
    OpenRequest,
    PreferenceRule,
    SceneDecision,
    UrlTarget,
)


def request(request_id="r1", target="C:/demo/report.csv", source="explorer") -> OpenRequest:
    return OpenRequest(
        request_id=request_id, source_pid=123, source_executable=Path(f"C:/{source}.exe"),
        verb="open", target=target, captured_at=datetime.now(UTC),
    )


def action(action_id="excel") -> OpenAction:
    return OpenAction(
        action_id=action_id, application_id=action_id, display_name=action_id,
        capability_description="Test action", invocation_kind="executable",
        invocation_data={"executable": "fake.exe", "arguments": ["{target}"]},
    )


class Interception:
    def __init__(self):
        self.started = self.stopped = 0
        self.active = False
        self.fallbacks = []
        self.start_error = None
        self.fallback_error = None

    async def start(self, handler):
        self.started += 1
        if self.start_error:
            raise self.start_error
        self.active = True

    async def stop(self):
        self.stopped += 1
        self.active = False

    async def emergency_stop(self):
        self.active = False

    async def fallback(self, value):
        self.fallbacks.append(value.request_id)
        if self.fallback_error:
            raise self.fallback_error

    def is_active(self):
        return self.active


class Context:
    def __init__(self):
        self.delay = 0
        self.chats = {}
        self.deadlines = []
        self.error = None

    async def assemble(self, value, deadline):
        self.deadlines.append(deadline)
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        target = (
            UrlTarget(url=value.target, scheme="https", host="example.test")
            if value.target.startswith("https:")
            else FileTarget(path=Path(value.target), extension=".csv")
        )
        chat = self.chats.get(value.request_id)
        return ContextEnvelope(
            request=value, target=target, source_application=value.source_executable.stem,
            chat_context=chat, source_account_hash=chat.account_id_hash if chat else None,
        )


class Actions:
    def __init__(self):
        self.candidates = (action(), action("vscode"))
        self.attempted = []
        self.launched = []
        self.failures = 0
        self.delay = 0
        self.launch_error = None

    async def discover(self, target):
        await asyncio.sleep(self.delay)
        return self.candidates

    async def launch(self, selected, context):
        self.attempted.append(selected.action_id)
        if self.launch_error is not None:
            raise self.launch_error
        if self.failures:
            self.failures -= 1
            raise OSError("private target must not be logged")
        self.launched.append(selected.action_id)


class Decision:
    def __init__(self):
        self.calls = 0
        self.delay = 0
        self.error = None

    async def decide(self, context, deadline):
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return OpenDecision(
            source="jev", action_id="excel", probabilities={"excel": .9, "vscode": .1},
            scene=SceneDecision(scene_id="finance", probabilities={"finance": 1.}),
            latency_ms=1,
        )


class State:
    def __init__(self):
        self.rows = []
        self.saved = []
        self.preference = None
        self.audit_error = None
        self.save_error = None
        self.find_delay = 0

    async def find_preference(self, context):
        await asyncio.sleep(self.find_delay)
        return self.preference

    async def save_preference(self, rule):
        self.saved.append(rule)
        if self.save_error:
            raise self.save_error

    async def record_decision(
        self, context, decision, final_action_id, *, final_scene_id=None, status=None,
    ):
        self.rows.append({
            "context": context, "decision": decision, "action_id": final_action_id,
            "scene_id": final_scene_id, "status": status,
        })
        if self.audit_error:
            raise self.audit_error


class Ui:
    def __init__(self):
        self.presentations = []
        self.confirmations = []
        self.delay = 0
        self.wait = None
        self.entered = asyncio.Event()
        self.cancel_count = 0
        self.notifications = []
        self.bound = None

    def bind(self, **runtime):
        self.bound = runtime

    def run(self):
        assert threading.current_thread() is threading.main_thread()
        return 0

    async def enqueue(self, context, decision):
        self.presentations.append(decision)
        self.entered.set()
        if self.wait is not None:
            await self.wait.wait()
        await asyncio.sleep(self.delay)
        if self.confirmations:
            return self.confirmations.pop(0)
        return ConfirmationResult(confirmed=True, action_id="excel", scene_id="work_general")

    def cancel_pending(self):
        self.cancel_count += 1

    async def notify_launch_result(self, *args):
        self.notifications.append(args)


def broker(**kwargs):
    return BrokerApplication(
        interception=Interception(), context=Context(), actions=Actions(), decision=Decision(),
        state=State(), ui=Ui(), **kwargs,
    )


def rule(action_id="excel"):
    now = datetime.now(UTC)
    return PreferenceRule(
        rule_id="r", scope="exact_target", selector={"target": "C:/demo/report.csv"},
        action_id=action_id, created_at=now, updated_at=now,
    )


@pytest.mark.asyncio
async def test_human_confirmation_does_not_expire_with_model_deadline():
    app = broker(decision_deadline_ms=100)
    app.ui.delay = .15
    await app.handle_request(request())
    assert app.actions.launched == ["excel"]
    assert app.interception.fallbacks == []
    assert app.state.rows[-1]["scene_id"] == "work_general"


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["context", "actions", "decision", "state"])
async def test_every_preconfirmation_stage_obeys_total_deadline(stage):
    app = broker(decision_deadline_ms=20)
    component = getattr(app, stage)
    setattr(component, "find_delay" if stage == "state" else "delay", .1)
    await app.handle_request(request())
    assert app.interception.fallbacks == ["r1"]
    assert app.state.rows[-1]["status"] == "fallback"
    assert app.ui.presentations == []


@pytest.mark.asyncio
async def test_context_receives_its_own_short_deadline():
    app = broker(decision_deadline_ms=20_000, context_deadline_ms=500)
    before = datetime.now(UTC)
    value = request()
    await app.handle_request(value)
    assert 0 < (app.context.deadlines[0] - before).total_seconds() <= .6
    assert app.state.rows[-1]["context"].request.captured_at == value.captured_at


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    httpx.ConnectError("network failure"),
    httpx.HTTPStatusError(
        "service unavailable", request=httpx.Request("POST", "https://example.test"),
        response=httpx.Response(529),
    ),
    ValueError("malformed model response"),
])
async def test_model_errors_fallback_and_record_exactly_once(failure):
    app = broker()
    app.decision.error = failure
    await app.handle_request(request())
    assert app.interception.fallbacks == ["r1"]
    assert len(app.state.rows) == 1
    assert app.state.rows[0]["decision"].source == "system_fallback"


@pytest.mark.asyncio
async def test_no_candidates_skips_model_and_records_fallback():
    app = broker()
    app.actions.candidates = ()
    await app.handle_request(request())
    assert app.decision.calls == 0
    assert app.interception.fallbacks == ["r1"]
    assert app.state.rows[-1]["status"] == "fallback"


@pytest.mark.asyncio
async def test_failed_fallback_is_not_retried():
    app = broker()
    app.actions.candidates = ()
    app.interception.fallback_error = OSError("launch failed")
    await app.handle_request(request())
    assert app.interception.fallbacks == ["r1"]
    assert app.state.rows[-1]["status"] == "failed"


@pytest.mark.asyncio
async def test_launch_failure_returns_to_confirmation_without_default_open():
    app = broker()
    app.actions.failures = 1
    app.ui.confirmations = [
        ConfirmationResult(confirmed=True, action_id="excel", remember_scope=rule()),
        ConfirmationResult(confirmed=True, action_id="vscode", scene_id="data_analysis"),
    ]
    await app.handle_request(request())
    assert app.actions.attempted == ["excel", "vscode"]
    assert app.actions.launched == ["vscode"]
    assert app.interception.fallbacks == []
    assert app.ui.presentations[1].action_id is None
    assert "launch_failed:OSError" in app.ui.presentations[1].warnings
    assert app.state.saved == []
    assert app.state.rows[-1]["scene_id"] == "data_analysis"
    assert app.state.rows[-1]["decision"].action_id == "excel"


@pytest.mark.asyncio
async def test_audit_failure_after_launch_never_causes_second_open(caplog):
    app = broker()
    app.state.audit_error = OSError("private path / secrets")
    await app.handle_request(request())
    assert app.actions.launched == ["excel"]
    assert app.interception.fallbacks == []
    assert "private path" not in caplog.text


@pytest.mark.asyncio
async def test_preference_is_saved_only_after_successful_confirmed_launch():
    app = broker()
    preference = rule()
    app.ui.confirmations = [ConfirmationResult(
        confirmed=True, action_id="excel", remember_scope=preference,
    )]
    await app.handle_request(request())
    assert app.state.saved == [preference]
    assert app.state.rows[-1]["status"] == "launched"


@pytest.mark.asyncio
async def test_cancel_does_not_save_injected_remember_rule():
    app = broker()
    app.ui.confirmations = [ConfirmationResult(confirmed=False, remember_scope=rule())]
    await app.handle_request(request())
    assert app.state.saved == []
    assert app.actions.launched == []
    assert app.state.rows[-1]["status"] == "cancelled_by_user"


@pytest.mark.asyncio
async def test_preference_hit_skips_jev_but_still_requires_confirmation():
    app = broker()
    app.state.preference = rule()
    await app.handle_request(request())
    assert app.decision.calls == 0
    assert len(app.ui.presentations) == 1
    assert app.ui.presentations[0].source == "preference"
    assert not app.ui.presentations[0].scene.evaluated


@pytest.mark.asyncio
async def test_pending_exact_context_is_coalesced_but_terminal_request_is_not():
    app = broker()
    app.ui.wait = asyncio.Event()
    first = asyncio.create_task(app.handle_request(request("r1")))
    await app.ui.entered.wait()
    await app.handle_request(request("r2"))
    assert app.decision.calls == 1
    app.ui.wait.set()
    await first
    await app.handle_request(request("r3"))
    assert app.decision.calls == 2
    assert app.actions.launched == ["excel", "excel"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "variants", ["url_case", "conversation", "account", "evidence", "unknown_chat"],
)
async def test_distinct_contexts_are_never_coalesced(variants):
    app = broker()
    app.ui.wait = asyncio.Event()
    first_request = request("r1", source="qq")
    second_request = request("r2", source="qq")
    if variants == "url_case":
        first_request = request("r1", "https://example.test/Report")
        second_request = request("r2", "https://example.test/report")
    elif variants != "unknown_chat":
        base = ChatContext(
            provider="qq", account_id_hash="account-a", conversation_id="chat-a",
            acquisition="internal", confidence=1., messages=(ChatMessage(
                message_type="text", text="Finance"),),
        )
        changed = {
            "conversation": {"conversation_id": "chat-b"},
            "account": {"account_id_hash": "account-b"},
            "evidence": {"messages": (ChatMessage(message_type="text", text="Data analysis"),)},
        }[variants]
        app.context.chats = {"r1": base, "r2": base.model_copy(update=changed)}
    first = asyncio.create_task(app.handle_request(first_request))
    await app.ui.entered.wait()
    second = asyncio.create_task(app.handle_request(second_request))
    for _ in range(100):
        if len(app.ui.presentations) == 2:
            break
        await asyncio.sleep(.001)
    assert len(app.ui.presentations) == 2
    app.ui.wait.set()
    await asyncio.gather(first, second)
    assert len(app.actions.launched) == 2


@pytest.mark.asyncio
async def test_manual_enable_gate_and_disable_are_idempotent():
    app = broker(ready_check=lambda: False)
    with pytest.raises(RuntimeError, match="consent"):
        await app.enable()
    assert app.interception.started == 0
    app.ready_check = lambda: None
    with pytest.raises(RuntimeError, match="consent"):
        await app.enable()
    app.ready_check = lambda: True
    await app.enable()
    await app.enable()
    assert app.interception.started == 1
    assert app.enabled
    await app.disable()
    assert not app.enabled


@pytest.mark.asyncio
async def test_partial_hook_start_is_cleaned_up():
    app = broker()
    app.interception.start_error = OSError("failure after partial start")
    with pytest.raises(OSError):
        await app.enable()
    assert app.interception.stopped == 1
    assert not app.enabled


@pytest.mark.asyncio
async def test_disable_cancels_a_pending_confirmation_without_opening():
    app = broker()
    app.ui.wait = asyncio.Event()
    task = asyncio.create_task(app.handle_request(request()))
    await app.ui.entered.wait()
    await app.disable()
    assert task.cancelled()
    assert app.ui.cancel_count == 1
    assert not app._pending
    assert app.interception.fallbacks == []
    assert app.actions.launched == []
    assert app.state.rows[-1]["status"] == "cancelled"


def test_run_binds_background_loop_but_does_not_install_hooks():
    app = broker()
    assert app.run() == 0
    assert app.ui.bound["broker"] is app
    assert app.interception.started == 0
    assert app._loop is None
    assert not [thread for thread in threading.enumerate() if thread.name == "jev-open-broker"]


def test_ui_start_failure_still_stops_worker_thread():
    app = broker()

    def fail():
        raise RuntimeError("Qt startup failed")

    app.ui.run = fail
    with pytest.raises(RuntimeError, match="Qt startup"):
        app.run()
    assert app.interception.stopped == 1
    assert not [thread for thread in threading.enumerate() if thread.name == "jev-open-broker"]


@pytest.mark.asyncio
async def test_edit_request_excludes_open_only_candidates_before_model():
    app = broker()
    await app.handle_request(request().model_copy(update={"verb": "edit"}))
    assert app.decision.calls == 0
    assert app.interception.fallbacks == ["r1"]


@pytest.mark.asyncio
async def test_adapter_launch_timeout_is_unknown_and_does_not_offer_retry():
    app = broker()
    app.actions.launch_error = TimeoutError("native operation might still be running")
    await app.handle_request(request())
    assert len(app.ui.presentations) == 1
    assert app.actions.attempted == ["excel"]
    assert app.interception.fallbacks == []
    assert app.state.rows[-1]["status"] == "failed"
    assert "launch_outcome_unknown" in app.state.rows[-1]["decision"].warnings


def test_startup_marks_unclean_and_orderly_shutdown_marks_clean():
    app = broker()
    markers = []
    recovery = []

    async def read(name, default):
        return False

    async def write(name, value):
        markers.append((name, value))

    async def show_recovery():
        recovery.append(True)

    app.state.get_setting = read
    app.state.set_setting = write
    app.ui.show_recovery_report = show_recovery
    app.run()
    assert markers == [("clean_shutdown", False), ("clean_shutdown", True)]
    assert recovery == [True]


def test_ui_crash_preserves_unclean_marker_and_stops_context():
    app = broker()
    markers = []
    stopped = []

    async def read(name, default):
        return True

    async def write(name, value):
        markers.append(value)

    def fail():
        raise RuntimeError("UI crash")

    app.state.get_setting = read
    app.state.set_setting = write
    app.ui.run = fail
    app.context.stop = lambda: stopped.append(True)
    with pytest.raises(RuntimeError, match="UI crash"):
        app.run()
    assert markers == [False]
    assert stopped == [True]


@pytest.mark.asyncio
async def test_clear_history_also_clears_model_memory_cache():
    app = broker()
    cleared = []

    async def clear_history():
        cleared.append("history")

    app.state.clear_history = clear_history
    app.decision.clear_cache = lambda: cleared.append("model_cache")
    await app.clear_history()
    assert cleared == ["model_cache", "history", "model_cache"]


@pytest.mark.asyncio
@pytest.mark.parametrize("storage_fails", [False, True])
async def test_history_deletion_invalidates_before_and_after_blocking_storage(storage_fails):
    app = broker()
    events = []
    entered = asyncio.Event()
    release = asyncio.Event()

    async def clear_history():
        events.append("storage_started")
        entered.set()
        await release.wait()
        events.append("storage_finished")
        if storage_fails:
            raise OSError("database unavailable")

    app.state.clear_history = clear_history
    app.decision.clear_cache = lambda: events.append("cache_invalidated")
    deleting = asyncio.create_task(app.clear_history())
    await asyncio.wait_for(entered.wait(), 1)
    assert events == ["cache_invalidated", "storage_started"]
    events.append("decision_started_during_delete")
    release.set()
    if storage_fails:
        with pytest.raises(OSError, match="database unavailable"):
            await deleting
    else:
        await deleting
    assert events == [
        "cache_invalidated", "storage_started", "decision_started_during_delete",
        "storage_finished", "cache_invalidated",
    ]
    assert app.ui.cancel_count == 0
    assert app.interception.stopped == 0


@pytest.mark.asyncio
async def test_history_deletion_rejects_a_different_runtime_loop():
    app = broker()
    other = asyncio.new_event_loop()
    app._loop = other
    try:
        with pytest.raises(RuntimeError, match="broker event loop"):
            await app.clear_history()
    finally:
        app._loop = None
        other.close()


@pytest.mark.asyncio
async def test_provenance_maintenance_runs_on_startup_and_repeats_off_pipeline():
    app = broker(maintenance_interval_seconds=.01)
    calls = []
    repeated = asyncio.Event()

    async def prune():
        calls.append(True)
        if len(calls) >= 2:
            repeated.set()
        return 0

    app.state.prune_provenance = prune
    await app._startup()
    maintenance = app._maintenance_task
    assert maintenance is not None
    await asyncio.wait_for(repeated.wait(), 1)
    await app._shutdown(True)
    assert maintenance.cancelled()
    assert app._maintenance_task is None
    count = len(calls)
    await asyncio.sleep(.02)
    assert len(calls) == count


@pytest.mark.asyncio
async def test_shutdown_cancels_a_running_provenance_job_without_private_logs(caplog):
    app = broker(maintenance_interval_seconds=.01)
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    attempts = 0

    async def prune():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("private-account-path")
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    app.state.prune_provenance = prune
    await app._startup()
    await asyncio.wait_for(entered.wait(), 1)
    await app._shutdown(True)
    assert cancelled.is_set()
    assert "private-account-path" not in caplog.text
    assert "exception_type=OSError" in caplog.text
