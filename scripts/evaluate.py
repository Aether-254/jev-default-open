"""Validate synthetic scenarios, or explicitly evaluate them against Jev.

Examples:
    python scripts/evaluate.py
    python scripts/evaluate.py --live --timeout-seconds 30

No target is ever opened and no local account, chat, or installed app is inspected.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import sys
import time
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path, PureWindowsPath
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from jev_open.decision.jev import DEFAULT_SCENES, JevDecisionModule  # noqa: E402
from jev_open.domain.models import ContextEnvelope, FileTarget, OpenDecision  # noqa: E402

DEFAULT_FIXTURES = ROOT / "tests" / "fixtures" / "scenarios.json"
DEFAULT_REPORT_DIRECTORY = ROOT / "artifacts" / "evaluation"
ACTION_ACCURACY_THRESHOLD = 0.80


class FixtureMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    origin: Literal["synthetic"]
    created_at: date
    contains_private_data: Literal[False]
    description: str
    intended_use: str
    scene_set: list[str]


def _in_synthetic_directory(value: str, directory: str) -> bool:
    path = PureWindowsPath(value)
    root = PureWindowsPath(f"C:/{directory}")
    return path.is_relative_to(root) and ".." not in path.parts


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    title: str
    expected_scene: str
    expected_action_id: str
    context: ContextEnvelope

    @model_validator(mode="after")
    def validate_synthetic_scenario(self) -> Scenario:
        context = self.context
        actions = context.open_actions
        ids = [action.action_id for action in actions]
        if not 2 <= len(actions) <= 4 or len(ids) != len(set(ids)):
            raise ValueError("Each scenario needs 2..4 distinct compatible actions")
        if self.expected_action_id not in ids:
            raise ValueError("Expected action must be one of the scenario candidates")
        if self.expected_scene not in DEFAULT_SCENES:
            raise ValueError("Expected scene is not part of the fixed scene set")
        request = context.request
        if not request.request_id.startswith("synthetic-"):
            raise ValueError("Scenario request IDs must be explicitly synthetic")
        if not _in_synthetic_directory(str(request.source_executable), "SyntheticApps"):
            raise ValueError("Scenario source executable must be a synthetic path")
        if request.parameters or request.shell_execute_flags:
            raise ValueError("Synthetic scenarios must not contain executable parameters")
        if isinstance(context.target, FileTarget):
            target = str(context.target.path)
            if not _in_synthetic_directory(target, "Synthetic"):
                raise ValueError("File targets must stay in C:/Synthetic")
            if PureWindowsPath(target) != PureWindowsPath(request.target):
                raise ValueError("Request and file target must match")
            if request.working_directory is not None and not _in_synthetic_directory(
                str(request.working_directory), "Synthetic"
            ):
                raise ValueError("Working directories must be synthetic")
        else:
            url = urlsplit(context.target.url)
            if (
                url.scheme != "https"
                or not url.hostname
                or not url.hostname.endswith(".invalid")
                or url.username
                or url.password
            ):
                raise ValueError("URL targets must use synthetic HTTPS .invalid domains")
            if context.target.url != request.target or context.target.host != url.hostname:
                raise ValueError("Request and URL target must match")
        for action in actions:
            data = action.invocation_data
            if data.get("synthetic_only") is not True or not _in_synthetic_directory(
                str(data.get("executable", "")), "SyntheticApps"
            ):
                raise ValueError("All action invocations must be marked synthetic")
        chat = context.chat_context
        if chat is not None:
            if not chat.account_id_hash.startswith("synthetic-"):
                raise ValueError("Chat account identifiers must be synthetic")
            if not (chat.conversation_id or "").startswith("synthetic-"):
                raise ValueError("Chat conversation identifiers must be synthetic")
            if any(message.sender != "Synthetic Sender" for message in chat.messages):
                raise ValueError("Fixture messages must have a synthetic sender")
        return self


class ScenarioSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    metadata: FixtureMetadata
    scenarios: list[Scenario] = Field(min_length=20)

    @model_validator(mode="after")
    def validate_coverage(self) -> ScenarioSet:
        ids = [scenario.id for scenario in self.scenarios]
        if len(ids) != len(set(ids)):
            raise ValueError("Scenario IDs must be unique")
        expected_scenes = set(DEFAULT_SCENES)
        if set(self.metadata.scene_set) != expected_scenes:
            raise ValueError("Metadata must declare the complete fixed scene set")
        if {scenario.expected_scene for scenario in self.scenarios} != expected_scenes:
            raise ValueError("The fixture set must cover all eight scenes")
        return self


def load_scenarios(path: Path) -> tuple[ScenarioSet, str]:
    payload = path.read_bytes()
    return ScenarioSet.model_validate_json(payload), hashlib.sha256(payload).hexdigest()


def percentile95(values: list[float]) -> float | None:
    """Nearest-rank P95, with no interpolation on small evaluation sets."""
    if not values:
        return None
    return round(sorted(values)[math.ceil(0.95 * len(values)) - 1], 3)


def _error_code(error: Exception) -> str:
    # Exceptions can contain authenticated URLs, payloads or response bodies.
    if isinstance(error, httpx.HTTPStatusError):
        return f"http_{error.response.status_code}"
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return "timeout"
    if isinstance(error, httpx.RequestError):
        return "transport_error"
    if isinstance(error, (ValueError, TypeError)):
        return "invalid_response"
    return "evaluation_error"


def _prediction_row(scenario: Scenario, decision: OpenDecision, latency_ms: float) -> dict:
    if decision.source != "jev":
        raise ValueError("Live evaluation cannot count cached or fallback decisions")
    return {
        "scenario_id": scenario.id,
        "expected_action_id": scenario.expected_action_id,
        "predicted_action_id": decision.action_id,
        "action_correct": decision.action_id == scenario.expected_action_id,
        "expected_scene": scenario.expected_scene,
        "predicted_scene": decision.scene.scene_id,
        "scene_correct": (
            decision.scene.evaluated and decision.scene.scene_id == scenario.expected_scene
        ),
        "action_probabilities": decision.probabilities,
        "scene_probabilities": decision.scene.probabilities,
        "action_confidence": decision.confidence,
        "scene_confidence": decision.scene.confidence,
        "latency_ms": round(latency_ms, 3),
        "status": "evaluated",
        "error_code": None,
    }


def summarize(rows: list[dict]) -> dict:
    completed = [row for row in rows if row["status"] == "evaluated"]
    latencies = [row["latency_ms"] for row in rows]
    successful_latencies = [row["latency_ms"] for row in completed]
    action_correct = sum(row["action_correct"] for row in completed)
    scene_correct = sum(row["scene_correct"] for row in completed)
    confusion: dict[str, dict[str, int]] = {scene: {} for scene in DEFAULT_SCENES}
    for row in rows:
        predicted = row.get("predicted_scene") or "__error__"
        counts = confusion[row["expected_scene"]]
        counts[predicted] = counts.get(predicted, 0) + 1
    evaluated = bool(completed)
    action_accuracy = action_correct / len(rows) if evaluated and rows else None
    scene_accuracy = scene_correct / len(rows) if evaluated and rows else None
    return {
        "attempted_count": len(rows),
        "evaluated_count": len(completed),
        "error_count": len(rows) - len(completed),
        "evaluation_performed": evaluated,
        "accuracy_denominator": "all_attempted_scenarios_errors_count_as_incorrect",
        "action_top1_accuracy": action_accuracy,
        "scene_accuracy": scene_accuracy,
        "latency_mean_ms": round(sum(latencies) / len(latencies), 3) if latencies else None,
        "latency_p95_ms": percentile95(latencies),
        "successful_latency_p95_ms": percentile95(successful_latencies),
        "scene_confusion_matrix": confusion,
        "action_accuracy_gate": {
            "threshold": ACTION_ACCURACY_THRESHOLD,
            "passed": action_accuracy >= ACTION_ACCURACY_THRESHOLD if evaluated else None,
            "status": "evaluated" if evaluated else "not_evaluated",
        },
    }


async def evaluate_live(
    fixtures: ScenarioSet, *, model: str, api_key: str, base_url: str, timeout_seconds: float
) -> dict:
    if not api_key:
        raise ValueError("TYPESAFE_API_KEY is required for --live")
    module = JevDecisionModule(model=model, api_key=api_key, base_url=base_url)
    rows = []
    for scenario in fixtures.scenarios:
        started = time.perf_counter()
        try:
            decision = await module.decide(
                scenario.context, datetime.now(UTC) + timedelta(seconds=timeout_seconds)
            )
            row = _prediction_row(scenario, decision, (time.perf_counter() - started) * 1000)
        except Exception as error:
            row = {
                "scenario_id": scenario.id,
                "expected_action_id": scenario.expected_action_id,
                "predicted_action_id": None,
                "action_correct": False,
                "expected_scene": scenario.expected_scene,
                "predicted_scene": None,
                "scene_correct": False,
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                "status": "error",
                "error_code": _error_code(error),
            }
        rows.append(row)
        print(f"[{len(rows):02d}/{len(fixtures.scenarios)}] {scenario.id}: {row['status']}")
    return {"metrics": summarize(rows), "results": rows}


def _positive_seconds(value: str) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("timeout must be finite and positive")
    return seconds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--validate-only", action="store_true", help="offline validation (default)")
    mode.add_argument("--live", action="store_true", help="send synthetic fixtures to Jev")
    parser.add_argument("--model", default=os.environ.get("TYPESAFE_MODEL", "jev-1.13.0"))
    parser.add_argument("--timeout-seconds", type=_positive_seconds, default=30.0)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIRECTORY)
    args = parser.parse_args(argv)
    try:
        fixtures, fingerprint = load_scenarios(args.fixtures)
    except (OSError, ValueError) as error:
        print(f"Fixture validation failed ({type(error).__name__}).", file=sys.stderr)
        return 2
    counts = Counter(scenario.expected_scene for scenario in fixtures.scenarios)
    print(f"Validated {len(fixtures.scenarios)} synthetic scenarios; SHA-256 {fingerprint}")
    print("Scenes: " + ", ".join(f"{scene}={counts[scene]}" for scene in DEFAULT_SCENES))
    if not args.live:
        print("Mode: validate-only; no inference, network requests, or target launches.")
        print("Accuracy gate: NOT RUN. Schema validation is not a model quality result.")
        return 0
    api_key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not api_key:
        print("--live requires the TYPESAFE_API_KEY environment variable.", file=sys.stderr)
        return 2
    base_url = os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai/v1")
    endpoint = urlsplit(base_url)
    if (
        endpoint.scheme != "https" or not endpoint.hostname
        or endpoint.username or endpoint.password
    ):
        print("Live evaluation requires an HTTPS API endpoint without URL credentials.",
              file=sys.stderr)
        return 2
    print(f"Mode: live; sending {len(fixtures.scenarios)} synthetic contexts to Jev.")
    try:
        result = asyncio.run(evaluate_live(
            fixtures, model=args.model, api_key=api_key, base_url=base_url,
            timeout_seconds=args.timeout_seconds,
        ))
    except KeyboardInterrupt:
        print("Evaluation interrupted; no completed report was saved.", file=sys.stderr)
        return 130
    created_at = datetime.now(UTC)
    report = {
        "schema_version": 1,
        "mode": "live",
        "created_at": created_at.isoformat(),
        "fixture_origin": "synthetic",
        "fixture_sha256": fingerprint,
        "model": args.model,
        "scene_counts": dict(counts),
        **result,
    }
    report_path = args.report_dir / f"evaluation-{created_at.strftime('%Y%m%dT%H%M%S%fZ')}.json"
    try:
        args.report_dir.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except OSError as error:
        print(f"Could not save evaluation report ({type(error).__name__}).", file=sys.stderr)
        return 2
    metrics = result["metrics"]
    print(f"Report: {report_path.resolve()}")
    print(json.dumps(metrics, indent=2, sort_keys=True))
    if not metrics["evaluation_performed"]:
        return 3
    return 0 if metrics["action_accuracy_gate"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
