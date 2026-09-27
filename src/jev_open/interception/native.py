from __future__ import annotations

import asyncio
import ctypes
import json
import logging
import os
import secrets
import subprocess
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import uuid4

from jev_open.domain import OpenRequest
from jev_open.domain.errors import LaunchFailed
from jev_open.process_env import sanitized_child_environment

from .interface import RequestHandler
from .protocol import (
    MAX_TRANSPORT_BYTES,
    OpenRequestMessage,
    ProtocolHello,
    parse_native_message,
    validate_request,
)

_LOG = logging.getLogger(__name__)

def launch_system(request: OpenRequest) -> None:
    """Preserve verb and arguments even when the host is stopped or has crashed."""
    validate_request(request)
    if os.name != "nt":
        raise LaunchFailed("Windows ShellExecute is unavailable on this platform")
    from ctypes import wintypes

    shell = ctypes.WinDLL("shell32", use_last_error=True)
    execute = shell.ShellExecuteW
    execute.argtypes = [
        wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
        wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int,
    ]
    execute.restype = ctypes.c_void_p
    result = execute(
        None, request.verb.value, request.target, request.parameters or None,
        str(request.working_directory) if request.working_directory else None,
        request.show_command,
    )
    if not result or result <= 32:
        raise LaunchFailed(f"Windows default opener failed with ShellExecute code {result or 0}")


class NativeInterceptionModule:
    """Manual hook lifecycle; construction and transport preparation install no hooks."""

    def __init__(
        self,
        native_host_path: Path,
        *,
        test_mode: bool = False,
        system_launcher: Callable[[OpenRequest], None] = launch_system,
        command_timeout: float = 3.0,
    ) -> None:
        self._native_host_path = native_host_path
        self._test_mode = test_mode
        self._system_launcher = system_launcher
        self._command_timeout = command_timeout
        self._active = False
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._handler: RequestHandler | None = None
        self._hello: asyncio.Future[ProtocolHello] | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._write_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._nonce = ""
        self._claim_library: Any = None
        self._claim: Any = None

    async def prepare(self) -> None:
        """Start only the stdio transport and validate the host protocol handshake."""
        if self._process and self._process.returncode is None:
            return
        if not self._native_host_path.is_file():
            raise FileNotFoundError(f"Native host is not built: {self._native_host_path}")
        self._nonce = secrets.token_hex(24)
        self._hello = asyncio.get_running_loop().create_future()
        args = [str(self._native_host_path), "--stdio", "--nonce", self._nonce]
        if self._test_mode:
            args.append("--test-mode")
        options: dict[str, Any] = {}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        self._process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=MAX_TRANSPORT_BYTES + 1,
            env=sanitized_child_environment(),
            **options,
        )
        self._reader_task = asyncio.create_task(self._read_messages(self._process))
        try:
            await asyncio.wait_for(asyncio.shield(self._hello), self._command_timeout)
        except BaseException:
            await self._close_transport()
            raise

    async def start(self, handler: RequestHandler) -> None:
        async with self._lifecycle_lock:
            if self.is_active():
                return
            self._handler = handler
            try:
                await self.prepare()
                self._load_claim_library()
                result = await self._command("start_hook")
                if result.get("hook_active") is not True:
                    raise RuntimeError("Native host did not confirm active interception")
                self._active = True
            except BaseException:
                self._active = False
                await self._close_transport()
                raise

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            self._active = False
            await self._close_transport()
            # Already accepted requests belong to the broker, including pending UI.

    async def emergency_stop(self) -> None:
        async with self._lifecycle_lock:
            self._active = False
            if self._process and self._process.returncode is None:
                try:
                    await self._command("emergency_stop")
                except (RuntimeError, TimeoutError, ConnectionError):
                    await self._close_transport()
                except asyncio.CancelledError:
                    if self._process.returncode is None:
                        self._process.kill()
                        await asyncio.shield(self._process.wait())
                    raise

    async def fallback(self, request: OpenRequest) -> None:
        validate_request(request)
        await asyncio.to_thread(self._system_launcher, request)

    async def ingest(self, request: OpenRequest) -> None:
        """Explicit synthetic entry point; never acknowledges a native offer."""
        validate_request(request)
        if self._handler is None:
            raise RuntimeError("No request handler is configured")
        await self._handler(request)

    def is_active(self) -> bool:
        return bool(self._active and self._process and self._process.returncode is None)

    async def _close_transport(self) -> None:
        process = self._process
        if process and process.returncode is None:
            try:
                with suppress(RuntimeError, TimeoutError, ConnectionError):
                    await self._command("exit")
                if process.stdin:
                    process.stdin.close()
                await asyncio.wait_for(process.wait(), 1.0)
            finally:
                if process.returncode is None:
                    process.kill()
                    await asyncio.shield(process.wait())
        if self._reader_task and self._reader_task is not asyncio.current_task():
            self._reader_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._reader_task
        self._process = None
        self._reader_task = None
        self._active = False

    async def _command(self, name: str) -> dict[str, Any]:
        process = self._process
        if process is None or process.returncode is not None or not process.stdin:
            raise ConnectionError("Native host is unavailable")
        command_id = uuid4().hex
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[command_id] = future
        try:
            line = json.dumps({"command": name, "command_id": command_id}).encode() + b"\n"
            async with self._write_lock:
                process.stdin.write(line)
                await process.stdin.drain()
            result = await asyncio.wait_for(asyncio.shield(future), self._command_timeout)
            if (
                name == "start_hook" and result.get("success") is not True
                and result.get("win32") == 50
            ):
                raise RuntimeError(
                    "Native start_hook failed: experimental interception is disabled "
                    "in this build or the host is running in test mode"
                )
            if result.get("command") != name or result.get("success") is not True:
                raise RuntimeError(f"Native {name} failed (win32={result.get('win32', 'unknown')})")
            return result
        finally:
            self._pending.pop(command_id, None)
            if not future.done():
                future.cancel()

    def _load_claim_library(self) -> None:
        if self._claim is not None:
            return
        if os.name != "nt":
            raise RuntimeError("Native interception requires Windows")
        path = self._native_host_path.with_name("native_claim.dll")
        if not path.is_file():
            raise FileNotFoundError(f"Native ownership helper is not built: {path}")
        from ctypes import wintypes

        self._claim_library = ctypes.WinDLL(str(path), use_last_error=True)
        self._claim = self._claim_library.ClaimOpenRequest
        self._claim.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_char_p, wintypes.DWORD,
        ]
        self._claim.restype = wintypes.BOOL

    def _claim_offer(self, offer: OpenRequestMessage, line: bytes) -> bool:
        if not self._claim:
            return False
        return bool(self._claim(
            offer.ownership, offer.payload.source_pid, line, len(line),
        ))

    async def _deliver(self, request: OpenRequest) -> None:
        try:
            assert self._handler is not None
            await self._handler(request)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Retrying could double launch after a handler partially succeeded.
            _LOG.error("Accepted request handler failed; automatic retry suppressed")

    async def _read_messages(self, process: asyncio.subprocess.Process) -> None:
        assert process.stdout
        try:
            while line := await process.stdout.readline():
                raw = line.rstrip(b"\r\n")
                try:
                    message = parse_native_message(raw, max_bytes=MAX_TRANSPORT_BYTES)
                    kind = message.get("type")
                    if kind == "hello":
                        hello = ProtocolHello.model_validate(message)
                        if hello.nonce != self._nonce or hello.process_id != process.pid:
                            raise RuntimeError("Native handshake identity mismatch")
                        if hello.test_mode != self._test_mode:
                            raise RuntimeError("Native test mode mismatch")
                        if self._hello is not None and not self._hello.done():
                            self._hello.set_result(hello)
                    elif kind == "command_result":
                        future = self._pending.get(str(message.get("command_id")))
                        if future is not None and not future.done():
                            future.set_result(message)
                    elif kind == "status":
                        if message.get("hook_active") is False:
                            self._active = False
                    elif kind == "open_request":
                        transported = message.pop("transport_payload", None)
                        if not isinstance(transported, str):
                            raise ValueError("Native offer omitted its immutable source payload")
                        source_bytes = transported.encode("utf-8")
                        if parse_native_message(source_bytes) != message:
                            raise ValueError("Native offer differs from its source payload")
                        offer = OpenRequestMessage.model_validate(message)
                        if self.is_active() and self._handler and len(self._tasks) < 64:
                            if self._claim_offer(offer, source_bytes):
                                task = asyncio.create_task(self._deliver(offer.payload))
                                self._tasks.add(task)
                                task.add_done_callback(self._tasks.discard)
                except (ValueError, KeyError, TypeError):
                    _LOG.warning("Rejected invalid native protocol message")
        except (OSError, ValueError, RuntimeError):
            _LOG.warning("Native protocol transport terminated")
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
        finally:
            self._active = False
            failure = ConnectionError("Native host disconnected")
            if self._hello is not None and not self._hello.done():
                self._hello.set_exception(failure)
            for future in tuple(self._pending.values()):
                if not future.done():
                    future.set_exception(failure)
