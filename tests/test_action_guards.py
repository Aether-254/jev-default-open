from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from jev_open.actions import windows
from jev_open.actions.windows import WindowsOpenActionModule
from jev_open.domain import (
    ContextEnvelope,
    FileTarget,
    OpenAction,
    OpenRequest,
    OpenVerb,
    UrlTarget,
)
from jev_open.domain.errors import LaunchFailed


def action(executable: str, **data) -> OpenAction:
    return OpenAction(
        action_id="fixture",
        application_id="fixture",
        display_name="Fixture opener",
        capability_description="Test opener",
        invocation_kind="executable",
        invocation_data={
            "executable": executable,
            "arguments": ["{target}"],
            "schemes": ["https"],
            "supported_verbs": ["open"],
            **data,
        },
    )


def context(selected: OpenAction, *, verb=OpenVerb.OPEN) -> ContextEnvelope:
    raw = "HTTPS://EXAMPLE.test:443/a?signature=%2f%2F&x=1#part"
    return ContextEnvelope(
        request=OpenRequest(
            request_id="action-test",
            source_pid=10,
            source_executable=Path("C:/QQ.exe"),
            target=raw,
            verb=verb,
            captured_at=datetime.now(UTC),
        ),
        target=UrlTarget(url=raw, scheme="https", host="example.test"),
        source_application="fixture",
        open_actions=(selected,),
    )


@pytest.mark.asyncio
async def test_launch_preserves_exact_url_and_does_not_use_shell(tmp_path, monkeypatch):
    exe = tmp_path / "browser.exe"
    exe.write_bytes(b"not executed")
    selected = action(str(exe))
    calls = []
    monkeypatch.setattr(windows.subprocess, "Popen", lambda *a, **kw: calls.append((a, kw)))
    evidence = context(selected)
    await WindowsOpenActionModule().launch(selected, evidence)
    assert calls[0][0][0] == [str(exe), evidence.request.target]
    assert calls[0][1]["shell"] is False


@pytest.mark.asyncio
async def test_launch_strips_service_credentials_from_child_environment(tmp_path, monkeypatch):
    exe = tmp_path / "browser.exe"
    exe.write_bytes(b"not executed")
    selected = action(str(exe))
    calls = []
    monkeypatch.setenv("TYPESAFE_API_KEY", "fixture-key")
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-openai-key")
    monkeypatch.setenv("FIXTURE_TOKEN", "fixture-token")
    monkeypatch.setenv("JEV_VISIBLE_SETTING", "kept")
    monkeypatch.setattr(windows.subprocess, "Popen", lambda *a, **kw: calls.append((a, kw)))
    await WindowsOpenActionModule().launch(selected, context(selected))
    child_env = calls[0][1]["env"]
    assert "TYPESAFE_API_KEY" not in child_env
    assert "OPENAI_API_KEY" not in child_env
    assert "FIXTURE_TOKEN" not in child_env
    assert child_env["JEV_VISIBLE_SETTING"] == "kept"


@pytest.mark.asyncio
async def test_launch_requires_candidate_membership(tmp_path):
    selected = action(str(tmp_path / "app.exe"))
    evidence = context(selected).model_copy(update={"open_actions": ()})
    with pytest.raises(LaunchFailed, match="candidate set"):
        await WindowsOpenActionModule().launch(selected, evidence)


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", ["code.cmd", "script.bat", "cmd.exe", "powershell.exe"])
async def test_launch_rejects_script_hosts_and_scripts(tmp_path, filename):
    executable = tmp_path / filename
    executable.write_bytes(b"not executed")
    selected = action(str(executable))
    with pytest.raises(LaunchFailed):
        await WindowsOpenActionModule().launch(selected, context(selected))


@pytest.mark.asyncio
async def test_launch_rejects_unsupported_edit_and_extra_parameters(tmp_path):
    executable = tmp_path / "browser.exe"
    executable.write_bytes(b"not executed")
    selected = action(str(executable))
    with pytest.raises(LaunchFailed, match="verb"):
        await WindowsOpenActionModule().launch(selected, context(selected, verb=OpenVerb.EDIT))
    evidence = context(selected)
    evidence = evidence.model_copy(
        update={
            "request": evidence.request.model_copy(update={"parameters": "unpreserved arguments"})
        }
    )
    with pytest.raises(LaunchFailed, match="parameters"):
        await WindowsOpenActionModule().launch(selected, evidence)
    evidence = context(selected).model_copy(
        update={
            "request": context(selected).request.model_copy(
                update={"working_directory": Path("relative")}
            )
        }
    )
    with pytest.raises(LaunchFailed, match="working directory"):
        await WindowsOpenActionModule().launch(selected, evidence)


def test_vscode_never_candidate_for_binary_workbook():
    target = FileTarget(path=Path("C:/report.xlsx"), extension=".xlsx")
    assert windows._vscode_actions(target) == []


def test_arbitrary_protocol_never_offered_browser_profile():
    target = UrlTarget(url="custom:payload", scheme="custom")
    assert windows._browser_profile_actions(target) == []


def test_compatibility_tokens_are_case_insensitive():
    selected = action("C:/fixture.exe", schemes=["HTTPS"], extensions=[".CSV"])
    assert windows._compatible(selected, UrlTarget(url="https://example.test", scheme="https"))
    assert windows._compatible(
        selected, FileTarget(path=Path("C:/report.csv"), extension=".csv")
    )


@pytest.mark.parametrize(
    "data",
    [
        {"executable": "fixture.exe", "arguments": ["{target}"]},
        {"executable": "C:/fixture.exe", "arguments": ["{target}", "\n"]},
        {
            "executable": "C:/fixture.exe",
            "arguments": ["{target}"],
            "profile_directory": "relative-profile",
        },
    ],
)
def test_launch_shape_rejects_relative_paths_and_control_arguments(data):
    selected = action(
        data["executable"],
        **{key: value for key, value in data.items() if key != "executable"},
    )
    assert not windows._launch_shape_valid(selected)


def test_launch_target_rejects_relative_file_and_control_uri():
    target = FileTarget(path=Path("relative/report.csv"), extension=".csv")
    assert not windows._launch_target_valid("relative/report.csv", target)
    url = UrlTarget(url="https://example.test/a", scheme="https")
    assert not windows._launch_target_valid("https://example.test/\n", url)


def test_browser_profile_discovery_uses_safe_absolute_profile_and_label(monkeypatch, tmp_path):
    local = tmp_path / "local"
    executable = local / "Google/Chrome/Application/chrome.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"fixture")
    profile_root = local / "Google/Chrome/User Data"
    profile = profile_root / "---"
    profile.mkdir(parents=True)
    (profile_root / "Local State").write_text(
        '{"profile":{"info_cache":{"---":{"name":"Work\\nInjected"}}}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "programs"))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "programs-x86"))
    found = windows._browser_profile_actions(
        UrlTarget(url="https://example.test", scheme="https")
    )
    assert len(found) == 1
    selected = found[0]
    assert selected.action_id.startswith("chrome.")
    assert "\n" not in selected.display_name
    assert Path(selected.invocation_data["profile_directory"]).is_absolute()
    assert windows._launch_shape_valid(selected)


@pytest.mark.asyncio
async def test_configured_actions_require_capability_and_add_explicit_labels(monkeypatch):
    monkeypatch.setattr(windows, "_protocol_actions", lambda scheme: [])
    monkeypatch.setattr(windows, "_browser_profile_actions", lambda target: [])
    good = action("C:/fixture.exe")
    other = good.model_copy(
        update={
            "action_id": "other",
            "invocation_data": {"executable": "C:/other.exe", "arguments": ["{target}"]},
        }
    )
    module = WindowsOpenActionModule(
        (good.model_dump(), other.model_dump()), profile_labels={"fixture": ("Work", "Client A")}
    )
    found = await module.discover(UrlTarget(url="https://example.test", scheme="https"))
    assert len(found) == 1
    assert "User labels: Work, Client A" in found[0].capability_description


@pytest.mark.asyncio
async def test_registered_action_invokes_exact_progid(monkeypatch):
    selected = OpenAction(
        action_id="assoc.work",
        application_id="WorkBrowser",
        display_name="Work Browser",
        capability_description="Registered work handler",
        invocation_kind="assoc_handler",
        invocation_data={
            "progid": "WorkBrowser",
            "schemes": ["https"],
            "supported_verbs": ["open"],
        },
    )
    calls = []
    monkeypatch.setattr(windows, "_invoke_progid", lambda *args: calls.append(args))
    evidence = context(selected)
    await WindowsOpenActionModule().launch(selected, evidence)
    assert calls[0][0] == "WorkBrowser"
    assert calls[0][1] == evidence.request.target


def test_command_parser_round_trip_quoted_unicode_windows_path():
    assert windows._command_line_to_argv(
        '"C:\\Program Files\\app.exe" --flag "C:\\fake folder\\file.csv"'
    ) == ["C:\\Program Files\\app.exe", "--flag", "C:\\fake folder\\file.csv"]


@pytest.mark.asyncio
async def test_removed_profile_is_not_silently_recreated(tmp_path):
    exe = tmp_path / "browser.exe"
    exe.write_bytes(b"not executed")
    selected = action(str(exe), profile_directory=str(tmp_path / "missing-profile"))
    with pytest.raises(LaunchFailed, match="profile no longer"):
        await WindowsOpenActionModule().launch(selected, context(selected))


@pytest.mark.asyncio
async def test_discovery_keeps_current_default_before_candidate_limit(monkeypatch):
    raw = action("C:/fixture.exe")
    candidates = [
        raw.model_copy(update={"action_id": str(index), "display_name": str(index)})
        for index in range(260)
    ]
    default = raw.model_copy(
        update={
            "action_id": "current-default",
            "display_name": "ZZZ default",
            "invocation_data": {**raw.invocation_data, "is_default": True},
        }
    )
    monkeypatch.setattr(windows, "_protocol_actions", lambda scheme: [*candidates, default])
    monkeypatch.setattr(windows, "_browser_profile_actions", lambda target: [])
    discovered = await WindowsOpenActionModule().discover(
        UrlTarget(url="https://example.test", scheme="https")
    )
    assert len(discovered) == 255
    assert discovered[0].action_id == "current-default"
