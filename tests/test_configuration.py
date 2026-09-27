from pathlib import Path

import pytest
from pydantic import ValidationError

from jev_open.config import AppConfig, load_config
from jev_open.domain import UrlTarget


def test_config_default_is_inert_and_native_path_is_repo_root(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    config = load_config()
    assert not config.enabled_providers
    assert not config.state_directory.exists()
    root = Path(__file__).resolve().parents[1]
    assert config.native_host_path == root / "native/build/Release/native_host.exe"


def test_url_preserves_signed_query_and_case():
    raw = "https://EXAMPLE.test/SomePath?token=a%2fb&k=+&k=%20#A"
    value = UrlTarget(url=raw, scheme="https", host="example.test")
    assert value.url == raw
    assert value.model_dump(mode="json")["url"] == raw


@pytest.mark.parametrize("value", ["relative", "javascript:\ncode"])
def test_uri_validation(value):
    with pytest.raises(ValidationError):
        UrlTarget(url=value, scheme="example")


def test_standard_context_budget_is_capped():
    assert AppConfig(mode="standard").context_budget_ms == 500
    assert AppConfig(mode="demo").context_budget_ms == 2000


def test_native_ack_deadline_is_a_fixed_protocol_constant():
    assert AppConfig().pipe_ack_deadline_ms == 50
    with pytest.raises(ValidationError):
        AppConfig(pipe_ack_deadline_ms=100)


def test_qq_runtime_allow_list_is_explicit_and_bounded(tmp_path):
    config = AppConfig(
        qq_runtime_path=tmp_path / "QQ.exe",
        qq_allowed_versions=("9.9.27.45758",),
        qq_allowed_sha256=("A" * 64,),
    )
    assert config.qq_runtime_path == tmp_path / "QQ.exe"
    assert config.qq_allowed_versions == ("9.9.27.45758",)
    assert config.qq_allowed_sha256 == ("a" * 64,)


def test_config_rejects_unknown_fields_and_providers():
    with pytest.raises(ValidationError):
        AppConfig(unknown="ignored secrets")
    with pytest.raises(ValidationError):
        AppConfig(enabled_providers=("unregistered",))


def test_qq_bridge_is_disabled_by_default_and_fixture_mode_is_explicit():
    assert AppConfig().qq_bridge_mode == "disabled"
    assert (
        AppConfig(enabled_providers=("qq",), qq_bridge_mode="fixture").qq_bridge_mode
        == "fixture"
    )
    with pytest.raises(ValidationError):
        AppConfig(qq_bridge_mode="fixture")
    with pytest.raises(ValidationError):
        AppConfig(qq_bridge_mode="authenticated")


def test_qq_runtime_fingerprint_is_opt_in_and_allow_lists_are_validated(tmp_path):
    path = tmp_path / "QQ.exe"
    config = AppConfig(
        qq_runtime_path=path,
        qq_allowed_versions=("9.9.20.12345",),
        qq_allowed_sha256=("A" * 64,),
    )
    assert config.qq_runtime_path == path
    assert config.qq_allowed_sha256 == ("a" * 64,)
    assert AppConfig().qq_runtime_path is None
    with pytest.raises(ValidationError):
        AppConfig(qq_runtime_path=Path("relative/QQ.exe"))
    with pytest.raises(ValidationError):
        AppConfig(qq_allowed_sha256=("not-a-digest",))
