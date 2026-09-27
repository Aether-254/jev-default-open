from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from jev_open.domain import OpenRequest


class ProtocolHello(BaseModel, frozen=True):
    type: Literal["hello"] = "hello"
    protocol_version: int = Field(ge=1)
    process_id: int = Field(gt=0)
    nonce: str


class OpenRequestMessage(BaseModel, frozen=True):
    type: Literal["open_request"] = "open_request"
    payload: OpenRequest


class Acknowledgement(BaseModel, frozen=True):
    type: Literal["ack"] = "ack"
    request_id: str
    accepted: bool
    reason: str | None = None
