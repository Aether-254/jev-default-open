from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import queue
import sys
import threading
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from jev_open.decision.jev import DEFAULT_SCENES
from jev_open.domain import ConfirmationResult, ContextEnvelope, OpenDecision, OpenRequest, OpenVerb
from jev_open.single_instance import MAX_MESSAGE_BYTES, SERVER_NAME

SCENE_NAMES = {
    "personal": "个人",
    "work_general": "通用工作",
    "finance": "财务",
    "data_analysis": "数据分析",
    "software_development": "软件开发",
    "creative_media": "创作与媒体",
    "learning": "学习",
    "unknown": "未知",
}
SCOPE_NAMES = {
    "exact_target": "当前目标",
    "conversation": "当前会话与文件类型 / 域名",
    "path_label": "当前目录标签与文件类型",
    "path_prefix": "当前目录与文件类型",
    "provider": "当前 IM 账号与类型",
    "extension_or_mime": "文件类型 / MIME",
    "url_domain": "URL 域名",
    "global": "同一目标类别",
}
SETTINGS_KEYS = {
    "profile_labels",
    "path_labels",
    "enabled_providers",
    "qq_bridge_mode",
    "qq_runtime_path",
    "qq_allowed_versions",
    "qq_allowed_sha256",
    "scenes",
    "configured_actions",
    "conversation_labels",
    "mode",
    "standard_deadline_ms",
    "demo_deadline_ms",
    "context_deadline_ms",
}


def _preselected(
    choice: str | None, probabilities: dict[str, float], *, explicit: bool = False,
) -> str | None:
    if not choice:
        return None
    if explicit:
        return choice
    ranked = sorted(probabilities.items(), key=lambda item: (-item[1], item[0]))
    if not ranked or ranked[0][0] != choice or ranked[0][1] < 0.70:
        return None
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
    return choice if ranked[0][1] - runner_up >= 0.15 - 1e-9 else None


def _complete(
    future: concurrent.futures.Future[ConfirmationResult], result: ConfirmationResult,
) -> None:
    try:
        future.set_result(result)
    except concurrent.futures.InvalidStateError:
        pass


@dataclass
class DecisionRow:
    context: ContextEnvelope
    decision: OpenDecision
    future: concurrent.futures.Future[ConfirmationResult]
    index: int
    selected_box: QCheckBox
    action_box: QComboBox
    scene_box: QComboBox
    remember_box: QCheckBox
    scope_box: QComboBox
    open_button: QPushButton
    cancel_button: QPushButton
    status_label: QLabel


class BatchWindow(QDialog):
    """One modeless confirmation surface; each row owns one broker future."""

    def __init__(self, owner: QtUserInterfaceModule) -> None:
        super().__init__()
        self.owner = owner
        self.rows: dict[str, DecisionRow] = {}
        self.setWindowTitle("Jev Default Open · 打开方式确认")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        self.resize(1450, 540)
        layout = QVBoxLayout(self)
        if owner.demo_mode:
            demo = QLabel("离线演示 · 合成聊天与模型结果 · 不调用 Jev，不拦截、不启动真实应用")
            demo.setStyleSheet("background: #fff0c2; color: #573b00; padding: 9px;")
            layout.addWidget(demo)
        heading = QLabel("根据聊天场景与路径，选择合适的应用和 Profile")
        heading.setStyleSheet("font-size: 18px; font-weight: 600; padding: 6px 0;")
        layout.addWidget(heading)
        note = QLabel(
            "每项均需确认。低概率或接近的结果不会预选；修改场景不会替换应用。"
            "关闭窗口 / Esc 将取消未确认项目。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            ["批量", "目标 / 来源", "上下文", "场景", "打开动作（按概率）", "记住范围",
             "状态", "操作", "证据"]
        )
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for index, width in enumerate((44, 235, 150, 145, 220, 180, 105, 145, 58)):
            self.table.setColumnWidth(index, width)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        tools = QHBoxLayout()
        self.summary = QLabel("等待打开请求")
        tools.addWidget(self.summary, 1)
        self.bulk_actions = QComboBox()
        self.bulk_actions.setMinimumWidth(210)
        self.bulk_actions.setPlaceholderText("选择要批量应用的动作")
        tools.addWidget(self.bulk_actions)
        apply_button = QPushButton("应用到勾选项")
        apply_button.clicked.connect(
            lambda: self.apply_to_selected(self.bulk_actions.currentData())
        )
        tools.addWidget(apply_button)
        open_button = QPushButton("打开勾选且已选择的项目")
        open_button.clicked.connect(self.confirm_selected)
        tools.addWidget(open_button)
        cancel_button = QPushButton("取消全部未确认项")
        cancel_button.clicked.connect(self.cancel_all)
        tools.addWidget(cancel_button)
        layout.addLayout(tools)

    def add_request(
        self, context: ContextEnvelope, decision: OpenDecision,
        future: concurrent.futures.Future[ConfirmationResult],
    ) -> None:
        request_id = context.request.request_id
        previous = self.rows.get(request_id)
        if previous:
            _complete(previous.future, ConfirmationResult(confirmed=False))
            index = previous.index
        else:
            self._prune_completed()
            index = self.table.rowCount()
            self.table.insertRow(index)
        selected = QCheckBox()
        selected.setChecked(True)
        self.table.setCellWidget(index, 0, selected)
        target = QTableWidgetItem(f"{context.request.target}\n{context.source_application}")
        target.setToolTip(context.request.target)
        self.table.setItem(index, 1, target)
        chat = context.chat_context
        context_text = (
            f"{chat.provider} · {chat.conversation_title or '未命名会话'}\n"
            f"{chat.acquisition} · {chat.confidence:.0%}"
            if chat else "聊天上下文不可用"
        )
        if context.path_labels:
            context_text += "\n" + "、".join(context.path_labels)
        context_item = QTableWidgetItem(context_text)
        if chat:
            context_item.setToolTip(
                f"账号标识：{chat.account_id_hash}\n会话：{chat.conversation_id or '未知'}"
            )
        self.table.setItem(index, 2, context_item)

        scene_box = QComboBox()
        scene_box.addItem("未评估 / 请手动选择", None)
        scene_ids = list(dict.fromkeys([*self.owner.scenes, *decision.scene.probabilities]))
        for scene_id in scene_ids:
            probability = decision.scene.probabilities.get(scene_id)
            suffix = f" · {probability:.0%}" if probability is not None else ""
            scene_box.addItem(SCENE_NAMES.get(scene_id, scene_id) + suffix, scene_id)
            scene_box.setItemData(
                scene_box.count() - 1, self.owner.scenes.get(scene_id, ""),
                Qt.ItemDataRole.ToolTipRole,
            )
        scene_box.setToolTip(_confidence_text("场景", decision.scene.confidence))
        scene_choice = _preselected(decision.scene.scene_id, decision.scene.probabilities)
        if scene_choice and decision.scene.evaluated:
            scene_box.setCurrentIndex(max(0, scene_box.findData(scene_choice)))
        self.table.setCellWidget(index, 3, scene_box)

        action_box = QComboBox()
        action_box.addItem("请选择打开动作", None)
        ranked = sorted(context.open_actions, key=lambda action: (
            -decision.probabilities.get(action.action_id, -1.0), action.display_name,
        ))
        for rank, action in enumerate(ranked):
            probability = decision.probabilities.get(action.action_id)
            suffix = f" · {probability:.1%}" if probability is not None else ""
            prefix = f"{rank + 1}. " if rank < 3 else ""
            action_box.addItem(prefix + action.display_name + suffix, action.action_id)
            action_box.setItemData(
                action_box.count() - 1, action.capability_description, Qt.ItemDataRole.ToolTipRole,
            )
        choice = _preselected(
            decision.action_id, decision.probabilities, explicit=decision.source == "preference",
        )
        if choice:
            action_box.setCurrentIndex(max(0, action_box.findData(choice)))
        action_box.setToolTip(_confidence_text("动作", decision.confidence))
        self.table.setCellWidget(index, 4, action_box)

        remember = QCheckBox("记住")
        scope = QComboBox()
        for value, label in SCOPE_NAMES.items():
            if value in ("conversation", "provider") and chat is None:
                continue
            if value == "conversation" and (not chat or not chat.conversation_id):
                continue
            if value == "path_label" and not context.path_labels:
                continue
            if value in ("path_prefix", "path_label", "extension_or_mime"):
                if context.target.kind != "file":
                    continue
            if value == "url_domain" and (
                context.target.kind != "url" or not context.target.host
            ):
                continue
            if value == "path_label":
                for path_label in context.path_labels:
                    scope.addItem(f"目录标签「{path_label}」与类型", value)
                    scope.setItemData(scope.count() - 1, path_label, Qt.ItemDataRole.UserRole + 1)
                continue
            scope.addItem(label, value)
        scope.setEnabled(False)
        remember.toggled.connect(scope.setEnabled)
        memory_cell = QWidget()
        memory_layout = QVBoxLayout(memory_cell)
        memory_layout.setContentsMargins(3, 3, 3, 3)
        memory_layout.addWidget(remember)
        memory_layout.addWidget(scope)
        self.table.setCellWidget(index, 5, memory_cell)
        status = QLabel("启动失败，请改选" if any(
            warning.startswith("launch_failed") for warning in decision.warnings
        ) else "等待确认")
        status.setWordWrap(True)
        status.setToolTip("\n".join((
            _confidence_text("动作", decision.confidence),
            _confidence_text("场景", decision.scene.confidence), *decision.warnings,
        )))
        self.table.setCellWidget(index, 6, status)
        open_button = QPushButton("打开")
        open_button.setEnabled(action_box.currentData() is not None)
        cancel_button = QPushButton("取消")
        controls = QWidget()
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(2, 2, 2, 2)
        controls_layout.addWidget(open_button)
        controls_layout.addWidget(cancel_button)
        self.table.setCellWidget(index, 7, controls)
        evidence_button = QPushButton("查看")
        evidence_button.clicked.connect(lambda: self.owner.show_evidence(context, decision))
        self.table.setCellWidget(index, 8, evidence_button)
        row = DecisionRow(
            context, decision, future, index, selected, action_box, scene_box, remember, scope,
            open_button, cancel_button, status,
        )
        self.rows[request_id] = row
        action_box.currentIndexChanged.connect(
            lambda: open_button.setEnabled(
                not future.done() and action_box.currentData() is not None
            )
        )
        open_button.clicked.connect(lambda: self.confirm(request_id))
        cancel_button.clicked.connect(lambda: self.cancel(request_id))
        self.table.setRowHeight(index, 92)
        self._refresh_summary()
        self._refresh_bulk_actions()

    def confirm(self, request_id: str) -> None:
        row = self.rows[request_id]
        action_id = row.action_box.currentData()
        if row.future.done() or not action_id:
            return
        if action_id not in {action.action_id for action in row.context.open_actions}:
            row.status_label.setText("动作不兼容")
            return
        preference = None
        if row.remember_box.isChecked():
            try:
                from jev_open.persistence.preferences import create_preference

                preference = create_preference(
                    row.context, action_id, row.scope_box.currentData(),
                    label=row.scope_box.currentData(Qt.ItemDataRole.UserRole + 1),
                )
            except (ValueError, ImportError) as exc:
                row.status_label.setText("记忆范围不可用")
                row.status_label.setToolTip(str(exc))
                return
        _complete(row.future, ConfirmationResult(
            confirmed=True, action_id=action_id, scene_id=row.scene_box.currentData(),
            remember_scope=preference,
        ))
        self._disable_row(row, "已确认，等待启动")

    def cancel(self, request_id: str) -> None:
        row = self.rows[request_id]
        if not row.future.done():
            _complete(row.future, ConfirmationResult(confirmed=False))
            self._disable_row(row, "已取消")

    def cancel_all(self) -> None:
        for request_id in tuple(self.rows):
            self.cancel(request_id)

    def confirm_selected(self) -> None:
        for request_id, row in tuple(self.rows.items()):
            if row.selected_box.isChecked():
                self.confirm(request_id)

    def apply_to_selected(self, action_id: str | None) -> None:
        if not action_id:
            return
        skipped = 0
        for row in self.rows.values():
            if row.future.done() or not row.selected_box.isChecked():
                continue
            index = row.action_box.findData(action_id)
            if index > 0:
                row.action_box.setCurrentIndex(index)
            else:
                skipped += 1
        if skipped:
            self.summary.setText(f"已应用；{skipped} 项不支持该动作，保持原选择")

    def launch_result(self, request_id: str, success: bool, message: str) -> None:
        row = self.rows.get(request_id)
        if row:
            if not success and "outcome is unknown" in message.casefold():
                status = "启动结果未知，请检查应用"
            else:
                status = "已启动" if success else "启动失败，请改选"
            row.status_label.setText(status)
            row.status_label.setToolTip(message)

    def _disable_row(self, row: DecisionRow, status: str) -> None:
        for widget in (row.selected_box, row.action_box, row.scene_box, row.remember_box,
                       row.scope_box, row.open_button, row.cancel_button):
            widget.setEnabled(False)
        row.status_label.setText(status)
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        pending = sum(not row.future.done() for row in self.rows.values())
        self.summary.setText(f"{pending} 项等待确认 / 本批 {len(self.rows)} 项")

    def _prune_completed(self) -> None:
        if len(self.rows) < 200:
            return
        for request_id, row in tuple(self.rows.items()):
            if row.future.done():
                self.table.removeRow(row.index)
                del self.rows[request_id]
                for retained in self.rows.values():
                    if retained.index > row.index:
                        retained.index -= 1
            if len(self.rows) < 100:
                break

    def _refresh_bulk_actions(self) -> None:
        current = self.bulk_actions.currentData()
        actions = {
            action.action_id: action.display_name
            for row in self.rows.values() if not row.future.done()
            for action in row.context.open_actions
        }
        self.bulk_actions.clear()
        self.bulk_actions.addItem("批量选择动作", None)
        for action_id, name in sorted(actions.items(), key=lambda item: item[1]):
            self.bulk_actions.addItem(name, action_id)
        self.bulk_actions.setCurrentIndex(max(0, self.bulk_actions.findData(current)))

    def reject(self) -> None:
        self.cancel_all()
        self.owner.cancel_pending()
        super().reject()

    def closeEvent(self, event: Any) -> None:
        self.cancel_all()
        self.owner.cancel_pending()
        super().closeEvent(event)


class QtUserInterfaceModule:
    def __init__(
        self, *, self_test: Callable[[], Coroutine[Any, Any, dict[str, Any]]] | None = None,
        scenes: dict[str, str] | None = None, config: Any = None, collection_ms: int = 500,
        demo_mode: bool = False,
    ) -> None:
        self.scenes = scenes or DEFAULT_SCENES
        self.demo_mode = demo_mode
        self._self_test = self_test
        self._config = config
        self._collection_ms = max(0, collection_ms)
        self._queue: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self._futures: set[concurrent.futures.Future[ConfirmationResult]] = set()
        self._future_lock = threading.Lock()
        self._application: QApplication | None = None
        self._window: BatchWindow | None = None
        self._timer: QTimer | None = None
        self._collection_timer: QTimer | None = None
        self._tray: QSystemTrayIcon | None = None
        self._dialogs: list[QDialog] = []
        self._submit: Callable | None = None
        self._broker: Any = None
        self._state: Any = None
        self._ready_check: Callable | None = None
        self._test_ok = False
        self._hook_available = False
        self._consent = False
        self._restart_required = False
        self._onboarding: QDialog | None = None
        self._local_server: QLocalServer | None = None
        self._local_buffers: dict[Any, bytearray] = {}
        self._enable_action: Any = None
        self._disable_action: Any = None

    @property
    def ready_to_enable(self) -> bool:
        return (
            self._test_ok
            and self._hook_available
            and self._consent
            and not self._restart_required
        )

    def bind(self, *, submit: Callable, broker: Any, state: Any, ready_check: Any = None) -> None:
        self._submit = submit
        self._broker = broker
        self._state = state
        self._ready_check = ready_check

    def _initialize(self) -> None:
        if self._application is not None:
            return
        self._application = QApplication.instance() or QApplication([])
        self._application.setApplicationName("Jev Default Open")
        self._application.setQuitOnLastWindowClosed(False)
        self._application.setWindowIcon(_application_icon())
        self._window = BatchWindow(self)
        self._timer = QTimer(self._window)
        self._timer.timeout.connect(self._drain)
        self._timer.start(40)
        self._collection_timer = QTimer(self._window)
        self._collection_timer.setSingleShot(True)
        self._collection_timer.timeout.connect(self.show_pending)
        self._start_local_server()

    def _start_local_server(self) -> None:
        assert self._application is not None
        server = QLocalServer(self._application)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not server.listen(SERVER_NAME):
            QLocalServer.removeServer(SERVER_NAME)
            if not server.listen(SERVER_NAME):
                return
        server.newConnection.connect(self._accept_local_connections)
        self._local_server = server

    def _accept_local_connections(self) -> None:
        assert self._local_server is not None
        while self._local_server.hasPendingConnections():
            socket = self._local_server.nextPendingConnection()
            self._local_buffers[socket] = bytearray()
            socket.readyRead.connect(lambda current=socket: self._read_local_target(current))
            socket.disconnected.connect(
                lambda current=socket: self._local_buffers.pop(current, None)
            )

    def _read_local_target(self, socket: Any) -> None:
        buffer = self._local_buffers.get(socket)
        if buffer is None:
            return
        buffer.extend(bytes(socket.readAll()))
        if len(buffer) > MAX_MESSAGE_BYTES:
            socket.disconnectFromServer()
            return
        if b"\n" not in buffer:
            return
        raw, _, _ = bytes(buffer).partition(b"\n")
        try:
            payload = json.loads(raw)
            target = payload["target"]
            if not isinstance(target, str) or not target or len(target) > 16_384:
                raise ValueError
            request = OpenRequest(
                request_id=uuid4().hex,
                source_pid=os.getpid(),
                source_executable=Path(sys.executable).resolve(),
                verb=OpenVerb.OPEN,
                target=target,
                captured_at=datetime.now(UTC),
            )
            if self._broker is None or self._submit is None:
                raise RuntimeError
            self._submit(self._broker.handle_request(request))
            socket.write(b"OK\n")
            socket.flush()
        except (KeyError, TypeError, ValueError, RuntimeError, json.JSONDecodeError):
            socket.write(b"ERROR\n")
            socket.flush()
        socket.disconnectFromServer()

    def run(self) -> int:
        self._initialize()
        assert self._application is not None
        self._tray = QSystemTrayIcon(_application_icon(), self._application)
        self._tray.setToolTip("Jev Default Open · 拦截默认停用")
        menu = QMenu()
        self._enable_action = menu.addAction("启用 Hook", self._enable)
        self._disable_action = menu.addAction("停用 Hook", self._disable)
        menu.addAction("待处理打开请求", self.show_pending)
        menu.addSeparator()
        menu.addAction("引导 / 本地自检", self.show_onboarding)
        menu.addAction("Profile / 路径 / 场景设置", self.show_settings)
        menu.addAction("偏好规则", self.show_preferences)
        menu.addAction("历史记录", self.show_history)
        menu.addSeparator()
        menu.addAction("安全退出", self._exit)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(lambda reason: self.show_pending()
                                    if reason == QSystemTrayIcon.ActivationReason.DoubleClick
                                    else None)
        self._tray.show()
        self._refresh_controls()
        self.show_onboarding()
        try:
            return self._application.exec()
        finally:
            self.cancel_pending()
            self._timer.stop()
            self._collection_timer.stop()
            self._tray.hide()
            if self._local_server is not None:
                self._local_server.close()
                QLocalServer.removeServer(SERVER_NAME)

    async def enqueue(self, context: ContextEnvelope, decision: OpenDecision) -> ConfirmationResult:
        future: concurrent.futures.Future[ConfirmationResult] = concurrent.futures.Future()
        with self._future_lock:
            self._futures.add(future)
        self._queue.put(("request", context, decision, future))
        try:
            return await asyncio.wrap_future(future)
        finally:
            with self._future_lock:
                self._futures.discard(future)
            if future.cancelled():
                self._queue.put(("settled", context.request.request_id, future))

    def cancel_pending(self) -> None:
        with self._future_lock:
            futures = tuple(self._futures)
        for future in futures:
            _complete(future, ConfirmationResult(confirmed=False))
        self._queue.put(("cancelled",))

    async def show_recovery_report(self) -> None:
        self._queue.put(("recovery",))

    async def notify_launch_result(
        self, request_id: str, success: bool, message: str = "",
    ) -> None:
        self._queue.put(("launch", request_id, success, message))

    def _drain(self) -> None:
        assert self._window is not None
        for _ in range(100):
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            kind = item[0]
            if kind == "request":
                _, context, decision, future = item
                if future.done():
                    continue
                self._window.add_request(context, decision, future)
                if not self._window.isVisible() and not self._collection_timer.isActive():
                    self._collection_timer.start(self._collection_ms)
            elif kind == "launch":
                self._window.launch_result(*item[1:])
            elif kind == "callback":
                callback, error_callback, future = item[1:]
                try:
                    result = future.result()
                except Exception as exc:
                    self._message("操作未完成", f"{type(exc).__name__}；请检查配置和诊断结果。")
                    if error_callback:
                        error_callback(exc)
                else:
                    callback(result)
            elif kind == "recovery":
                self._test_ok = False
                self._message("恢复模式", "上次运行未正常结束。Hook 保持停用，请重新执行本地自检。")
            elif kind == "cancelled":
                self._collection_timer.stop()
                for row in self._window.rows.values():
                    if row.future.done() and (
                        row.future.cancelled() or not row.future.result().confirmed
                    ):
                        self._window._disable_row(row, "已取消")
            elif kind == "settled":
                row = self._window.rows.get(item[1])
                if row and row.future is item[2]:
                    self._window._disable_row(row, "已取消")
        self._refresh_controls()

    def _run_async(
        self, coroutine: Coroutine, callback: Callable | None = None,
        error_callback: Callable | None = None,
    ) -> None:
        if self._submit is None:
            coroutine.close()
            self._message("服务未连接", "后台服务尚未连接，无法执行此操作。")
            if error_callback:
                error_callback(RuntimeError("Background service is not connected"))
            return
        try:
            future = self._submit(coroutine)
        except Exception as exc:
            coroutine.close()
            self._message("后台服务未响应", f"{type(exc).__name__}；请重新启动应用。")
            if error_callback:
                error_callback(exc)
            return
        future.add_done_callback(lambda completed: self._queue.put(
            ("callback", callback or (lambda _: None), error_callback, completed)
        ))

    def show_pending(self) -> None:
        if self._window is None:
            return
        if any(not row.future.done() for row in self._window.rows.values()):
            self._window.show()
            self._window.raise_()
            self._window.activateWindow()
        elif not self._window.rows:
            self.show_onboarding()

    def _refresh_controls(self) -> None:
        enabled = bool(getattr(self._broker, "enabled", False))
        if self._enable_action is not None:
            self._enable_action.setEnabled(self.ready_to_enable and not enabled)
            self._disable_action.setEnabled(enabled)
        if self._tray is not None:
            self._tray.setToolTip(
                "Jev Default Open · " + ("拦截已启用" if enabled else "拦截已停用")
            )

    def _enable(self) -> None:
        if not self.ready_to_enable:
            self.show_onboarding()
            return
        if self._broker:
            self._run_async(self._broker.enable(), lambda _: self._refresh_controls())

    def _disable(self) -> None:
        self.cancel_pending()
        if self._broker:
            self._run_async(self._broker.disable(), lambda _: self._refresh_controls())

    def _exit(self) -> None:
        self.cancel_pending()
        if self._broker and self._submit:
            self._run_async(self._broker.disable(), lambda _: self._application.quit())
        elif self._application:
            self._application.quit()

    def show_onboarding(self) -> None:
        if self._onboarding is not None:
            self._onboarding.show()
            self._onboarding.raise_()
            return
        dialog = QDialog()
        dialog.setWindowTitle("Jev Default Open · 首次引导与自检")
        dialog.resize(660, 520)
        layout = QVBoxLayout(dialog)
        title = QLabel("先检查，再由你手动启用")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        layout.addWidget(title)
        description = QLabel(
            "应用以当前用户权限运行，不会自动提权或自动启用 Hook。\n\n"
            "启用后，参与决策的完整文件路径、完整 URL，以及已授权 IM 的聊天摘录、"
            "会话信息与目录标签可能发送给 Jev，并写入本机 SQLCipher 加密历史。"
            "导出的 JSON 则为明文。请先在设置中选择允许的 IM、标注 Profile 和路径。\n\n"
            "当前 QQ 未接入可信适配器与认证传输；微信尚未实现可见聊天读取器。"
            "启用 IM 配置不会自动获得聊天，当前实际客户端请求只能依据"
            "目标路径 / URL 等信息决策。\n\n"
            "本地自检不调用 Jev，也不读取真实聊天；自检通过不表示 IM 接入成功。"
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        settings = QPushButton("配置 Profile、路径、场景与 IM")
        settings.clicked.connect(self.show_settings)
        layout.addWidget(settings)
        output = QPlainTextEdit()
        output.setReadOnly(True)
        output.setPlaceholderText("尚未运行本地自检")
        layout.addWidget(output, 1)
        consent = QCheckBox("我理解上述传输与存储范围，并同意按我的配置用于 Jev 决策")
        consent.setChecked(self._consent)
        consent.toggled.connect(self._set_consent)
        layout.addWidget(consent)
        buttons = QHBoxLayout()
        test_button = QPushButton("运行本地自检")
        enable_button = QPushButton("手动启用 Hook")
        enable_button.setEnabled(self.ready_to_enable)
        close_button = QPushButton("保持停用")
        buttons.addWidget(test_button)
        buttons.addWidget(enable_button)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        def tested(result: dict[str, Any]) -> None:
            checks = result.get("checks", [])
            self._test_ok = bool(result.get("ok")) and all(
                bool(check.get("ok")) for check in checks
            )
            # Synthetic/custom self-tests predating this capability field keep
            # their existing behavior. Production diagnostics always provide it.
            self._hook_available = bool(result.get("hook_available", True))
            output.setPlainText("\n".join(
                f"{'通过' if check.get('ok') else '失败'} | {check.get('name', '')}"
                f" | {check.get('detail', '')}" for check in checks
            ) or ("通过" if self._test_ok else "自检未通过"))
            warnings = result.get("warnings", [])
            if warnings:
                output.appendPlainText("\n未接入 / 限制提示（不代表已通过实测）：")
                for warning in warnings:
                    output.appendPlainText(f"提示 | {warning}")
            if self._test_ok and not self._hook_available:
                output.appendPlainText(
                    "\n提示 | 当前发行包未启用实验性全局 Hook；可使用 --match 进行 App 匹配。"
                )
            if self._restart_required:
                output.appendPlainText("配置已修改；必须先重启应用，自检不能跳过重启。")
            enable_button.setEnabled(self.ready_to_enable)
            test_button.setEnabled(True)
            self._refresh_controls()

        def start_test() -> None:
            self._test_ok = False
            enable_button.setEnabled(False)
            if self._self_test is None:
                output.setPlainText("自检未连接；为避免不完整接管，Hook 保持停用。")
                return
            test_button.setEnabled(False)
            output.setPlainText("正在检查本地组件；不会发起云请求……")
            self._run_async(
                self._self_test(), tested, lambda _: test_button.setEnabled(True),
            )

        test_button.clicked.connect(start_test)
        consent.toggled.connect(lambda _: enable_button.setEnabled(self.ready_to_enable))
        enable_button.clicked.connect(self._enable)
        close_button.clicked.connect(dialog.hide)
        self._onboarding = dialog
        self._dialogs.append(dialog)
        dialog.show()

    def _set_consent(self, value: bool) -> None:
        self._consent = value
        if not value and getattr(self._broker, "enabled", False):
            self._disable()
        self._refresh_controls()

    def show_evidence(self, context: ContextEnvelope, decision: OpenDecision) -> None:
        self._show_json("本次证据与模型结果", {
            "context": context.model_dump(mode="json"),
            "decision": decision.model_dump(mode="json"),
        })

    def _show_json(self, title: str, value: Any) -> None:
        dialog = QDialog(self._window)
        dialog.setWindowTitle(title)
        dialog.resize(760, 580)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit(json.dumps(value, ensure_ascii=False, indent=2, default=str))
        text.setReadOnly(True)
        layout.addWidget(text)
        close = QPushButton("关闭")
        close.clicked.connect(dialog.close)
        layout.addWidget(close)
        self._dialogs.append(dialog)
        dialog.show()

    def _message(self, title: str, text: str) -> None:
        box = QMessageBox(self._window)
        box.setWindowTitle(title)
        box.setText(text)
        box.setIcon(QMessageBox.Icon.Information)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        self._dialogs.append(box)
        box.open()

    def _ask(self, title: str, text: str, accepted: Callable[[], None]) -> None:
        box = QMessageBox(self._window)
        box.setWindowTitle(title)
        box.setText(text)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        box.finished.connect(lambda result: accepted()
                             if result == QMessageBox.StandardButton.Yes else None)
        self._dialogs.append(box)
        box.open()

    def show_history(self) -> None:
        if self._state is None:
            self._message("历史不可用", "加密状态服务尚未连接。")
            return
        dialog = QDialog(self._window)
        dialog.setWindowTitle("Jev Default Open · 最近 1000 条加密历史")
        dialog.resize(1000, 560)
        layout = QVBoxLayout(dialog)
        query = QLineEdit()
        query.setPlaceholderText("搜索目标、场景、会话或动作")
        layout.addWidget(query)
        table = QTableWidget(0, 5)
        table.setHorizontalHeaderLabels(["时间", "目标", "场景", "最终动作", "状态"])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(table)
        records: list[dict[str, Any]] = []

        def loaded(items: list[dict[str, Any]]) -> None:
            records[:] = items
            table.setRowCount(len(items))
            for row, item in enumerate(items):
                context = item.get("context", {})
                values = [
                    item.get("created_at"), context.get("request", {}).get("target"),
                    item.get("final_scene_id"), item.get("final_action_id"), item.get("status"),
                ]
                for column, value in enumerate(values):
                    table.setItem(row, column, QTableWidgetItem(str(value or "")))

        def reload_records() -> None:
            self._run_async(
                self._state.list_history(limit=1000, query=query.text() or None), loaded,
            )

        def export() -> None:
            def confirmed() -> None:
                path, _ = QFileDialog.getSaveFileName(
                    dialog, "导出明文历史", "jev-open-history.json", "JSON (*.json)",
                )
                if path:
                    self._run_async(
                        self._state.export_history(Path(path), confirmed=True),
                        lambda count: self._message("导出完成", f"已导出 {count} 条明文记录。"),
                    )
            self._ask("明文导出确认", "导出包含完整路径、URL 与聊天摘录，任何能读取文件的人"
                      "都可以查看。是否继续？", confirmed)

        table.cellDoubleClicked.connect(lambda row, _: self._show_json("历史详情", records[row]))
        buttons = QHBoxLayout()
        refresh = QPushButton("搜索 / 刷新")
        refresh.clicked.connect(reload_records)
        query.returnPressed.connect(reload_records)
        export_button = QPushButton("导出 JSON（明文）")
        export_button.clicked.connect(export)
        clear = QPushButton("清空历史与缓存")
        clear_history = getattr(self._broker, "clear_history", None)
        if clear_history is None:
            def clear_history() -> Coroutine:
                return self._state.clear_history()
        clear.clicked.connect(lambda: self._ask(
            "清空历史", "此操作清空本机历史与模型缓存，不删除偏好与独立文件来源索引。"
            "是否继续？",
            lambda: self._run_async(clear_history(), lambda _: reload_records()),
        ))
        buttons.addWidget(refresh)
        buttons.addStretch()
        buttons.addWidget(export_button)
        buttons.addWidget(clear)
        layout.addLayout(buttons)
        self._dialogs.append(dialog)
        reload_records()
        dialog.show()

    def show_preferences(self) -> None:
        if self._state is None:
            return
        dialog = QDialog(self._window)
        dialog.setWindowTitle("Jev Default Open · 显式偏好规则")
        dialog.resize(860, 470)
        layout = QVBoxLayout(dialog)
        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(["范围", "匹配条件", "动作"])
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(table)
        records: list[Any] = []

        def loaded(rules: list[Any]) -> None:
            records[:] = rules
            table.setRowCount(len(rules))
            for index, rule in enumerate(rules):
                for column, value in enumerate((SCOPE_NAMES.get(rule.scope, rule.scope),
                                                json.dumps(rule.selector, ensure_ascii=False),
                                                rule.action_id)):
                    table.setItem(index, column, QTableWidgetItem(value))

        def refresh() -> None:
            self._run_async(self._state.list_preferences(), loaded)

        def delete() -> None:
            index = table.currentRow()
            if 0 <= index < len(records):
                self._ask("删除偏好", "删除选中的显式偏好规则？", lambda: self._run_async(
                    self._state.delete_preference(records[index].rule_id), lambda _: refresh(),
                ))

        remove = QPushButton("删除选中规则")
        remove.clicked.connect(delete)
        layout.addWidget(remove)
        self._dialogs.append(dialog)
        refresh()
        dialog.show()

    def show_settings(self) -> None:
        if self._state is None:
            self._message("设置不可用", "加密状态服务尚未连接。")
            return
        dialog = QDialog(self._window)
        dialog.setWindowTitle("Jev Default Open · 应用设置（重启生效）")
        dialog.resize(830, 620)
        layout = QVBoxLayout(dialog)
        label = QLabel(
            "编辑 JSON：profile_labels 标注个人 / 工作 Profile；path_labels 标注目录；"
            "conversation_labels 标注会话；enabled_providers 控制允许读取的 IM；"
            "qq_bridge_mode 只能是 disabled 或 fixture，不能接通真实 QQ；"
            "qq_runtime_path 仅用于只读 QQ.exe 版本指纹，qq_allowed_versions / qq_allowed_sha256"
            " 必须显式允许版本；这些字段不会接通聊天；"
            "scenes 配置场景。\n"
            "不自动推断 Profile 身份，不在此保存 API Key。保存后 Hook 停用，重启后生效。"
        )
        label.setWordWrap(True)
        layout.addWidget(label)
        editor = QPlainTextEdit()
        layout.addWidget(editor)
        status = QLabel()
        status.setWordWrap(True)
        layout.addWidget(status)
        save = QPushButton("校验并保存，停用 Hook")
        save.setEnabled(False)
        layout.addWidget(save)
        privacy_controls = QHBoxLayout()
        provider_box = QComboBox()
        provider_box.setObjectName("purge_provider")
        provider_box.addItem("选择要清理的 IM", None)
        for provider, name in (
            ("qq", "QQ"), ("wechat", "微信"), ("feishu", "飞书"),
            ("slack", "Slack"), ("dingtalk", "钉钉"),
        ):
            provider_box.addItem(name, provider)
        purge = QPushButton("清理所选 IM 的来源与偏好")
        purge.setEnabled(False)
        provider_box.currentIndexChanged.connect(
            lambda: purge.setEnabled(provider_box.currentData() is not None)
        )
        privacy_controls.addWidget(provider_box)
        privacy_controls.addWidget(purge)
        layout.addLayout(privacy_controls)
        defaults = self._config.model_dump(mode="json") if hasattr(
            self._config, "model_dump"
        ) else dict(self._config or {})

        def loaded(overrides: Any) -> None:
            values = {key: value for key, value in defaults.items() if key in SETTINGS_KEYS}
            if isinstance(overrides, dict):
                values.update({key: value for key, value in overrides.items()
                               if key in SETTINGS_KEYS})
            editor.setPlainText(json.dumps(values, ensure_ascii=False, indent=2))
            save.setEnabled(True)

        def saved(_: Any) -> None:
            self._test_ok = False
            self._restart_required = True
            self._disable()
            status.setText("已保存。请退出并重新启动应用，重新自检后手动启用。")
            save.setEnabled(True)

        def save_settings() -> None:
            try:
                from jev_open.config import AppConfig

                value = json.loads(editor.toPlainText())
                if not isinstance(value, dict) or set(value) - SETTINGS_KEYS:
                    raise ValueError("只允许编辑此窗口列出的应用设置字段")
                _reject_credentials(value)
                AppConfig.model_validate({**defaults, **value})
            except (ValueError, TypeError) as exc:
                status.setText(f"校验失败：{exc}")
                return
            save.setEnabled(False)
            self._run_async(
                self._state.set_setting("application_config", value), saved,
                lambda _: save.setEnabled(True),
            )

        async def purge_provider(provider: str) -> None:
            if self._broker is not None:
                await self._broker.disable()
                clear_cache = getattr(getattr(self._broker, "decision", None), "clear_cache", None)
                if clear_cache:
                    clear_cache()
            await self._state.clear_provider_data(provider)

        def purge_requested() -> None:
            provider = provider_box.currentData()
            if not provider:
                return

            def confirmed() -> None:
                self.cancel_pending()
                self._test_ok = False
                self._restart_required = True
                purge.setEnabled(False)
                provider_box.setEnabled(False)

                def settled(_: Any) -> None:
                    purge.setEnabled(True)
                    provider_box.setEnabled(True)

                def completed(_: Any) -> None:
                    settled(None)
                    status.setText(
                        "已清理来源索引、该 IM 的显式偏好与缓存。历史仍保留；"
                        "如需删除聊天摘录，请再清空历史。重启前 Hook 保持停用。"
                    )

                self._run_async(purge_provider(provider), completed, settled)

            self._ask(
                "清理 IM 来源数据",
                f"将停用 Hook，并删除 {provider_box.currentText()} 的本机文件来源索引、"
                "账号范围偏好和模型缓存。不会删除原始 IM 聊天，也不会删除决策历史。"
                "此操作无法撤销；完成后需要重启应用。是否继续？",
                confirmed,
            )

        save.clicked.connect(save_settings)
        purge.clicked.connect(purge_requested)
        self._dialogs.append(dialog)
        self._run_async(self._state.get_setting("application_config", {}), loaded)
        dialog.show()


def _reject_credentials(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.casefold() in {"api_key", "typesafe_api_key", "password", "secret", "token"}:
                raise ValueError("凭据不能保存在应用设置中")
            _reject_credentials(child)
    elif isinstance(value, list):
        for child in value:
            _reject_credentials(child)


def _confidence_text(name: str, confidence: float | None) -> str:
    if confidence is not None:
        return f"{name} confidence：{confidence:.1%}"
    return f"{name}未提供 confidence"


def _application_icon() -> QIcon:
    image = QPixmap(32, 32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#17634a"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(1, 1, 30, 30, 7, 7)
    painter.setPen(QColor("white"))
    font = painter.font()
    font.setPixelSize(23)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(image.rect(), Qt.AlignmentFlag.AlignCenter, "J")
    painter.end()
    return QIcon(image)
