from __future__ import annotations

import asyncio
import os

from .actions.windows import WindowsOpenActionModule
from .broker import BrokerApplication
from .config import AppConfig, load_config
from .context.module import DefaultContextModule
from .context.providers.qq import QQContextProvider
from .context.providers.skeletons import (
    DingTalkContextProvider,
    FeishuContextProvider,
    SlackContextProvider,
)
from .context.providers.wechat import WeChatContextProvider
from .decision.jev import JevDecisionModule
from .diagnostics import run_self_tests
from .interception.native import NativeInterceptionModule
from .persistence.sqlcipher import SQLCipherStateModule
from .ui.qt import QtUserInterfaceModule


def build_application(config: AppConfig | None = None) -> BrokerApplication:
    """Composition root for concrete adapters."""
    config = config or load_config()
    state = SQLCipherStateModule(config.state_directory / "state.db")
    overlay = asyncio.run(state.get_setting("application_config", {}))
    if overlay:
        allowed = {
            "mode",
            "enabled_providers",
            "qq_bridge_mode",
            "qq_runtime_path",
            "qq_allowed_versions",
            "qq_allowed_sha256",
            "path_labels",
            "profile_labels",
            "conversation_labels",
            "configured_actions",
            "scenes",
            "standard_deadline_ms",
            "demo_deadline_ms",
            "context_deadline_ms",
        }
        if not isinstance(overlay, dict) or set(overlay) - allowed:
            raise ValueError("Unsupported application settings; review the encrypted overlay")
        config = AppConfig.model_validate({**config.model_dump(), **overlay})
    api_key = os.environ.get("TYPESAFE_API_KEY", "")
    base_url = os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai/v1")
    model = os.environ.get("TYPESAFE_MODEL", "jev-1.13.0")
    factories = {
        "qq": QQContextProvider,
        "wechat": WeChatContextProvider,
        "feishu": FeishuContextProvider,
        "slack": SlackContextProvider,
        "dingtalk": DingTalkContextProvider,
    }
    providers = tuple(
        (
            factories[name](enabled=(name == "wechat" or config.qq_bridge_mode == "fixture"))
            if name in {"qq", "wechat"}
            else factories[name]()
        )
        for name in config.enabled_providers
    )
    ui = QtUserInterfaceModule(
        self_test=lambda: run_self_tests(config), config=config, scenes=config.scenes
    )
    return BrokerApplication(
        interception=NativeInterceptionModule(config.native_host_path),
        context=DefaultContextModule(
            providers,
            blocked_uri_schemes=config.blocked_uri_schemes,
            path_labels=config.path_labels,
            conversation_labels=config.conversation_labels,
            provenance=state,
            enabled_providers=config.enabled_providers,
        ),
        actions=WindowsOpenActionModule(
            config.configured_actions, profile_labels=config.profile_labels
        ),
        decision=JevDecisionModule(
            model=model,
            api_key=api_key,
            base_url=base_url,
            scenes=config.scenes,
            cache_store=state,
        ),
        state=state,
        ui=ui,
        decision_deadline_ms=config.decision_deadline_ms,
        context_deadline_ms=config.context_budget_ms,
        duplicate_window_seconds=config.duplicate_window_seconds,
        ready_check=lambda: ui.ready_to_enable,
    )
