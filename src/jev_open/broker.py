from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import inspect
import json
import logging
import ntpath
import threading
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import urlsplit

from .actions.interface import OpenActionModule
from .context.interface import ContextModule
from .decision.interface import DecisionModule
from .domain import ContextEnvelope, FileTarget, OpenDecision, OpenRequest, SceneDecision, UrlTarget
from .domain.errors import NoCompatibleAction
from .interception.interface import InterceptionModule
from .persistence.interface import StateModule
from .ui.interface import UserInterfaceModule

_LOG = logging.getLogger(__name__)
_IM_NAMES = {"qq", "qqnt", "wechat", "weixin", "dingtalk", "dingding", "feishu", "lark", "slack"}


@dataclass(slots=True)
class _Progress:
    context: ContextEnvelope | None = None
    decision: OpenDecision | None = None
    action_id: str | None = None
    scene_id: str | None = None
    status: str = "deciding"
    launch_in_progress: bool = False


@dataclass(slots=True)
class BrokerApplication:
    """Coordinate decisions on a worker loop while Qt owns the main thread.

    Hook installation is an explicit, gated operation. The decision deadline
    never covers a human confirmation or a launch attempt.
    """

    interception: InterceptionModule
    context: ContextModule
    actions: OpenActionModule
    decision: DecisionModule
    state: StateModule
    ui: UserInterfaceModule
    decision_deadline_ms: int = 20_000
    context_deadline_ms: int = 500
    duplicate_window_seconds: float = 30.0
    maintenance_interval_seconds: float = 86_400
    ready_check: Callable[[], bool | None] | None = None
    initial_requests: tuple[OpenRequest, ...] = ()
    _pending: dict[str, tuple[asyncio.Task[Any], float, str]] = field(
        default_factory=dict, init=False)
    _requests: set[asyncio.Task[Any]] = field(default_factory=set, init=False)
    _loop: asyncio.AbstractEventLoop | None = field(default=None, init=False)
    _thread: threading.Thread | None = field(default=None, init=False)
    _lifecycle_lock: asyncio.Lock | None = field(default=None, init=False)
    _enabled: bool = field(default=False, init=False)
    _stopping: bool = field(default=False, init=False)
    _clean_stop: bool = field(default=True, init=False)
    _maintenance_task: asyncio.Task[None] | None = field(default=None, init=False)

    @property
    def enabled(self) -> bool:
        return self._enabled and self.interception.is_active()

    def submit(self, coroutine: Coroutine[Any, Any, Any]) -> concurrent.futures.Future[Any]:
        loop = self._loop
        if loop is None or not loop.is_running() or loop.is_closed():
            coroutine.close()
            raise RuntimeError("The broker event loop is not running")
        return asyncio.run_coroutine_threadsafe(coroutine, loop)

    def run(self) -> int:
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("The application UI must run on the main thread")
        if self._loop is not None:
            raise RuntimeError("The broker application is already running")
        loop = asyncio.new_event_loop()
        self._loop = loop
        started = threading.Event()

        def worker() -> None:
            asyncio.set_event_loop(loop)
            loop.call_soon(started.set)
            try:
                loop.run_forever()
            finally:
                remaining = asyncio.all_tasks(loop)
                for task in remaining:
                    task.cancel()
                if remaining:
                    loop.run_until_complete(asyncio.gather(*remaining, return_exceptions=True))
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.run_until_complete(loop.shutdown_default_executor(timeout=2.0))
                loop.close()

        thread = threading.Thread(target=worker, name="jev-open-broker", daemon=True)
        self._thread = thread
        thread.start()
        completed = False
        try:
            if not started.wait(timeout=5):
                raise RuntimeError("The broker event loop did not start")
            bind = getattr(self.ui, "bind", None)
            if bind is not None:
                bind(
                    submit=self.submit, broker=self, state=self.state,
                    ready_check=self.ready_check,
                )
            self.submit(self._startup()).result(timeout=5)
            for request in self.initial_requests:
                self.submit(self.handle_request(request))
            result = self.ui.run()
            completed = True
            return result
        finally:
            if loop.is_running():
                try:
                    self.submit(self._shutdown(completed)).result(timeout=10)
                except Exception as exc:
                    _LOG.warning("broker.shutdown_failed exception_type=%s", type(exc).__name__)
                finally:
                    loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=5)
            self._loop = None
            self._thread = None
            self._lifecycle_lock = None

    async def _startup(self) -> None:
        read = getattr(self.state, "get_setting", None)
        write = getattr(self.state, "set_setting", None)
        if read is not None and write is not None:
            async with asyncio.timeout(4):
                clean = await read("clean_shutdown", True)
                await write("clean_shutdown", False)
            if clean is False:
                await self._notify("show_recovery_report")
        if getattr(self.state, "prune_provenance", None) is not None:
            self._maintenance_task = asyncio.create_task(
                self._maintain_provenance(), name="jev-open-provenance-maintenance",
            )

    async def _maintain_provenance(self) -> None:
        prune = self.state.prune_provenance
        while True:
            try:
                # The state adapter examines known indexed paths only.
                await prune()
            except Exception as exc:
                _LOG.warning("broker.provenance_prune_failed exception_type=%s", type(exc).__name__)
            await asyncio.sleep(max(0.01, self.maintenance_interval_seconds))

    async def _shutdown(self, completed: bool) -> None:
        maintenance = self._maintenance_task
        self._maintenance_task = None
        if maintenance is not None:
            maintenance.cancel()
            done, _ = await asyncio.wait({maintenance}, timeout=1)
            if not done:
                self._clean_stop = False
        await self.disable()
        stop_context = getattr(self.context, "stop", None)
        if stop_context is not None:
            try:
                result = stop_context()
                if inspect.isawaitable(result):
                    async with asyncio.timeout(2):
                        await result
            except Exception as exc:
                self._clean_stop = False
                _LOG.warning("broker.context_stop_failed exception_type=%s", type(exc).__name__)
        write = getattr(self.state, "set_setting", None)
        if completed and self._clean_stop and write is not None:
            async with asyncio.timeout(2):
                await write("clean_shutdown", True)

    async def enable(self) -> None:
        async with self._lock():
            if self.enabled:
                return
            if self.ready_check is not None and self.ready_check() is not True:
                raise RuntimeError("Complete provider consent and self-tests before enabling hooks")
            self._stopping = False
            self._clean_stop = True
            try:
                await self.interception.start(self.handle_request)
                self._enabled = True
            except BaseException:
                self._enabled = False
                self._stopping = True
                await self._stop_interception()
                raise

    async def clear_history(self) -> None:
        if self._loop is not None and asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("History must be cleared through the broker event loop")
        await self._clear_model_cache()
        try:
            await self.state.clear_history()
        finally:
            # Invalidate decisions started while the database deletion yielded.
            await self._clear_model_cache()

    async def _clear_model_cache(self) -> None:
        clear_cache = getattr(self.decision, "clear_cache", None)
        if clear_cache is not None:
            result = clear_cache()
            if inspect.isawaitable(result):
                await result

    async def disable(self) -> None:
        async with self._lock():
            self._enabled = False
            self._stopping = True
            cancel = getattr(self.ui, "cancel_pending", None)
            if cancel is not None:
                try:
                    cancel()
                except Exception as exc:
                    _LOG.warning("broker.ui_cancel_failed exception_type=%s", type(exc).__name__)
            current = asyncio.current_task()
            requests = {task for task in self._requests if task is not current and not task.done()}
            for task in requests:
                task.cancel()
            await self._stop_interception()
            if requests:
                done, pending = await asyncio.wait(requests, timeout=3.0)
                for task in done:
                    if not task.cancelled():
                        task.exception()
                for task in pending:
                    task.cancel()
                if pending:
                    self._clean_stop = False
            self._pending.clear()

    def _lock(self) -> asyncio.Lock:
        if self._lifecycle_lock is None:
            self._lifecycle_lock = asyncio.Lock()
        return self._lifecycle_lock

    async def _stop_interception(self) -> None:
        try:
            async with asyncio.timeout(3):
                await self.interception.stop()
        except Exception as exc:
            self._clean_stop = False
            _LOG.warning("broker.stop_failed exception_type=%s", type(exc).__name__)
            try:
                async with asyncio.timeout(1):
                    await self.interception.emergency_stop()
            except Exception as emergency:
                _LOG.warning(
                    "broker.emergency_stop_failed exception_type=%s", type(emergency).__name__,
                )

    async def handle_request(self, request: OpenRequest) -> None:
        if self._stopping:
            return
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("Open requests require an asyncio task")
        self._requests.add(task)
        progress = _Progress()
        fingerprint: str | None = None
        started = monotonic()
        deadline = datetime.now(UTC) + timedelta(milliseconds=self.decision_deadline_ms)
        try:
            try:
                async with asyncio.timeout(self.decision_deadline_ms / 1000):
                    context_deadline = min(
                        deadline,
                        datetime.now(UTC) + timedelta(milliseconds=self.context_deadline_ms),
                    )
                    progress.context = await self.context.assemble(request, context_deadline)
                    discovered = await self.actions.discover(progress.context.target)
                    candidates = tuple(
                        action for action in discovered
                        if str(request.verb) in action.invocation_data.get(
                            "supported_verbs", ["open"],
                        )
                    )
                    progress.context = progress.context.model_copy(
                        update={"open_actions": candidates})
                    if not candidates:
                        raise NoCompatibleAction("No compatible action exists")
                    fingerprint = _request_fingerprint(progress.context)
                    pending = self._pending.get(fingerprint)
                    if (pending is not None and not pending[0].done()
                            and monotonic() - pending[1] < self.duplicate_window_seconds):
                        await self._notify("notify_duplicate", pending[2])
                        return
                    self._pending[fingerprint] = (task, monotonic(), request.request_id)
                    preference = await self.state.find_preference(progress.context)
                    valid_action_ids = {action.action_id for action in candidates}
                    if preference is not None and preference.action_id in valid_action_ids:
                        progress.decision = OpenDecision(
                            source="preference", action_id=preference.action_id,
                            probabilities={preference.action_id: 1.0}, confidence=1.0,
                            scene=SceneDecision(scene_id=None, evaluated=False), latency_ms=0,
                        )
                    else:
                        progress.decision = await self.decision.decide(progress.context, deadline)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                reason = "decision_timeout" if isinstance(exc, TimeoutError) else type(exc).__name__
                await self._fallback(request, progress, reason, started)
                return

            await self._confirm_and_launch(progress)
        except asyncio.CancelledError:
            if progress.context is not None:
                if progress.launch_in_progress:
                    progress.status = "failed"
                    self._warn(progress, "launch_cancelled_outcome_unknown")
                elif progress.status not in {"launched", "fallback", "failed"}:
                    progress.status = "cancelled"
                await self._record(progress)
            raise
        except Exception as exc:
            # Never turn a confirmed click or an audit failure into a second open.
            if progress.status != "launched":
                progress.status = "failed"
            self._warn(progress, "confirmation_failed:" + type(exc).__name__)
            await self._record(progress)
        finally:
            if fingerprint is not None:
                pending = self._pending.get(fingerprint)
                if pending is not None and pending[0] is task:
                    self._pending.pop(fingerprint, None)
            self._requests.discard(task)

    async def _confirm_and_launch(self, progress: _Progress) -> None:
        assert progress.context is not None and progress.decision is not None
        presentation = progress.decision
        candidates = {action.action_id: action for action in progress.context.open_actions}
        while True:
            progress.status = "awaiting_confirmation"
            confirmation = await self.ui.enqueue(progress.context, presentation)
            progress.scene_id = confirmation.scene_id
            if not confirmation.confirmed:
                progress.status = "cancelled_by_user"
                progress.action_id = None
                await self._record(progress)
                return
            action = candidates.get(confirmation.action_id)
            if action is None:
                self._warn(progress, "invalid_selection")
                presentation = progress.decision.model_copy(update={"action_id": None})
                continue
            progress.action_id = action.action_id
            progress.launch_in_progress = True
            try:
                # A to_thread launch cannot be cancelled safely: timing it out
                # could offer a retry while the original OS call still executes.
                await self.actions.launch(action, progress.context)
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                progress.launch_in_progress = False
                progress.status = "failed"
                self._warn(progress, "launch_outcome_unknown")
                await self._record(progress)
                await self._notify(
                    "notify_launch_result", progress.context.request.request_id, False,
                    "Launch outcome is unknown; check the application before opening again.",
                )
                return
            except Exception as exc:
                progress.launch_in_progress = False
                progress.status = "failed"
                self._warn(progress, "launch_failed:" + type(exc).__name__)
                await self._record(progress)
                await self._notify(
                    "notify_launch_result", progress.context.request.request_id, False,
                    "Launch failed; choose an action again or cancel.",
                )
                presentation = progress.decision.model_copy(update={"action_id": None})
                continue
            progress.launch_in_progress = False
            progress.status = "launched"
            rule = confirmation.remember_scope
            if rule is not None and rule.action_id == action.action_id:
                try:
                    async with asyncio.timeout(2):
                        await self.state.save_preference(rule)
                except Exception as exc:
                    self._warn(progress, "preference_save_failed:" + type(exc).__name__)
                    _LOG.warning(
                        "broker.preference_save_failed exception_type=%s", type(exc).__name__,
                    )
            await self._record(progress)
            await self._notify(
                "notify_launch_result", progress.context.request.request_id, True, "Opened",
            )
            return

    async def _fallback(
        self, request: OpenRequest, progress: _Progress, reason: str, started: float,
    ) -> None:
        progress.context = progress.context or _minimal_context(request)
        progress.decision = OpenDecision(
            source="system_fallback", action_id=None, scene=SceneDecision(
                scene_id=None, evaluated=False), latency_ms=round((monotonic() - started) * 1000),
            warnings=(reason,),
        )
        progress.status = "fallback"
        try:
            async with asyncio.timeout(5):
                await self.interception.fallback(request)
        except asyncio.CancelledError:
            progress.launch_in_progress = True
            raise
        except TimeoutError:
            progress.status = "failed"
            self._warn(progress, "fallback_outcome_unknown")
        except Exception as exc:
            progress.status = "failed"
            self._warn(progress, "fallback_failed:" + type(exc).__name__)
        await self._record(progress)

    @staticmethod
    def _warn(progress: _Progress, warning: str) -> None:
        if progress.decision is None:
            progress.decision = OpenDecision(
                source="system_fallback", action_id=None,
                scene=SceneDecision(scene_id=None, evaluated=False), latency_ms=0,
            )
        progress.decision = progress.decision.model_copy(update={
            "warnings": (*progress.decision.warnings, warning),
        })

    async def _record(self, progress: _Progress) -> None:
        if progress.context is None:
            return
        if progress.decision is None:
            self._warn(progress, "decision_incomplete")
        try:
            async with asyncio.timeout(2):
                await self.state.record_decision(
                    progress.context, progress.decision, progress.action_id,
                    final_scene_id=progress.scene_id, status=progress.status,
                )
        except Exception as exc:
            _LOG.warning("broker.audit_failed exception_type=%s", type(exc).__name__)

    async def _notify(self, method: str, *args: object) -> None:
        callback = getattr(self.ui, method, None)
        if callback is None:
            return
        try:
            result = callback(*args)
            if inspect.isawaitable(result):
                async with asyncio.timeout(1):
                    await result
        except Exception as exc:
            _LOG.warning("broker.ui_notification_failed exception_type=%s", type(exc).__name__)


def _request_fingerprint(context: ContextEnvelope) -> str:
    payload = context.model_dump(mode="json")
    request = payload.pop("request")
    payload["request"] = {
        "verb": request["verb"], "parameters": request["parameters"],
        "source_executable": ntpath.normcase(str(context.request.source_executable)),
        "working_directory": (
            ntpath.normcase(str(context.request.working_directory))
            if context.request.working_directory else None),
    }
    if context.target.kind == "file":
        payload["target"]["path"] = ntpath.normcase(ntpath.normpath(str(context.target.path)))
    elif context.target.kind == "url":
        # URL paths, queries and fragments remain byte-for-byte case sensitive.
        payload["target"]["url"] = str(context.target.url)
    if (context.chat_context is None
            and context.source_application.casefold() in _IM_NAMES):
        payload["unresolved_chat_request"] = context.request.request_id
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _minimal_context(request: OpenRequest) -> ContextEnvelope:
    try:
        parsed = urlsplit(request.target)
        is_drive = len(request.target) >= 2 and request.target[1] == ":"
        if parsed.scheme and not is_drive:
            target = UrlTarget(url=request.target, scheme=parsed.scheme, host=parsed.hostname)
        else:
            path = Path(request.target)
            target = FileTarget(path=path, extension=path.suffix.casefold())
    except ValueError:
        target = FileTarget(path=Path(request.target), extension="")
    return ContextEnvelope(
        request=request, target=target, source_application=request.source_executable.stem,
    )
