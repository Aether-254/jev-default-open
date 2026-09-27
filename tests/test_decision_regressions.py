import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from test_architecture_contracts import action, request

from jev_open.decision.jev import JevDecisionModule
from jev_open.domain import ContextEnvelope, FileTarget


def envelope():
    return ContextEnvelope(
        request=request(),
        target=FileTarget(path="C:/demo/report.csv", extension=".csv"),
        source_application="explorer",
        open_actions=(action(), action("excel.default")),
    )


@pytest.mark.parametrize(
    "base_url",
    [
        "http://jev.invalid/v1",
        "https://user:secret@jev.invalid/v1",
        "https://jev.invalid/v1?token=secret",
        "https://jev.invalid/v1#fragment",
        "jev.invalid/v1",
    ],
)
def test_jev_rejects_non_tls_or_credential_bearing_endpoint(base_url):
    with pytest.raises(ValueError):
        JevDecisionModule(model="fixture", api_key="fake", base_url=base_url)


def response(probabilities=None):
    return {
        "answers": {
            "scene": {
                "type": "choice",
                "choice": "data_analysis",
                "probabilities": {"data_analysis": 1.0},
                "confidence": 1.0,
            },
            "open_action": {
                "type": "choice",
                "choice": "vscode.default",
                "probabilities": probabilities or {"vscode.default": 0.8, "excel.default": 0.2},
                "confidence": 0.8,
            },
        }
    }


@pytest.mark.asyncio
async def test_total_deadline_includes_transport(monkeypatch):
    module = JevDecisionModule(model="fixture", api_key="fake", base_url="https://example.test/v1")

    async def slow(*args):
        await asyncio.sleep(0.15)
        return response()

    monkeypatch.setattr(module, "_post", slow)
    with pytest.raises(TimeoutError):
        await module.decide(envelope(), datetime.now(UTC) + timedelta(milliseconds=10))


@pytest.mark.asyncio
async def test_cache_ignores_request_id_but_not_chat_or_candidate_identity(monkeypatch):
    module = JevDecisionModule(model="fixture", api_key="fake", base_url="https://example.test/v1")
    calls = []

    async def post(*args):
        calls.append(args)
        return response()

    monkeypatch.setattr(module, "_post", post)
    context = envelope()
    await module.decide(context, datetime.now(UTC) + timedelta(seconds=1))
    second = context.model_copy(
        update={
            "request": context.request.model_copy(
                update={"request_id": "different", "captured_at": datetime.now(UTC)}
            )
        }
    )
    result = await module.decide(second, datetime.now(UTC) + timedelta(seconds=1))
    assert len(calls) == 1
    assert result.source == "exact_cache"
    changed = second.model_copy(update={"path_labels": ("finance",)})
    await module.decide(changed, datetime.now(UTC) + timedelta(seconds=1))
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "probabilities",
    [
        {"vscode.default": float("nan"), "excel.default": 0.2},
        {"vscode.default": -1.0, "excel.default": 2.0},
        {"invented": 0.8, "vscode.default": 0.2},
    ],
)
async def test_rejects_invalid_probability_distribution(monkeypatch, probabilities):
    module = JevDecisionModule(model="fixture", api_key="fake", base_url="https://example.test/v1")

    async def post(*args):
        return response(probabilities)

    monkeypatch.setattr(module, "_post", post)
    with pytest.raises(ValueError):
        await module.decide(envelope(), datetime.now(UTC) + timedelta(seconds=1))
