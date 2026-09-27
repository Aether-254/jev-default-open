from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jev_open.domain import ChatContext, ContextEnvelope, OpenRequest

from .interface import ChatContextProvider


class DefaultContextModule:
    def __init__(self, providers: tuple[ChatContextProvider, ...]) -> None:
        self._providers = providers

    async def assemble(self, request: OpenRequest, deadline: datetime) -> ContextEnvelope:
        # TODO: Normalize target into FileTarget or UrlTarget.
        # TODO: Reject dangerous executable, script, folder, and system-URI targets.
        # TODO: Resolve source application and account scope.
        # TODO: Restore IM provenance when Explorer opens a downloaded file.
        # TODO: Resolve the matching provider within the context sub-deadline.
        # TODO: Apply the longest matching path-label prefix.
        # TODO: Populate actions in a later orchestration step or accept a catalog dependency.
        raise NotImplementedError("TODO: assemble ContextEnvelope")

    async def _resolve_chat(
        self, request: OpenRequest, deadline: datetime
    ) -> ChatContext | None:
        # TODO: Try only enabled providers that support the source process.
        # TODO: Validate provider output before returning it.
        raise NotImplementedError("TODO: resolve chat context")

    @staticmethod
    def _normalize_path(path: str) -> Path:
        # TODO: Resolve case and separators without dereferencing unsafe links.
        return Path(path)
