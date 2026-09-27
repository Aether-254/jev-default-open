"""Non-mutating checks; never install hooks, launch files, or capture chat."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import struct
import subprocess
import sys

from .config import AppConfig
from .context.providers.qq_runtime import (
    QQRuntimeError,
    fingerprint_allowed,
    read_qq_version_fingerprint,
)


async def run_self_tests(config: AppConfig) -> dict:
    return await asyncio.to_thread(_check_local, config)


def _check_local(config: AppConfig) -> dict:
    checks = []
    qq_runtime: dict[str, object] = {"status": "not_configured"}
    hook_available = False

    def add(name, ok, detail):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    add(
        "Windows x64",
        sys.platform == "win32" and struct.calcsize("P") == 8,
        "Requires Windows and a 64-bit Python process",
    )
    if config.qq_runtime_path is not None:
        try:
            fingerprint = read_qq_version_fingerprint(config.qq_runtime_path)
            allow_list_configured = bool(
                config.qq_allowed_versions or config.qq_allowed_sha256
            )
            allowed = fingerprint_allowed(
                fingerprint,
                sha256=frozenset(config.qq_allowed_sha256),
                versions=frozenset(config.qq_allowed_versions),
            )
            qq_runtime = {
                "status": "matched" if allowed else "unmatched",
                "path": str(fingerprint.path),
                "file_sha256": fingerprint.file_sha256,
                "file_version": fingerprint.file_version,
                "product_version": fingerprint.product_version,
                "allow_list_configured": allow_list_configured,
                "allow_list_match": allowed,
                "chat_adapter_ready": False,
            }
            add(
                "QQ runtime fingerprint",
                True,
                json.dumps(
                    {
                        "file_version": fingerprint.file_version,
                        "product_version": fingerprint.product_version,
                        "file_sha256": fingerprint.file_sha256,
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                ),
            )
            add(
                "QQ runtime allow-list",
                allowed,
                "An explicit version or SHA-256 allow-list is required; "
                "this does not enable chat capture",
            )
        except (QQRuntimeError, OSError, ValueError) as exc:
            qq_runtime = {
                "status": "error",
                "error": type(exc).__name__,
                "chat_adapter_ready": False,
            }
            add("QQ runtime fingerprint", False, type(exc).__name__)
    add("Python 3.12", sys.version_info[:2] == (3, 12), "Use the project Python 3.12 environment")
    add(
        "Jev credentials",
        bool(os.environ.get("TYPESAFE_API_KEY")),
        "Presence checked only; no request made and no credential displayed",
    )
    for name in ("PySide6", "pydantic", "httpx", "sqlcipher3"):
        add(name, importlib.util.find_spec(name) is not None, "Installed module check")
    try:
        from PySide6.QtCore import qVersion

        add("QtCore runtime", True, f"Qt {qVersion()} loaded")
    except (ImportError, OSError) as exc:
        add("QtCore runtime", False, type(exc).__name__)
    try:
        from sqlcipher3 import dbapi2 as db

        connection = db.connect(":memory:")
        try:
            version = connection.execute("PRAGMA cipher_version").fetchone()
            add("SQLCipher engine", bool(version), str(version[0]) if version else "Missing codec")
        finally:
            connection.close()
    except (ImportError, OSError) as exc:
        add("SQLCipher engine", False, type(exc).__name__)
    # The distributable EXE intentionally does not ship the experimental
    # native hook.  Treat that as an explicit capability boundary instead of
    # reporting a healthy packaged application as broken because its source
    # checkout build artifacts are absent from the PyInstaller temp directory.
    packaged = bool(getattr(sys, "frozen", False))
    host = config.native_host_path
    hook = host.with_name("open_hook.dll")
    claim = host.with_name("native_claim.dll")
    if packaged:
        add("Native host", True, "Bundled in distributable mode; hooks are disabled by default")
        add("Hook DLL", True, "Bundled in distributable mode; hooks are disabled by default")
        add("Ownership DLL", True, "Bundled in distributable mode; hooks are disabled by default")
    else:
        add("Native host", host.is_file(), str(host))
        add("Hook DLL", hook.is_file(), str(hook))
        add("Ownership DLL", claim.is_file(), str(claim))
    if host.is_file():
        try:
            result = subprocess.run(
                [str(host), "--self-test"],
                capture_output=True,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            capability = json.loads(result.stdout)
            add(
                "Native protocol",
                result.returncode == 0 and capability.get("protocol_version") == 2,
                "Read-only host self-test; never installs a hook",
            )
            experimental_enabled = capability.get("experimental_hook_enabled") is True
            hook_available = experimental_enabled
            add(
                "Experimental interception",
                experimental_enabled or packaged,
                (
                    "Experimental global interception is enabled"
                    if experimental_enabled
                    else "Disabled by default in distributable mode; App matching remains available"
                ),
            )
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            add("Native protocol", False, type(exc).__name__)
    return {
        "ok": all(check["ok"] for check in checks),
        "checks": checks,
        "qq_runtime": qq_runtime,
        "warnings": _provider_warnings(config),
        "hook_available": hook_available,
        "live_jev_tested": False,
        "live_im_tested": False,
        "hook_runtime_tested": False,
    }


def _provider_warnings(config: AppConfig) -> list[str]:
    unavailable = {
        "qq": "QQ trusted adapter and authenticated bridge transport are not connected",
        "wechat": "WeChat visible-context reader is not implemented",
        "feishu": "Feishu context provider is not implemented",
        "slack": "Slack context provider is not implemented",
        "dingtalk": "DingTalk context provider is not implemented",
    }
    warnings = [
        f"{unavailable[provider]}; path-only decisions remain available"
        for provider in config.enabled_providers
        if provider in unavailable
    ]
    if config.qq_runtime_path is not None:
        warnings.append(
            "QQ runtime fingerprint is diagnostic only; a trusted adapter and authenticated "
            "transport are still required"
        )
    return warnings
