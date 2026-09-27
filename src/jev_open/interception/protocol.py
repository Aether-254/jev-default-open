from __future__ import annotations

import json
import ntpath
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from jev_open.domain import OpenRequest

PROTOCOL_VERSION = 2
MAX_MESSAGE_BYTES = 128 * 1024
MAX_TRANSPORT_BYTES = MAX_MESSAGE_BYTES * 3 + 4096
OWNERSHIP_PREFIX = "Local\\JevOpenRequest-"
_OWNERSHIP_NAME = re.compile(r"Local\\JevOpenRequest-[1-9][0-9]*-[a-fA-F0-9-]{36}\Z")


class ProtocolHello(BaseModel, frozen=True):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["hello"] = "hello"
    protocol_version: Literal[2] = PROTOCOL_VERSION
    process_id: int = Field(gt=0)
    nonce: str = Field(min_length=16, max_length=128)
    test_mode: bool = False


class OpenRequestMessage(BaseModel, frozen=True):
    model_config = ConfigDict(extra="forbid")
    type: Literal["open_request"] = "open_request"
    protocol_version: Literal[2] = PROTOCOL_VERSION
    ownership: str
    payload: OpenRequest

    @field_validator("ownership")
    @classmethod
    def validate_ownership(cls, value: str) -> str:
        if not _OWNERSHIP_NAME.fullmatch(value):
            raise ValueError("Invalid ownership mapping name")
        return value

    @field_validator("payload")
    @classmethod
    def validate_payload(cls, request: OpenRequest) -> OpenRequest:
        validate_request(request)
        return request

    @field_validator("payload", mode="before")
    @classmethod
    def require_native_scalars(cls, value: object) -> object:
        if isinstance(value, dict):
            for name in ("source_pid", "show_command", "shell_execute_flags"):
                if name in value and type(value[name]) is not int:
                    raise ValueError(f"Native {name} must be an integer")
            for name in ("request_id", "source_executable", "target", "parameters", "verb"):
                if name in value and not isinstance(value[name], str):
                    raise ValueError(f"Native {name} must be a string")
        return value


class Acknowledgement(BaseModel, frozen=True):
    type: Literal["ack"] = "ack"
    request_id: str = Field(min_length=1, max_length=128)
    accepted: bool
    reason: str | None = None


def validate_request(request: OpenRequest) -> None:
    if not request.request_id or len(request.request_id) > 128:
        raise ValueError("Invalid request id")
    if not request.target or len(request.target) > 32767 or "\0" in request.target:
        raise ValueError("Invalid open target")
    for value in (request.parameters, str(request.source_executable)):
        if len(value) > 32767 or "\0" in value:
            raise ValueError("Invalid request text")
    if not ntpath.isabs(str(request.source_executable)):
        raise ValueError("Source executable must be an absolute Windows path")
    if request.working_directory is not None:
        directory = str(request.working_directory)
        if not ntpath.isabs(directory) or "\0" in directory or len(directory) > 32767:
            raise ValueError("Working directory must be an absolute Windows path")
    if request.source_pid > 0xFFFFFFFF or not 0 <= request.shell_execute_flags <= 0xFFFFFFFF:
        raise ValueError("Invalid native integer")
    if (
        request.foreground_hwnd is not None
        and not 0 <= request.foreground_hwnd <= 0xFFFFFFFFFFFFFFFF
    ):
        raise ValueError("Invalid window handle")
    if not 0 <= request.show_command <= 11:
        raise ValueError("Invalid show command")
    if request.captured_at.tzinfo is None:
        raise ValueError("Native timestamp must include its UTC offset")


def parse_native_message(
    line: bytes, *, max_bytes: int = MAX_MESSAGE_BYTES,
) -> dict[str, object]:
    if len(line) > max_bytes:
        raise ValueError("Native message exceeds its size limit")
    value = json.loads(line)
    if not isinstance(value, dict):
        raise ValueError("Native message must be an object")
    return value
