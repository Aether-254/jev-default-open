from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _local_app_data() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))


def _bundled_native_host() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "native_host.exe"
    return Path(__file__).resolve().parents[2] / "native" / "build" / "Release" / "native_host.exe"


class AppConfig(BaseModel, frozen=True):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["standard", "demo"] = "demo"
    standard_deadline_ms: int = Field(default=3_000, gt=0)
    demo_deadline_ms: int = Field(default=20_000, gt=0)
    context_deadline_ms: int = Field(default=2_000, gt=0)
    # Protocol v2 encodes this deadline in native/include/protocol.hpp.
    # Retain the field for config compatibility, but reject values that the
    # native hook cannot honor instead of silently pretending to apply them.
    pipe_ack_deadline_ms: Literal[50] = 50
    duplicate_window_seconds: int = Field(default=30, gt=0)
    enabled_providers: tuple[str, ...] = ()
    # ``fixture`` is an explicit offline injection mode for tests/replay.  A
    # production authenticated transport is intentionally not configurable
    # until a version-specific adapter has been implemented and reviewed.
    qq_bridge_mode: Literal["disabled", "fixture"] = "disabled"
    # Optional read-only identity check for a locally installed QQ.exe.  An
    # empty value keeps runtime fingerprinting entirely disabled.
    qq_runtime_path: Path | None = None
    qq_allowed_versions: tuple[str, ...] = ()
    qq_allowed_sha256: tuple[str, ...] = ()
    blocked_uri_schemes: tuple[str, ...] = ("ms-settings", "shell", "search-ms")
    state_directory: Path = Field(default_factory=lambda: _local_app_data() / "JevDefaultOpen")
    native_host_path: Path = Field(
        default_factory=_bundled_native_host
    )
    path_labels: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    configured_actions: tuple[dict[str, object], ...] = ()
    profile_labels: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    conversation_labels: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    scenes: dict[str, str] | None = None

    @field_validator("enabled_providers")
    @classmethod
    def known_providers(cls, names: tuple[str, ...]) -> tuple[str, ...]:
        if set(names) - {"qq", "wechat", "feishu", "slack", "dingtalk"}:
            raise ValueError("Unknown IM provider")
        return tuple(dict.fromkeys(names))

    @field_validator("qq_runtime_path")
    @classmethod
    def absolute_qq_runtime_path(cls, path: Path | None) -> Path | None:
        if path is not None and not path.is_absolute():
            raise ValueError("qq_runtime_path must be absolute")
        return path

    @field_validator("qq_allowed_versions")
    @classmethod
    def valid_qq_versions(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(
            not value or len(value) > 128 or any(ord(char) < 32 for char in value)
            for value in values
        ):
            raise ValueError("qq_allowed_versions contains an invalid value")
        return tuple(dict.fromkeys(values))

    @field_validator("qq_allowed_sha256")
    @classmethod
    def valid_qq_sha256(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not re.fullmatch(r"[0-9A-Fa-f]{64}", value) for value in values):
            raise ValueError("qq_allowed_sha256 must contain 64-character hex digests")
        return tuple(dict.fromkeys(value.casefold() for value in values))

    @field_validator("scenes")
    @classmethod
    def valid_scenes(cls, scenes: dict[str, str] | None):
        if scenes is not None and (
            not 2 <= len(scenes) <= 255
            or any(not key or not value for key, value in scenes.items())
        ):
            raise ValueError("Scenes require 2..255 named non-empty descriptions")
        return scenes

    @model_validator(mode="after")
    def validate_qq_bridge_mode(self) -> AppConfig:
        if self.qq_bridge_mode == "fixture" and "qq" not in self.enabled_providers:
            raise ValueError("qq_bridge_mode=fixture requires qq in enabled_providers")
        return self

    @property
    def decision_deadline_ms(self) -> int:
        return self.demo_deadline_ms if self.mode == "demo" else self.standard_deadline_ms

    @property
    def context_budget_ms(self) -> int:
        limit = 2000 if self.mode == "demo" else 500
        return min(limit, self.context_deadline_ms, self.decision_deadline_ms)


def load_config(path: Path | None = None) -> AppConfig:
    """Load defaults followed by the user config and an optional override file."""
    state_dir = _local_app_data() / "JevDefaultOpen"
    candidates = [state_dir / "config.json"]
    if path is not None and path not in candidates:
        candidates.append(path)

    merged: dict[str, object] = {"state_directory": state_dir}
    for candidate in candidates:
        if not candidate.exists():
            continue
        with candidate.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError(f"Config root must be an object: {candidate}")
        merged.update(data)

    config = AppConfig.model_validate(merged)
    return config
