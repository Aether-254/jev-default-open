from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import AnyUrl, BaseModel, Field


class OpenVerb(StrEnum):
    OPEN = "open"
    EDIT = "edit"


class RequestStatus(StrEnum):
    RECEIVED = "received"
    DECIDING = "deciding"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    LAUNCHED = "launched"
    CANCELLED = "cancelled"
    FALLBACK = "fallback"
    FAILED = "failed"


class FileTarget(BaseModel, frozen=True):
    kind: Literal["file"] = "file"
    path: Path
    extension: str
    mime_type: str | None = None
    size: int | None = Field(default=None, ge=0)
    modified_at: datetime | None = None


class UrlTarget(BaseModel, frozen=True):
    kind: Literal["url"] = "url"
    url: AnyUrl
    scheme: str
    host: str | None = None


OpenTarget = Annotated[FileTarget | UrlTarget, Field(discriminator="kind")]


class OpenRequest(BaseModel, frozen=True):
    request_id: str
    source_pid: int = Field(gt=0)
    source_executable: Path
    foreground_hwnd: int | None = None
    verb: OpenVerb
    target: str
    parameters: str = ""
    working_directory: Path | None = None
    show_command: int = 1
    shell_execute_flags: int = 0
    captured_at: datetime


class ChatMessage(BaseModel, frozen=True):
    message_id: str | None = None
    sender: str | None = None
    timestamp: datetime | None = None
    message_type: Literal[
        "text",
        "file",
        "link",
        "image",
        "audio",
        "video",
        "card",
        "system",
        "unknown",
    ]
    text: str | None = None
    attachment_name: str | None = None
    attachment_path: Path | None = None
    url: str | None = None


class ChatContext(BaseModel, frozen=True):
    provider: str
    account_id_hash: str
    conversation_id: str | None = None
    conversation_title: str | None = None
    conversation_labels: tuple[str, ...] = ()
    target_message_id: str | None = None
    messages: tuple[ChatMessage, ...] = ()
    acquisition: Literal["internal", "database", "official_api", "uia", "ocr"]
    confidence: float = Field(ge=0.0, le=1.0)
    warnings: tuple[str, ...] = ()


class OpenProfile(BaseModel, frozen=True):
    profile_id: str
    application_id: str
    display_name: str
    semantic_labels: tuple[str, ...] = ()
    launch_arguments: tuple[str, ...] = ()


class OpenAction(BaseModel, frozen=True):
    action_id: str
    application_id: str
    profile_id: str | None = None
    display_name: str
    capability_description: str
    invocation_kind: Literal["assoc_handler", "executable", "packaged_app"]
    invocation_data: dict[str, Any]


class ContextEnvelope(BaseModel, frozen=True):
    request: OpenRequest
    target: OpenTarget
    source_application: str
    source_account_hash: str | None = None
    chat_context: ChatContext | None = None
    path_labels: tuple[str, ...] = ()
    open_actions: tuple[OpenAction, ...] = ()


class SceneDecision(BaseModel, frozen=True):
    scene_id: str | None
    probabilities: dict[str, float] = Field(default_factory=dict)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evaluated: bool = True


class OpenDecision(BaseModel, frozen=True):
    source: Literal["jev", "preference", "exact_cache", "system_fallback"]
    action_id: str | None
    probabilities: dict[str, float] = Field(default_factory=dict)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    scene: SceneDecision
    latency_ms: int = Field(ge=0)
    warnings: tuple[str, ...] = ()


class PreferenceRule(BaseModel, frozen=True):
    rule_id: str
    scope: Literal[
        "exact_target",
        "conversation",
        "path_label",
        "path_prefix",
        "provider",
        "extension_or_mime",
        "url_domain",
        "global",
    ]
    selector: dict[str, str]
    action_id: str
    created_at: datetime
    updated_at: datetime
