from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jev_open.broker import BrokerApplication
from jev_open.decision.jev import JevDecisionModule
from jev_open.domain import (
    ConfirmationResult,
    ContextEnvelope,
    FileTarget,
    OpenAction,
    OpenDecision,
    OpenRequest,
    OpenVerb,
    PreferenceRule,
    SceneDecision,
)
from jev_open.persistence.sqlcipher import SQLCipherStateModule


def request() -> OpenRequest:
    return OpenRequest(
        request_id="r1",
        source_pid=42,
        source_executable=Path("C:/Windows/explorer.exe"),
        verb=OpenVerb.OPEN,
        target="C:/demo/report.csv",
        captured_at=datetime.now(UTC),
    )


def action(action_id: str = "vscode.default") -> OpenAction:
    return OpenAction(
        action_id=action_id,
        application_id="vscode",
        display_name=action_id,
        capability_description="Open data and source files.",
        invocation_kind="executable",
        invocation_data={"executable": "C:/fake.exe", "arguments": ["{target}"]},
    )


class FakeInterception:
    def __init__(self) -> None:
        self.fallbacks = []

    async def start(self, handler):
        self.handler = handler

    async def stop(self):
        return None

    async def emergency_stop(self):
        return None

    async def fallback(self, value):
        self.fallbacks.append(value)

    def is_active(self):
        return True


class FakeContext:
    async def assemble(self, value, deadline):
        return ContextEnvelope(
            request=value,
            target=FileTarget(path=Path(value.target), extension=".csv"),
            source_application="explorer",
        )


class FakeActions:
    def __init__(self) -> None:
        self.launched = []

    async def discover(self, target):
        return (action(), action("excel.default"))

    async def launch(self, selected, context):
        self.launched.append(selected.action_id)


class FakeDecision:
    async def decide(self, context, deadline):
        return OpenDecision(
            source="jev",
            action_id="vscode.default",
            probabilities={"vscode.default": 0.9, "excel.default": 0.1},
            confidence=0.9,
            scene=SceneDecision(scene_id="data_analysis", probabilities={"data_analysis": 1.0}),
            latency_ms=1,
        )


class FakeState:
    def __init__(self) -> None:
        self.recorded = []

    async def find_preference(self, context):
        return None

    async def save_preference(self, rule):
        return None

    async def record_decision(
        self, context, decision, final_action_id, *, final_scene_id=None, status=None,
    ):
        self.recorded.append(final_action_id)

    async def clear_all(self):
        return None


class FakeUi:
    def run(self):
        return 0

    async def enqueue(self, context, decision):
        return ConfirmationResult(confirmed=True, action_id=decision.action_id)

    async def show_recovery_report(self):
        return None


@pytest.mark.asyncio
async def test_broker_routes_one_request_through_module_interfaces() -> None:
    actions = FakeActions()
    state = FakeState()
    broker = BrokerApplication(
        interception=FakeInterception(),
        context=FakeContext(),
        actions=actions,
        decision=FakeDecision(),
        state=state,
        ui=FakeUi(),
    )
    await broker.handle_request(request())
    assert actions.launched == ["vscode.default"]
    assert state.recorded == ["vscode.default"]


@pytest.mark.asyncio
async def test_scene_and_action_are_independent_typed_questions() -> None:
    captured = {}
    module = JevDecisionModule(model="jev-test", api_key="key", base_url="https://example.test/v1")

    async def fake_post(payload, timeout):
        captured.update(payload)
        return {
            "answers": {
                "scene": {
                    "choice": "data_analysis",
                    "probabilities": {"data_analysis": 1.0},
                    "confidence": 1.0,
                },
                "open_action": {
                    "choice": "vscode.default",
                    "probabilities": {"vscode.default": 0.8, "excel.default": 0.2},
                    "confidence": 0.8,
                },
            }
        }

    module._post = fake_post
    context = ContextEnvelope(
        request=request(),
        target=FileTarget(path=Path("C:/demo/report.csv"), extension=".csv"),
        source_application="explorer",
        open_actions=(action(), action("excel.default")),
    )
    result = await module.decide(context, datetime.now(UTC) + timedelta(seconds=1))
    assert set(captured["questions"]) == {"scene", "open_action"}
    assert result.scene.scene_id == "data_analysis"
    assert result.action_id == "vscode.default"


@pytest.mark.asyncio
async def test_preference_matching_uses_most_specific_rule(tmp_path: Path) -> None:
    state = SQLCipherStateModule(tmp_path / "state.db")
    now = datetime.now(UTC)
    rule = PreferenceRule(
        rule_id="exact",
        scope="exact_target",
        selector={"target": "C:/demo/report.csv"},
        action_id="excel.default",
        created_at=now,
        updated_at=now,
    )
    await state.save_preference(rule)
    context = ContextEnvelope(
        request=request(),
        target=FileTarget(path=Path("C:/demo/report.csv"), extension=".csv"),
        source_application="explorer",
    )
    assert await state.find_preference(context) == rule
