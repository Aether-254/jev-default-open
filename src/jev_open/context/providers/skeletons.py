from __future__ import annotations

from datetime import datetime

from jev_open.domain import ChatContext, OpenRequest


class _SkeletonProvider:
    executable_names: frozenset[str] = frozenset()

    def supports(self, request: OpenRequest) -> bool:
        return request.source_executable.name.casefold() in self.executable_names

    async def resolve(self, request: OpenRequest, deadline: datetime) -> ChatContext | None:
        # TODO: Implement official API auth, message correlation, and fixture contract.
        return None


class FeishuContextProvider(_SkeletonProvider):
    provider_id = "feishu"
    executable_names = frozenset({"feishu.exe", "lark.exe"})


class SlackContextProvider(_SkeletonProvider):
    provider_id = "slack"
    executable_names = frozenset({"slack.exe"})


class DingTalkContextProvider(_SkeletonProvider):
    provider_id = "dingtalk"
    executable_names = frozenset({"dingtalk.exe", "dingtalklauncher.exe"})
