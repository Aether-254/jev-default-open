from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("jev_evaluate", ROOT / "scripts" / "evaluate.py")
assert SPEC and SPEC.loader
evaluation = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = evaluation
SPEC.loader.exec_module(evaluation)


@pytest.fixture
def fixtures():
    return evaluation.load_scenarios(evaluation.DEFAULT_FIXTURES)[0]


def test_synthetic_fixture_has_24_valid_contexts_covering_all_scenes(fixtures):
    assert len(fixtures.scenarios) == 24
    assert fixtures.metadata.origin == "synthetic"
    assert fixtures.metadata.contains_private_data is False
    assert str(fixtures.metadata.created_at) == "2026-09-27"
    counts = Counter(scenario.expected_scene for scenario in fixtures.scenarios)
    assert counts == dict.fromkeys(evaluation.DEFAULT_SCENES, 3)
    assert {scenario.context.target.kind for scenario in fixtures.scenarios} == {"file", "url"}
    assert any(scenario.context.chat_context is None for scenario in fixtures.scenarios)
    assert any(scenario.context.chat_context is not None for scenario in fixtures.scenarios)


def test_fixture_has_stable_hash():
    _, fingerprint = evaluation.load_scenarios(evaluation.DEFAULT_FIXTURES)
    assert len(fingerprint) == 64
    assert fingerprint == evaluation.load_scenarios(evaluation.DEFAULT_FIXTURES)[1]


@pytest.mark.parametrize("mutation", [
    lambda data: data["metadata"].update(origin="captured"),
    lambda data: data["metadata"].update(contains_private_data=True),
    lambda data: data["scenarios"][0].update(expected_action_id="missing"),
    lambda data: data["scenarios"][0].update(expected_scene="invented"),
    lambda data: data["scenarios"][0]["context"]["target"].update(
        url="https://private.example.com/account"
    ),
    lambda data: data["scenarios"][0]["context"]["request"].update(parameters="--token secret"),
    lambda data: data["scenarios"][1]["context"]["target"].update(
        path="C:/Users/ActualUser/Documents/private.png"
    ),
    lambda data: data["scenarios"][1]["context"]["target"].update(
        path="C:/Synthetic/../Private/private.png"
    ),
    lambda data: data["scenarios"][0]["context"]["open_actions"][0]["invocation_data"].update(
        synthetic_only=False
    ),
    lambda data: data.update(scenarios=data["scenarios"][:19]),
])
def test_rejects_invalid_or_unsafely_labelled_fixture(fixtures, mutation):
    data = fixtures.model_dump(mode="json")
    mutation(data)
    with pytest.raises(ValidationError):
        evaluation.ScenarioSet.model_validate(data)


def test_rejects_duplicate_ids(fixtures):
    data = fixtures.model_dump(mode="json")
    data["scenarios"][1]["id"] = data["scenarios"][0]["id"]
    with pytest.raises(ValidationError, match="unique"):
        evaluation.ScenarioSet.model_validate(data)


def test_offline_default_never_constructs_client_or_writes_report(monkeypatch, capsys, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("Offline validation must not construct a Jev client")
    monkeypatch.setattr(evaluation, "JevDecisionModule", forbidden)
    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-key-never-use")
    report_dir = tmp_path / "reports"
    assert evaluation.main(["--report-dir", str(report_dir)]) == 0
    assert not report_dir.exists()
    output = capsys.readouterr().out
    assert "Validated 24 synthetic scenarios" in output
    assert "Accuracy gate: NOT RUN" in output
    assert "synthetic-key-never-use" not in output


def test_live_requires_explicit_key(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert evaluation.main(["--live", "--report-dir", str(tmp_path / "reports")]) == 2
    assert "TYPESAFE_API_KEY" in capsys.readouterr().err
    assert not (tmp_path / "reports").exists()


def test_modes_are_mutually_exclusive():
    with pytest.raises(SystemExit) as caught:
        evaluation.main(["--live", "--validate-only"])
    assert caught.value.code == 2


@pytest.mark.parametrize("timeout", ["0", "-1", "nan", "inf"])
def test_timeout_requires_positive_finite_value(timeout):
    with pytest.raises(SystemExit) as caught:
        evaluation.main(["--timeout-seconds", timeout])
    assert caught.value.code == 2


@pytest.mark.parametrize("url", ["http://example.invalid/v1", "https://user:secret@example.invalid"])
def test_live_rejects_insecure_or_credential_bearing_endpoint(monkeypatch, tmp_path, capsys, url):
    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-key")
    monkeypatch.setenv("TYPESAFE_BASE_URL", url)
    assert evaluation.main(["--live", "--report-dir", str(tmp_path)]) == 2
    assert "secret" not in capsys.readouterr().err
    assert not list(tmp_path.glob("*.json"))


def test_p95_uses_nearest_rank():
    assert evaluation.percentile95([]) is None
    assert evaluation.percentile95([10]) == 10
    assert evaluation.percentile95(list(range(1, 21))) == 19


def test_no_evaluations_never_claims_accuracy_or_gate_success():
    report = evaluation.summarize([{
        "status": "error", "expected_scene": "personal", "predicted_scene": None,
        "action_correct": False, "scene_correct": False, "latency_ms": 12.0,
    }])
    assert report["evaluation_performed"] is False
    assert report["action_top1_accuracy"] is None
    assert report["scene_accuracy"] is None
    assert report["action_accuracy_gate"]["passed"] is None
    assert report["scene_confusion_matrix"]["personal"] == {"__error__": 1}


def test_errors_count_as_incorrect_and_scene_accuracy_is_separate():
    rows = [{
        "status": "evaluated", "expected_scene": "personal", "predicted_scene": "finance",
        "action_correct": True, "scene_correct": False, "latency_ms": 100.0,
    } for _ in range(4)]
    rows.append({
        "status": "error", "expected_scene": "personal", "predicted_scene": None,
        "action_correct": False, "scene_correct": False, "latency_ms": 500.0,
    })
    report = evaluation.summarize(rows)
    assert report["action_top1_accuracy"] == 0.8
    assert report["scene_accuracy"] == 0
    assert report["action_accuracy_gate"]["passed"] is True
    assert report["latency_p95_ms"] == 500
    assert report["successful_latency_p95_ms"] == 100
    assert report["scene_confusion_matrix"]["personal"] == {"finance": 4, "__error__": 1}


def _mock_post(fixtures):
    scenarios = {str(item.context.target.url if item.context.target.kind == "url"
                     else item.context.target.path): item for item in fixtures.scenarios}

    async def post(self, payload, timeout):
        target = payload["state"]["target"]
        scenario = scenarios[target.get("url", target.get("path"))]
        action = scenario.expected_action_id
        scene = scenario.expected_scene
        return {"answers": {
            "open_action": {"type": "choice", "choice": action,
                            "probabilities": {action: 1.0}, "confidence": 1.0},
            "scene": {"type": "choice", "choice": scene,
                      "probabilities": {scene: 1.0}, "confidence": 1.0},
        }}
    return post


@pytest.mark.asyncio
async def test_evaluate_uses_real_decision_module_with_mocked_transport(fixtures, monkeypatch):
    monkeypatch.setattr(evaluation.JevDecisionModule, "_post", _mock_post(fixtures))
    result = await evaluation.evaluate_live(
        fixtures, model="synthetic-test-model", api_key="synthetic-not-a-secret",
        base_url="https://api.example.invalid/v1", timeout_seconds=2,
    )
    assert result["metrics"]["evaluated_count"] == 24
    assert result["metrics"]["action_top1_accuracy"] == 1
    assert result["metrics"]["scene_accuracy"] == 1
    assert all(row["status"] == "evaluated" for row in result["results"])


@pytest.mark.asyncio
async def test_failed_transport_is_redacted_and_not_a_model_success(fixtures, monkeypatch):
    secret = "never-log-this-synthetic-secret"

    async def failed_post(*args, **kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(evaluation.JevDecisionModule, "_post", failed_post)
    result = await evaluation.evaluate_live(
        fixtures, model="synthetic-test-model", api_key=secret,
        base_url="https://api.example.invalid/v1", timeout_seconds=1,
    )
    assert secret not in json.dumps(result)
    assert result["metrics"]["evaluated_count"] == 0
    assert result["metrics"]["error_count"] == 24
    assert result["metrics"]["action_accuracy_gate"]["passed"] is None
    assert {row["error_code"] for row in result["results"]} == {"evaluation_error"}


def test_report_saved_only_on_explicit_live_run(fixtures, monkeypatch, tmp_path):
    secret = "synthetic-secret"
    monkeypatch.setattr(evaluation.JevDecisionModule, "_post", _mock_post(fixtures))
    monkeypatch.setenv("TYPESAFE_API_KEY", secret)
    monkeypatch.setenv("TYPESAFE_BASE_URL", "https://api.example.invalid/v1")
    assert evaluation.main([
        "--live", "--model", "synthetic-test-model", "--report-dir", str(tmp_path)
    ]) == 0
    reports = list(tmp_path.glob("evaluation-*.json"))
    assert len(reports) == 1
    serialized = reports[0].read_text(encoding="utf-8")
    assert secret not in serialized
    assert "Synthetic Sender" not in serialized
    report = json.loads(serialized)
    assert report["model"] == "synthetic-test-model"
    assert report["fixture_origin"] == "synthetic"
    assert report["metrics"]["action_accuracy_gate"]["threshold"] == 0.8


def test_live_run_below_accuracy_gate_returns_failure(fixtures, monkeypatch, tmp_path):
    scenarios = {str(item.context.target.url if item.context.target.kind == "url"
                     else item.context.target.path): item for item in fixtures.scenarios}

    async def incorrect_post(self, payload, timeout):
        target = payload["state"]["target"]
        scenario = scenarios[target.get("url", target.get("path"))]
        wrong_action = next(action.action_id for action in scenario.context.open_actions
                            if action.action_id != scenario.expected_action_id)
        return {"answers": {
            "open_action": {"choice": wrong_action, "probabilities": {wrong_action: 1.0}},
            "scene": {"choice": scenario.expected_scene,
                      "probabilities": {scenario.expected_scene: 1.0}},
        }}

    monkeypatch.setattr(evaluation.JevDecisionModule, "_post", incorrect_post)
    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("TYPESAFE_BASE_URL", "https://api.example.invalid/v1")
    assert evaluation.main(["--live", "--report-dir", str(tmp_path)]) == 1
    report = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert report["metrics"]["action_top1_accuracy"] == 0.0
    assert report["metrics"]["scene_accuracy"] == 1.0
    assert report["metrics"]["action_accuracy_gate"]["passed"] is False


def test_failed_live_run_returns_unevaluated_exit_code(monkeypatch, tmp_path):
    async def failed_post(*args, **kwargs):
        raise TimeoutError("synthetic timeout")

    monkeypatch.setattr(evaluation.JevDecisionModule, "_post", failed_post)
    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("TYPESAFE_BASE_URL", "https://api.example.invalid/v1")
    assert evaluation.main(["--live", "--report-dir", str(tmp_path)]) == 3
    report = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert report["metrics"]["action_accuracy_gate"]["status"] == "not_evaluated"
    assert report["metrics"]["evaluated_count"] == 0


def test_invalid_fixture_returns_nonzero_before_network(monkeypatch, tmp_path):
    fixture = tmp_path / "broken.json"
    fixture.write_text('{"schema_version": 1}', encoding="utf-8")
    assert evaluation.main(["--fixtures", str(fixture), "--live"]) == 2


def test_error_codes_do_not_include_response_or_url():
    request = httpx.Request("POST", "https://example.invalid/private?secret=hidden")
    response = httpx.Response(401, request=request, text="sensitive-server-error")
    error = httpx.HTTPStatusError("sensitive-error", request=request, response=response)
    assert evaluation._error_code(error) == "http_401"
