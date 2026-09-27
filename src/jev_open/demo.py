"""Interactive synthetic demonstration. No API calls, hooks, or external launches."""

from __future__ import annotations

import asyncio
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from .broker import BrokerApplication
from .config import AppConfig
from .domain import (
    ChatContext,
    ChatMessage,
    ContextEnvelope,
    FileTarget,
    OpenAction,
    OpenDecision,
    OpenRequest,
    OpenVerb,
    SceneDecision,
    UrlTarget,
)
from .persistence.sqlcipher import SQLCipherStateModule
from .ui.qt import QtUserInterfaceModule


class _DemoInterception:
    def __init__(self):
        self.active = False

    async def start(self, handler):
        self.active = True

    async def stop(self):
        self.active = False

    async def emergency_stop(self):
        await self.stop()

    async def fallback(self, request):
        return None  # Deliberately do not open the synthetic target.

    def is_active(self):
        return self.active


class _DemoContext:
    async def assemble(self, request, deadline):
        target = (
            UrlTarget(url=request.target, scheme="https", host="portal.example.invalid")
            if request.target.startswith("https:")
            else FileTarget(path=Path(request.target), extension=".csv", mime_type="text/csv")
        )
        chat = None
        if target.kind == "url":
            chat = ChatContext(
                provider="fixture",
                account_id_hash="0" * 64,
                conversation_id="synthetic-work",
                conversation_title="合成工作会话（非真实聊天）",
                conversation_labels=("工作",),
                target_message_id="fixture-message",
                acquisition="internal",
                confidence=1,
                messages=(
                    ChatMessage(
                        message_id="fixture-message",
                        sender="演示同事",
                        message_type="link",
                        url=request.target,
                        text="请在公司工作账号查看这个合成项目页面。",
                    ),
                ),
            )
        return ContextEnvelope(
            request=request,
            target=target,
            source_application="synthetic-demo",
            source_account_hash=chat.account_id_hash if chat else None,
            chat_context=chat,
            path_labels=("数据分析",) if target.kind == "file" else (),
        )


class _DemoActions:
    async def discover(self, target):
        options = (
            (
                ("chrome.work", "Chrome · 工作", "工作身份"),
                ("chrome.personal", "Chrome · 个人", "个人身份"),
            )
            if target.kind == "url"
            else (
                ("vscode.data", "VS Code · 数据分析", "数据分析"),
                ("excel.default", "Excel", "财务表格"),
            )
        )
        return tuple(
            OpenAction(
                action_id=identifier,
                application_id=identifier.split(".")[0],
                display_name=name,
                capability_description=description,
                invocation_kind="executable",
                invocation_data={"demo_only": True, "supported_verbs": ["open"]},
            )
            for identifier, name, description in options
        )

    async def launch(self, action, context):
        return None  # Demo intentionally never calls ShellExecute or Popen.


class _DemoDecision:
    async def decide(self, context, deadline):
        await asyncio.sleep(0.1)
        first, second = context.open_actions
        scene = "data_analysis" if context.target.kind == "file" else "work_general"
        return OpenDecision(
            source="fixture",
            action_id=first.action_id,
            probabilities={first.action_id: 0.88, second.action_id: 0.12},
            confidence=0.86,
            scene=SceneDecision(scene_id=scene, probabilities={scene: 1.0}, confidence=1.0),
            latency_ms=100,
            warnings=("SYNTHETIC DEMO: fixture probabilities; Jev was NOT called.",),
        )


class _DemoUI(QtUserInterfaceModule):
    def bind(self, **kwargs):
        super().bind(**kwargs)

        async def seed():
            targets = (
                "C:/Synthetic/DataScience/observations.csv",
                "https://portal.example.invalid/synthetic-project",
            )
            await asyncio.gather(
                *(
                    kwargs["broker"].handle_request(
                        OpenRequest(
                            request_id=f"demo-{index}",
                            source_pid=1,
                            source_executable=Path("C:/Synthetic/fixture.exe"),
                            verb=OpenVerb.OPEN,
                            target=target,
                            captured_at=datetime.now(UTC),
                        )
                    )
                    for index, target in enumerate(targets)
                )
            )

        kwargs["submit"](seed())


def run_demo(config: AppConfig) -> int:
    async def demo_checks():
        return {
            "ok": True,
            "checks": [
                {
                    "name": "离线演示",
                    "ok": True,
                    "detail": "不调用 Jev、不安装 Hook、不读取聊天、不启动应用",
                }
            ],
        }

    with tempfile.TemporaryDirectory(prefix="jev-open-demo-") as directory:
        state = SQLCipherStateModule(Path(directory) / "synthetic.db")
        ui = _DemoUI(self_test=demo_checks, config=config, demo_mode=True)
        broker = BrokerApplication(
            interception=_DemoInterception(),
            context=_DemoContext(),
            actions=_DemoActions(),
            decision=_DemoDecision(),
            state=state,
            ui=ui,
            ready_check=lambda: ui.ready_to_enable,
        )
        return broker.run()
