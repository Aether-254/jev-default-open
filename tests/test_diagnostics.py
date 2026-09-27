from pathlib import Path
from types import SimpleNamespace

from jev_open.config import AppConfig
from jev_open.diagnostics import _check_local, _provider_warnings


def test_provider_configuration_does_not_claim_runtime_adapter_readiness():
    config = AppConfig(enabled_providers=("qq", "wechat", "dingtalk"))
    warnings = _provider_warnings(config)
    assert len(warnings) == 3
    assert "not connected" in warnings[0]
    assert "not implemented" in warnings[1]
    assert "not implemented" in warnings[2]
    assert all("path-only" in warning for warning in warnings)
    assert _provider_warnings(AppConfig()) == []


def test_self_test_reports_disabled_hook_without_reading_chat(tmp_path, monkeypatch):
    import jev_open.diagnostics as diagnostics

    class Result:
        returncode = 0
        stdout = b'{"protocol_version":2,"experimental_hook_enabled":false}'

    calls = []

    def self_test(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return Result()

    monkeypatch.setattr(Path, "is_file", lambda self: True)
    monkeypatch.setattr(diagnostics.subprocess, "run", self_test)
    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-presence-only")
    config = AppConfig(
        native_host_path=tmp_path / "native_host.exe", enabled_providers=("qq",)
    )

    report = _check_local(config)

    assert not report["ok"]
    assert not report["live_jev_tested"]
    assert not report["live_im_tested"]
    assert not report["hook_runtime_tested"]
    assert report["hook_available"] is False
    assert len(report["warnings"]) == 1
    checks = {check["name"]: check for check in report["checks"]}
    assert checks["Native protocol"]["ok"]
    assert not checks["Experimental interception"]["ok"]
    assert calls[0][0] == [str(config.native_host_path), "--self-test"]
    assert calls[0][1]["timeout"] == 5
    assert "synthetic-presence-only" not in str(report)


def test_self_test_reports_qq_runtime_fingerprint_without_claiming_chat_ready(
    tmp_path, monkeypatch
):
    import jev_open.diagnostics as diagnostics

    fingerprint = SimpleNamespace(
        path=tmp_path / "QQ.exe",
        file_sha256="a" * 64,
        file_version="9.9.20.12345",
        product_version="9.9.20.12345",
    )
    monkeypatch.setattr(diagnostics, "read_qq_version_fingerprint", lambda _: fingerprint)
    monkeypatch.setattr(diagnostics, "fingerprint_allowed", lambda *_args, **_kwargs: True)
    config = AppConfig(
        qq_runtime_path=fingerprint.path,
        qq_allowed_versions=(fingerprint.file_version,),
    )

    report = diagnostics._check_local(config)

    assert report["qq_runtime"]["status"] == "matched"
    assert report["qq_runtime"]["file_version"] == fingerprint.file_version
    assert report["qq_runtime"]["chat_adapter_ready"] is False
    checks = {check["name"]: check for check in report["checks"]}
    assert checks["QQ runtime fingerprint"]["ok"]
    assert checks["QQ runtime allow-list"]["ok"]
