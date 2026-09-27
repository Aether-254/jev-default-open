from __future__ import annotations

from datetime import datetime

from jev_open.domain import ChatContext, OpenRequest


class QQContextProvider:
    provider_id = "qq"

    def supports(self, request: OpenRequest) -> bool:
        return request.source_executable.name.casefold() == "qq.exe"

    async def resolve(self, request: OpenRequest, deadline: datetime) -> ChatContext | None:
        # TODO: Correlate LiteLoader bridge events by target and timestamp.
        # TODO: Query pinned qqcli JSON output for messages around the target.
        # TODO: Fall back to bridge-provided visible context when DB access fails.
        # TODO: Validate QQ version compatibility and attach warnings.
        raise NotImplementedError("TODO: resolve QQ context")
