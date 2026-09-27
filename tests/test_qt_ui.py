from __future__ import annotations

import asyncio
import concurrent.futures
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from jev_open.domain import (
    ContextEnvelope,
    FileTarget,
    OpenAction,
    OpenDecision,
    OpenRequest,
    OpenVerb,
    SceneDecision,
)


def _context(request_id: str, action_ids: tuple[str, ...] = ("excel", "code")) -> ContextEnvelope:
    target = FileTarget(path=Path("C:/synthetic/Finance/report.csv"), extension=".csv")
    return ContextEnvelope(
        request=OpenRequest(
            request_id=request_id,
            source_pid=4242,
            source_executable=Path("C:/synthetic/explorer.exe"),
            verb=OpenVerb.OPEN,
            target=str(target.path),
            captured_at=datetime.now(UTC),
        ),
        target=target,
        source_application="Synthetic Explorer",
        path_labels=("Finance",),
        open_actions=tuple(
            OpenAction(
                action_id=action_id,
                application_id=action_id,
                display_name=action_id.title(),
                capability_description="Synthetic action; tests never execute this application.",
                invocation_kind="executable",
                invocation_data={"executable": f"C:/synthetic/{action_id}.exe"},
            )
            for action_id in action_ids
        ),
    )


def _decision(
    action_id: str | None = "excel",
    *,
    probabilities: dict[str, float] | None = None,
    scene_id: str | None = "finance",
    scene_probabilities: dict[str, float] | None = None,
    source: str = "jev",
) -> OpenDecision:
    return OpenDecision(
        source=source,
        action_id=action_id,
        probabilities=probabilities if probabilities is not None else {"excel": 0.85, "code": 0.15},
        confidence=0.9,
        scene=SceneDecision(
            scene_id=scene_id,
            probabilities=(
                scene_probabilities
                if scene_probabilities is not None
                else {"finance": 0.9, "data_analysis": 0.1}
            ),
            confidence=0.9,
            evaluated=source != "preference",
        ),
        latency_ms=12,
    )


@pytest.fixture
def ui(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from jev_open.ui.qt import QtUserInterfaceModule

    instance = QtUserInterfaceModule(collection_ms=20)
    instance._initialize()
    yield instance
    instance.cancel_pending()
    instance._drain()
    for dialog in instance._dialogs:
        dialog.close()
    instance._window.close()
    instance._timer.stop()
    instance._collection_timer.stop()
    instance._application.processEvents()


async def _pump(ui: Any, condition: Any, *, timeout: float = 1.0) -> None:
    until = time.monotonic() + timeout
    while not condition():
        ui._drain()
        ui._application.processEvents()
        if time.monotonic() >= until:
            raise AssertionError("Qt condition did not settle within the test deadline")
        await asyncio.sleep(0.005)


async def _pending(ui: Any, context: ContextEnvelope, decision: OpenDecision) -> asyncio.Task:
    request_id = context.request.request_id
    previous = ui._window.rows.get(request_id)
    previous_future = previous.future if previous is not None else None
    task = asyncio.create_task(ui.enqueue(context, decision))
    await asyncio.sleep(0)
    await _pump(
        ui,
        lambda: request_id in ui._window.rows
        and ui._window.rows[request_id].future is not previous_future,
    )
    return task


async def test_high_confidence_action_and_scene_can_be_corrected_independently(ui: Any) -> None:
    task = await _pending(ui, _context("independent"), _decision())
    row = ui._window.rows["independent"]
    assert row.action_box.currentData() == "excel"
    assert row.scene_box.currentData() == "finance"

    row.action_box.setCurrentIndex(row.action_box.findData("code"))
    assert row.scene_box.currentData() == "finance"
    row.scene_box.setCurrentIndex(row.scene_box.findData("personal"))
    assert row.action_box.currentData() == "code"
    ui._window.confirm("independent")

    result = await asyncio.wait_for(task, 1)
    assert result.confirmed
    assert result.action_id == "code"
    assert result.scene_id == "personal"
    assert result.remember_scope is None


async def test_low_confidence_requires_explicit_action_without_forcing_scene(ui: Any) -> None:
    task = await _pending(
        ui,
        _context("ambiguous"),
        _decision(
            probabilities={"excel": 0.55, "code": 0.45},
            scene_probabilities={"finance": 0.52, "data_analysis": 0.48},
        ),
    )
    row = ui._window.rows["ambiguous"]
    assert not row.action_box.currentData()
    assert not row.scene_box.currentData()
    ui._window.confirm("ambiguous")
    await asyncio.sleep(0)
    assert not task.done()

    row.action_box.setCurrentIndex(row.action_box.findData("code"))
    ui._window.confirm("ambiguous")
    result = await asyncio.wait_for(task, 1)
    assert result.confirmed
    assert result.action_id == "code"
    assert result.scene_id is None


async def test_candidates_are_ranked_and_all_remain_selectable(ui: Any) -> None:
    task = await _pending(
        ui,
        _context("ranking", ("fourth", "code", "excel", "third")),
        _decision(probabilities={"excel": 0.8, "code": 0.1, "third": 0.06, "fourth": 0.04}),
    )
    row = ui._window.rows["ranking"]
    ranked = [
        row.action_box.itemData(index)
        for index in range(row.action_box.count())
        if row.action_box.itemData(index)
    ]
    assert ranked == ["excel", "code", "third", "fourth"]
    assert "80" in row.action_box.itemText(row.action_box.findData("excel"))
    assert "%" in row.action_box.itemText(row.action_box.findData("excel"))
    row.action_box.setCurrentIndex(row.action_box.findData("fourth"))
    ui._window.confirm("ranking")
    assert (await asyncio.wait_for(task, 1)).action_id == "fourth"


async def test_missing_selected_action_is_not_silently_replaced_by_first_candidate(ui: Any) -> None:
    task = await _pending(ui, _context("no-selection"), _decision(action_id=None))
    assert not ui._window.rows["no-selection"].action_box.currentData()
    ui._window.cancel("no-selection")
    assert not (await asyncio.wait_for(task, 1)).confirmed


async def test_preference_can_select_without_model_probability_or_scene(ui: Any) -> None:
    task = await _pending(
        ui,
        _context("preference"),
        _decision(
            action_id="code",
            source="preference",
            probabilities={},
            scene_id=None,
            scene_probabilities={},
        ),
    )
    row = ui._window.rows["preference"]
    assert row.action_box.currentData() == "code"
    assert not row.scene_box.currentData()
    assert row.scene_box.findData("personal") >= 0
    assert row.scene_box.findData("finance") >= 0
    ui._window.confirm("preference")
    result = await asyncio.wait_for(task, 1)
    assert result.action_id == "code"
    assert result.scene_id is None


async def test_late_arrivals_share_one_nonmodal_batch_window(ui: Any, tmp_path: Path) -> None:
    window = ui._window
    first = await _pending(ui, _context("first"), _decision())
    await _pump(ui, window.isVisible)
    second = await _pending(ui, _context("second"), _decision())
    assert ui._window is window
    assert {"first", "second"}.issubset(window.rows)
    assert not first.done()
    assert not second.done()
    screenshot = tmp_path / "ui-smoke.png"
    assert window.grab().save(str(screenshot))
    assert screenshot.stat().st_size > 0

    window.close()
    results = await asyncio.wait_for(asyncio.gather(first, second), 1)
    assert all(not result.confirmed for result in results)


async def test_explicit_cancel_never_remembers_or_returns_an_action(ui: Any) -> None:
    task = await _pending(ui, _context("cancel"), _decision())
    ui._window.rows["cancel"].remember_box.setChecked(True)
    ui._window.cancel("cancel")
    result = await asyncio.wait_for(task, 1)
    assert not result.confirmed
    assert result.action_id is None
    assert result.remember_scope is None


async def test_failed_launch_retry_uses_a_new_future_and_new_selection(ui: Any) -> None:
    context = _context("retry")
    first = await _pending(ui, context, _decision())
    ui._window.confirm("retry")
    assert (await asyncio.wait_for(first, 1)).action_id == "excel"
    await ui.notify_launch_result("retry", False, "Synthetic failure")
    ui._drain()

    retry = await _pending(
        ui,
        context,
        _decision(action_id="code", probabilities={"excel": 0.1, "code": 0.9}),
    )
    assert ui._window.rows["retry"].action_box.currentData() == "code"
    ui._window.confirm("retry")
    result = await asyncio.wait_for(retry, 1)
    assert result.confirmed
    assert result.action_id == "code"


async def test_batch_apply_skips_incompatible_targets_and_unselected_rows(ui: Any) -> None:
    first = await _pending(ui, _context("compatible"), _decision())
    second = await _pending(
        ui,
        _context("incompatible", ("notepad",)),
        _decision("notepad", probabilities={"notepad": 1.0}),
    )
    third = await _pending(ui, _context("unselected"), _decision())
    ui._window.rows["compatible"].selected_box.setChecked(True)
    ui._window.rows["incompatible"].selected_box.setChecked(True)
    ui._window.rows["unselected"].selected_box.setChecked(False)
    ui._window.apply_to_selected("code")
    assert ui._window.rows["compatible"].action_box.currentData() == "code"
    assert ui._window.rows["incompatible"].action_box.currentData() == "notepad"
    assert ui._window.rows["unselected"].action_box.currentData() == "excel"

    ui._window.confirm_selected()
    assert (await asyncio.wait_for(first, 1)).action_id == "code"
    assert (await asyncio.wait_for(second, 1)).action_id == "notepad"
    assert not third.done()
    ui._window.cancel("unselected")
    assert not (await asyncio.wait_for(third, 1)).confirmed


async def test_remember_is_explicit_and_preserves_scope(ui: Any) -> None:
    task = await _pending(ui, _context("remember"), _decision())
    row = ui._window.rows["remember"]
    row.remember_box.setChecked(True)
    row.scope_box.setCurrentIndex(row.scope_box.findData("exact_target"))
    ui._window.confirm("remember")
    result = await asyncio.wait_for(task, 1)
    assert result.remember_scope is not None
    assert result.remember_scope.scope == "exact_target"
    assert result.remember_scope.action_id == "excel"


async def test_cancel_pending_is_safe_from_a_worker_thread(ui: Any) -> None:
    task = await _pending(ui, _context("shutdown"), _decision())
    await asyncio.to_thread(ui.cancel_pending)
    await _pump(ui, task.done)
    assert not (await task).confirmed


class _Services:
    def __init__(self) -> None:
        self.enabled = False
        self.enable_calls = 0
        self.disable_calls = 0
        self.exports: list[tuple[Path, bool]] = []
        self.settings: list[tuple[str, dict]] = []
        self.purged: list[str] = []
        self.fail_save = False

    async def enable(self) -> None:
        self.enable_calls += 1
        self.enabled = True

    async def disable(self) -> None:
        self.disable_calls += 1
        self.enabled = False

    async def list_history(self, *, limit: int, query: str | None) -> list:
        assert limit == 1000
        return []

    async def export_history(self, path: Path, *, confirmed: bool) -> int:
        self.exports.append((path, confirmed))
        return 0

    async def get_setting(self, name: str, default: Any) -> Any:
        return default

    async def set_setting(self, name: str, value: dict) -> None:
        if self.fail_save:
            raise RuntimeError("Synthetic storage failure")
        self.settings.append((name, value))

    async def clear_provider_data(self, provider: str) -> None:
        assert not self.enabled
        self.purged.append(provider)


def _submit(coroutine: Any) -> concurrent.futures.Future:
    future: concurrent.futures.Future = concurrent.futures.Future()

    async def run() -> None:
        try:
            future.set_result(await coroutine)
        except Exception as exc:
            future.set_exception(exc)

    asyncio.create_task(run())
    return future


def _button(dialog: Any, label: str) -> Any:
    from PySide6.QtWidgets import QPushButton

    matches = [button for button in dialog.findChildren(QPushButton) if button.text() == label]
    assert len(matches) == 1, f"Expected one button named {label!r}"
    return matches[0]


async def test_onboarding_requires_successful_selftest_and_explicit_consent(ui: Any) -> None:
    from PySide6.QtWidgets import QCheckBox

    services = _Services()
    ui.bind(submit=_submit, broker=services, state=services)

    async def self_test() -> dict:
        return {"ok": True, "checks": [{"ok": True, "name": "synthetic"}]}

    ui._self_test = self_test
    ui.show_onboarding()
    dialog = ui._onboarding
    enable = _button(dialog, "手动启用 Hook")
    test = _button(dialog, "运行本地自检")
    consent = dialog.findChild(QCheckBox)
    assert not enable.isEnabled()
    test.click()
    await _pump(ui, lambda: ui._test_ok)
    assert not ui.ready_to_enable
    assert not enable.isEnabled()
    assert services.enable_calls == 0

    consent.setChecked(True)
    assert ui.ready_to_enable
    assert enable.isEnabled()
    assert services.enable_calls == 0
    enable.click()
    await _pump(ui, lambda: services.enabled)
    assert services.enable_calls == 1
    consent.setChecked(False)
    await _pump(ui, lambda: not services.enabled)
    assert services.disable_calls == 1


async def test_onboarding_failed_or_erroring_selftest_is_retryable(ui: Any) -> None:
    from PySide6.QtWidgets import QCheckBox

    services = _Services()
    ui.bind(submit=_submit, broker=services, state=services)
    attempts = 0

    async def self_test() -> dict:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("Synthetic selftest failure")
        if attempts == 2:
            return {"ok": True, "checks": [{"ok": False, "name": "synthetic"}]}
        return {"ok": True, "checks": [{"ok": True, "name": "synthetic"}]}

    ui._self_test = self_test
    ui.show_onboarding()
    dialog = ui._onboarding
    dialog.findChild(QCheckBox).setChecked(True)
    test = _button(dialog, "运行本地自检")
    enable = _button(dialog, "手动启用 Hook")
    test.click()
    await _pump(ui, test.isEnabled)
    assert attempts == 1
    assert not enable.isEnabled()
    assert not ui.ready_to_enable
    test.click()
    await _pump(ui, test.isEnabled)
    assert attempts == 2
    assert not enable.isEnabled()
    assert not ui.ready_to_enable
    test.click()
    await _pump(ui, lambda: ui.ready_to_enable)
    assert attempts == 3
    assert enable.isEnabled()
    assert services.enable_calls == 0


async def test_history_export_requires_yes_and_passes_explicit_confirmation(
    ui: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    services = _Services()
    ui.bind(submit=_submit, broker=services, state=services)
    destination = tmp_path / "synthetic-history.json"
    picker_calls = []

    def pick(*args: Any, **kwargs: Any) -> tuple[str, str]:
        picker_calls.append(True)
        return str(destination), "JSON (*.json)"

    monkeypatch.setattr(QFileDialog, "getSaveFileName", pick)
    ui.show_history()
    dialog = ui._dialogs[-1]
    _button(dialog, "导出 JSON（明文）").click()
    prompt = ui._dialogs[-1]
    assert isinstance(prompt, QMessageBox)
    assert not services.exports
    assert not picker_calls
    prompt.button(QMessageBox.StandardButton.No).click()
    await asyncio.sleep(0)
    ui._drain()
    assert not services.exports
    assert not picker_calls

    _button(dialog, "导出 JSON（明文）").click()
    prompt = ui._dialogs[-1]
    prompt.button(QMessageBox.StandardButton.Yes).click()
    await _pump(ui, lambda: bool(services.exports))
    assert services.exports == [(destination, True)]
    assert picker_calls == [True]


async def test_settings_rejects_nested_credentials_before_storage(ui: Any) -> None:
    from PySide6.QtWidgets import QLabel, QPlainTextEdit

    from jev_open.config import AppConfig

    services = _Services()
    ui._config = AppConfig()
    ui.bind(submit=_submit, broker=services, state=services)
    ui.show_settings()
    dialog = ui._dialogs[-1]
    save = _button(dialog, "校验并保存，停用 Hook")
    await _pump(ui, save.isEnabled)
    editor = dialog.findChild(QPlainTextEdit)
    editor.setPlainText(json.dumps({"configured_actions": [{"api_key": "synthetic-secret"}]}))
    save.click()
    await asyncio.sleep(0)
    ui._drain()
    assert not services.settings
    assert services.disable_calls == 0
    assert any("凭据" in label.text() for label in dialog.findChildren(QLabel))


async def test_settings_storage_error_can_retry_and_success_disables_hook(ui: Any) -> None:
    from PySide6.QtWidgets import QPlainTextEdit

    from jev_open.config import AppConfig

    services = _Services()
    services.enabled = True
    services.fail_save = True
    ui._config = AppConfig()
    ui.bind(submit=_submit, broker=services, state=services)
    ui.show_settings()
    dialog = ui._dialogs[-1]
    save = _button(dialog, "校验并保存，停用 Hook")
    await _pump(ui, save.isEnabled)
    dialog.findChild(QPlainTextEdit).setPlainText(json.dumps({"enabled_providers": ["qq"]}))
    save.click()
    await _pump(ui, save.isEnabled)
    assert not services.settings
    assert services.enabled
    services.fail_save = False
    save.click()
    await _pump(ui, lambda: bool(services.settings) and not services.enabled)
    assert services.settings == [("application_config", {"enabled_providers": ["qq"]})]
    assert not ui.ready_to_enable


async def test_provider_purge_requires_selection_and_yes_then_disables_before_delete(
    ui: Any,
) -> None:
    from PySide6.QtWidgets import QComboBox, QMessageBox

    services = _Services()
    services.enabled = True
    ui.bind(submit=_submit, broker=services, state=services)
    ui.show_settings()
    dialog = ui._dialogs[-1]
    purge = _button(dialog, "清理所选 IM 的来源与偏好")
    picker = dialog.findChild(QComboBox, "purge_provider")
    assert not purge.isEnabled()
    picker.setCurrentIndex(picker.findData("qq"))
    assert purge.isEnabled()
    purge.click()
    prompt = ui._dialogs[-1]
    assert isinstance(prompt, QMessageBox)
    assert not services.purged
    assert services.enabled
    prompt.button(QMessageBox.StandardButton.No).click()
    await asyncio.sleep(0)
    assert not services.purged

    purge.click()
    prompt = ui._dialogs[-1]
    prompt.button(QMessageBox.StandardButton.Yes).click()
    await _pump(ui, lambda: bool(services.purged))
    assert services.purged == ["qq"]
    assert services.disable_calls == 1
    assert ui._restart_required
    assert not ui.ready_to_enable


async def test_unknown_launch_outcome_is_not_presented_as_safe_retry(ui: Any) -> None:
    task = await _pending(ui, _context("unknown-launch"), _decision())
    ui._window.confirm("unknown-launch")
    assert (await task).confirmed
    await ui.notify_launch_result("unknown-launch", False, "Launch outcome is unknown; check app")
    ui._drain()
    row = ui._window.rows["unknown-launch"]
    assert "未知" in row.status_label.text()
    assert "改选" not in row.status_label.text()
    assert not row.open_button.isEnabled()


async def test_onboarding_displays_provider_limits_without_blocking_path_selftest(ui: Any) -> None:
    from PySide6.QtWidgets import QCheckBox, QLabel, QPlainTextEdit

    services = _Services()
    ui.bind(submit=_submit, broker=services, state=services)
    provider_warning = "QQ transport is not connected; path-only routing remains available."

    async def self_test() -> dict:
        return {
            "ok": True,
            "checks": [{"ok": True, "name": "path-only components"}],
            "warnings": [provider_warning],
            "live_jev_tested": False,
        }

    ui._self_test = self_test
    ui.show_onboarding()
    dialog = ui._onboarding
    description = "\n".join(label.text() for label in dialog.findChildren(QLabel))
    assert "QQ 未接入可信适配器与认证传输" in description
    assert "微信尚未实现可见聊天读取器" in description
    assert "启用 IM 配置不会自动获得聊天" in description
    dialog.findChild(QCheckBox).setChecked(True)
    _button(dialog, "运行本地自检").click()
    await _pump(ui, lambda: ui._test_ok)
    output = dialog.findChild(QPlainTextEdit).toPlainText()
    assert provider_warning in output
    assert "不代表已通过实测" in output
    assert "提示 | " + provider_warning in output
    assert "通过 | " + provider_warning not in output
    assert ui.ready_to_enable
    assert _button(dialog, "手动启用 Hook").isEnabled()
    assert services.enable_calls == 0
