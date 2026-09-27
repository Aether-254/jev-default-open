from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class AppConfig(BaseModel, frozen=True):
    mode: Literal["standard", "demo"] = "demo"
    standard_deadline_ms: int = Field(default=3_000, gt=0)
    demo_deadline_ms: int = Field(default=20_000, gt=0)
    context_deadline_ms: int = Field(default=2_000, gt=0)
    pipe_ack_deadline_ms: int = Field(default=50, gt=0)
    duplicate_window_seconds: int = Field(default=30, gt=0)
    enabled_providers: tuple[str, ...] = ("qq", "wechat")
    blocked_uri_schemes: tuple[str, ...] = ("ms-settings", "shell", "search-ms")
    state_directory: Path
    native_host_path: Path

    @property
    def decision_deadline_ms(self) -> int:
        return self.demo_deadline_ms if self.mode == "demo" else self.standard_deadline_ms


def load_config(path: Path | None = None) -> AppConfig:
    """Load user configuration without reading secrets.

    # TODO: Merge defaults, the LocalAppData config file, and an optional path.
    # TODO: Validate that native paths remain inside the installation directory.
    """
    raise NotImplementedError("TODO: load application configuration")
