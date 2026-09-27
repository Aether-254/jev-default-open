from __future__ import annotations

from datetime import datetime

from jev_open.domain import ChatContext, OpenRequest

from .uia import VerifiedVisibleContextReader, visible_text_context


class WeChatContextProvider:
    provider_id = "wechat"

    def __init__(
        self, *, enabled: bool = False, reader: VerifiedVisibleContextReader | None = None
    ) -> None:
        self._enabled = enabled
        self._reader = reader
        self.status = "requires_verified_adapter" if enabled else "disabled"

    def supports(self, request: OpenRequest) -> bool:
        return self._enabled and request.source_executable.name.casefold() in {
            "wechat.exe",
            "weixin.exe",
        }

    def stop(self) -> None:
        self._enabled = False
        self.status = "disabled"

    async def resolve(self, request: OpenRequest, deadline: datetime) -> ChatContext | None:
        if not self.supports(request):
            return None
        return await visible_text_context(
            request, provider=self.provider_id, reader=self._reader, deadline=deadline
        )
