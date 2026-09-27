from __future__ import annotations

from datetime import datetime

from jev_open.domain import ChatContext, OpenRequest


class WeChatContextProvider:
    provider_id = "wechat"

    def supports(self, request: OpenRequest) -> bool:
        return request.source_executable.name.casefold() in {"wechat.exe", "weixin.exe"}

    async def resolve(self, request: OpenRequest, deadline: datetime) -> ChatContext | None:
        # TODO: Probe the version adapter for account and active conversation.
        # TODO: Read a consistent, read-only local DB snapshot when supported.
        # TODO: Fall back to UI Automation and then chat-region OCR.
        # TODO: Reject unverified or ambiguous text rather than sending bad context.
        raise NotImplementedError("TODO: resolve WeChat context")
