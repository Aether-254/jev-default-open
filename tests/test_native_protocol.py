import asyncio
import json
import mmap
import os
import struct
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from jev_open.domain import OpenRequest, OpenVerb
from jev_open.domain.errors import LaunchFailed
from jev_open.interception.native import NativeInterceptionModule
from jev_open.interception.protocol import (
    MAX_MESSAGE_BYTES,
    MAX_TRANSPORT_BYTES,
    Acknowledgement,
    OpenRequestMessage,
    ProtocolHello,
    parse_native_message,
)


def make_request(**changes: Any) -> OpenRequest:
    values = {
        "request_id": "native-1",
        "source_pid": 123,
        "source_executable": Path("C:/Windows/explorer.exe"),
        "verb": OpenVerb.OPEN,
        "target": "C:/demo/file.txt",
        "captured_at": datetime.now(UTC),
    }
    values.update(changes)
    return OpenRequest(**values)


def make_offer(**changes: Any) -> OpenRequestMessage:
    return OpenRequestMessage(
        ownership=f"Local\\JevOpenRequest-123-{uuid4()}",
        payload=make_request(**changes),
    )


def test_native_request_round_trip_is_bounded_and_versioned() -> None:
    message = make_offer()
    assert OpenRequestMessage.model_validate_json(message.model_dump_json()) == message
    assert ProtocolHello(process_id=123, nonce="n" * 16).protocol_version == 2
    assert Acknowledgement(request_id="native-1", accepted=True).accepted


@pytest.mark.parametrize("changes", [
    {"target": ""},
    {"target": "C:/a\0.txt"},
    {"source_executable": Path("relative.exe")},
    {"working_directory": Path("relative")},
    {"shell_execute_flags": -1},
    {"show_command": 100},
    {"captured_at": datetime(2026, 9, 27)},
])
def test_native_request_validation_rejects_bad_fields(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        make_offer(**changes)


def test_native_protocol_rejects_wrong_version_and_unbounded_payload() -> None:
    with pytest.raises(ValidationError):
        ProtocolHello(protocol_version=1, process_id=123, nonce="n" * 16)
    with pytest.raises(ValueError):
        parse_native_message(b" " * (MAX_MESSAGE_BYTES + 1))
    with pytest.raises(ValueError):
        parse_native_message(b"[]")


def test_transport_bound_allows_escaped_copy_of_valid_source() -> None:
    offer = make_offer(target="https://example.invalid/" + "x" * 30000,
                       parameters="a" * 30000)
    raw = offer.model_dump_json()
    assert len(raw.encode()) < MAX_MESSAGE_BYTES
    envelope = json.loads(raw)
    envelope["transport_payload"] = raw
    wire = json.dumps(envelope).encode()
    assert parse_native_message(wire, max_bytes=MAX_TRANSPORT_BYTES)["type"] == "open_request"


@pytest.mark.asyncio
async def test_fallback_does_not_drop_request_without_native_host(tmp_path: Path) -> None:
    launches = []
    module = NativeInterceptionModule(tmp_path / "missing.exe", system_launcher=launches.append)
    request = make_request(verb=OpenVerb.EDIT, parameters="--plain")
    await module.fallback(request)
    assert launches == [request]
    assert not module.is_active()


@pytest.mark.asyncio
async def test_fallback_propagates_launch_failure(tmp_path: Path) -> None:
    def failing_launcher(request: OpenRequest) -> None:
        raise LaunchFailed("Synthetic failure")

    module = NativeInterceptionModule(tmp_path / "missing.exe", system_launcher=failing_launcher)
    with pytest.raises(LaunchFailed, match="Synthetic failure"):
        await module.fallback(make_request())


class FakeInput:
    def __init__(self, process: "FakeProcess", *, fail_start: bool) -> None:
        self.process = process
        self.fail_start = fail_start

    def write(self, data: bytes) -> None:
        command = json.loads(data)
        name = command["command"]
        self.process.stdout.feed_data(json.dumps({
            "type": "command_result",
            "command_id": command["command_id"],
            "command": name,
            "success": not (name == "start_hook" and self.fail_start),
            "hook_active": name == "start_hook" and not self.fail_start,
            "win32": 50,
        }).encode() + b"\n")
        if name == "exit":
            self.process.returncode = 0
            self.process.stdout.feed_eof()

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.process.stdout.feed_eof()


class FakeProcess:
    pid = 456

    def __init__(
        self,
        nonce: str,
        *,
        fail_start: bool = False,
        child_environment: dict[str, str] | None = None,
    ) -> None:
        self.returncode: int | None = None
        self.child_environment = child_environment or {}
        self.stdout = asyncio.StreamReader()
        self.stdin = FakeInput(self, fail_start=fail_start)
        self.stdout.feed_data(ProtocolHello(
            process_id=self.pid, nonce=nonce,
        ).model_dump_json().encode() + b"\n")

    async def wait(self) -> int:
        while self.returncode is None:
            await asyncio.sleep(0)
        return self.returncode

    def kill(self) -> None:
        self.returncode = 1
        self.stdout.feed_eof()


def fake_subprocess(monkeypatch: pytest.MonkeyPatch, module: NativeInterceptionModule,
                    *, fail_start: bool = False) -> list[FakeProcess]:
    processes: list[FakeProcess] = []

    async def spawn(*args: str, **kwargs: Any) -> FakeProcess:
        process = FakeProcess(
            args[args.index("--nonce") + 1],
            fail_start=fail_start,
            child_environment=kwargs.get("env"),
        )
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(module, "_load_claim_library", lambda: None)
    return processes


@pytest.mark.asyncio
async def test_start_is_active_only_after_positive_native_ack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = tmp_path / "native_host.exe"
    host.touch()
    module = NativeInterceptionModule(host)
    fake_subprocess(monkeypatch, module, fail_start=True)

    async def handler(request: OpenRequest) -> None:
        raise AssertionError("No offer expected")

    with pytest.raises(RuntimeError, match="start_hook failed"):
        await module.start(handler)
    assert not module.is_active()


@pytest.mark.asyncio
async def test_native_host_environment_excludes_service_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = tmp_path / "native_host.exe"
    host.touch()
    module = NativeInterceptionModule(host)
    monkeypatch.setenv("TYPESAFE_API_KEY", "fixture-key")
    monkeypatch.setenv("CUSTOM_TOKEN", "fixture-token")
    monkeypatch.setenv("JEV_VISIBLE_SETTING", "kept")
    processes = fake_subprocess(monkeypatch, module)

    await module.prepare()
    environment = processes[0].child_environment
    assert "TYPESAFE_API_KEY" not in environment
    assert "CUSTOM_TOKEN" not in environment
    assert environment["JEV_VISIBLE_SETTING"] == "kept"
    await module.stop()


@pytest.mark.asyncio
async def test_host_disconnect_clears_active_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = tmp_path / "native_host.exe"
    host.touch()
    module = NativeInterceptionModule(host)
    processes = fake_subprocess(monkeypatch, module)

    async def handler(request: OpenRequest) -> None:
        pass

    await module.start(handler)
    assert module.is_active()
    processes[0].kill()
    await asyncio.sleep(0)
    assert not module.is_active()
    await module.stop()


@pytest.mark.asyncio
async def test_stop_cancellation_kills_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = tmp_path / "native_host.exe"
    host.touch()
    module = NativeInterceptionModule(host)
    processes = fake_subprocess(monkeypatch, module)

    async def handler(request: OpenRequest) -> None:
        pass

    await module.start(handler)
    monkeypatch.setattr(processes[0].stdin, "write", lambda data: None)
    task = asyncio.create_task(module.stop())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert processes[0].returncode is not None
    assert not module.is_active()
    await module.stop()


@pytest.mark.asyncio
async def test_emergency_stop_cancellation_kills_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = tmp_path / "native_host.exe"
    host.touch()
    module = NativeInterceptionModule(host)
    processes = fake_subprocess(monkeypatch, module)

    async def handler(request: OpenRequest) -> None:
        pass

    await module.start(handler)
    monkeypatch.setattr(processes[0].stdin, "write", lambda data: None)
    task = asyncio.create_task(module.emergency_stop())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert processes[0].returncode is not None
    assert not module.is_active()
    await module.stop()


@pytest.mark.asyncio
async def test_expired_offer_is_not_delivered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = tmp_path / "native_host.exe"
    host.touch()
    module = NativeInterceptionModule(host)
    processes = fake_subprocess(monkeypatch, module)
    seen = []

    async def handler(request: OpenRequest) -> None:
        seen.append(request)

    monkeypatch.setattr(module, "_claim_offer", lambda offer, raw: False)
    await module.start(handler)
    offer = make_offer()
    envelope = json.loads(offer.model_dump_json())
    envelope["transport_payload"] = offer.model_dump_json()
    processes[0].stdout.feed_data(json.dumps(envelope).encode() + b"\n")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert seen == []
    await module.stop()


def built_host() -> Path:
    configured = os.environ.get("JEV_NATIVE_TEST_HOST")
    if not configured:
        pytest.skip("Set JEV_NATIVE_TEST_HOST to run no-hook compiled host integration")
    return Path(configured)


def test_compiled_claim_helper_accepts_source_bytes_exactly_once() -> None:
    import ctypes

    host = built_host()
    module = NativeInterceptionModule(host, test_mode=True)
    module._load_claim_library()
    request = make_request(source_pid=os.getpid(), source_executable=Path(sys.executable))
    name = f"Local\\JevOpenRequest-{request.source_pid}-{uuid4()}"
    offer = OpenRequestMessage(ownership=name, payload=request)
    payload = offer.model_dump_json().encode("utf-8")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetTickCount64.restype = ctypes.c_uint64
    with mmap.mmap(-1, MAX_MESSAGE_BYTES + 24, tagname=name) as mapping:
        mapping.write(struct.pack("<IIQiI", 2, request.source_pid,
                                  kernel.GetTickCount64() + 500, 0, len(payload)))
        mapping.write(payload)
        assert not module._claim_offer(offer, b"wrong")
        assert module._claim_offer(offer, payload)
        assert not module._claim_offer(offer, payload)
        assert struct.unpack_from("<i", mapping, 16)[0] == 1


@pytest.mark.asyncio
async def test_compiled_host_test_mode_never_installs_hook() -> None:
    module = NativeInterceptionModule(built_host(), test_mode=True)
    await module.prepare()
    assert not module.is_active()
    status = await module._command("status")
    assert status["hook_active"] is False

    async def handler(request: OpenRequest) -> None:
        raise AssertionError("Test host must not intercept any application")

    with pytest.raises(RuntimeError, match="start_hook failed"):
        await module.start(handler)
    assert not module.is_active()
    await module.stop()
