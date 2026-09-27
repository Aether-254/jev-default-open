from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from jev_open.domain import ChatContext, ContextEnvelope, FileTarget, OpenRequest, UrlTarget
from jev_open.domain.errors import UnsupportedTarget

from .interface import ChatContextProvider
from .providers.validation import validated_chat

_DANGEROUS_EXTENSIONS = {
    ".bat",
    ".cmd",
    ".com",
    ".cpl",
    ".dll",
    ".exe",
    ".js",
    ".jse",
    ".lnk",
    ".msi",
    ".msix",
    ".ps1",
    ".scr",
    ".sys",
    ".vbs",
    ".wsf",
}


class ProvenanceStore(Protocol):
    async def remember_provenance(self, target: FileTarget, chat: ChatContext) -> None: ...

    async def restore_provenance(
        self, target: FileTarget, enabled_providers: set[str]
    ) -> ChatContext | None: ...


class DefaultContextModule:
    def __init__(
        self,
        providers: tuple[ChatContextProvider, ...],
        *,
        blocked_uri_schemes: tuple[str, ...] = ("ms-settings", "shell", "search-ms"),
        path_labels: dict[str, tuple[str, ...]] | None = None,
        conversation_labels: dict[str, tuple[str, ...]] | None = None,
        provenance: ProvenanceStore | None = None,
        enabled_providers: tuple[str, ...] = (),
    ) -> None:
        self._providers = providers
        self._blocked_uri_schemes = {scheme.casefold() for scheme in blocked_uri_schemes}
        self._path_labels = {
            os.path.normcase(os.path.abspath(prefix)): labels
            for prefix, labels in (path_labels or {}).items()
        }
        self._conversation_labels = conversation_labels or {}
        self._provenance = provenance
        self._enabled_providers = frozenset(enabled_providers)

    async def assemble(self, request: OpenRequest, deadline: datetime) -> ContextEnvelope:
        if not request.target or any(ord(char) < 32 for char in request.target):
            raise UnsupportedTarget("Target is empty or contains control characters")
        parsed = urlparse(request.target)
        if parsed.scheme and not _looks_like_windows_drive(request.target):
            if request.verb == "edit":
                raise UnsupportedTarget("URL edit requests must retain Windows behavior")
            if parsed.scheme.casefold() in self._blocked_uri_schemes:
                raise UnsupportedTarget(f"Blocked system URI scheme: {parsed.scheme}")
            target = UrlTarget(url=request.target, scheme=parsed.scheme, host=parsed.hostname)
            labels: tuple[str, ...] = ()
        else:
            path = self._normalize_path(request.target, request.working_directory)
            if path.suffix.casefold() in _DANGEROUS_EXTENSIONS:
                raise UnsupportedTarget(f"Unsafe or unsupported file target: {path}")
            # Network metadata can block indefinitely and disclose authentication.
            # Leave remote and device targets to the original Shell request.
            if str(path).startswith(("\\\\", "//")):
                raise UnsupportedTarget("Remote and device paths retain Windows behavior")
            remaining = (deadline - datetime.now(tz=deadline.tzinfo)).total_seconds()
            if remaining <= 0:
                raise TimeoutError("Context assembly deadline expired")
            async with asyncio.timeout(remaining):
                is_directory, size, modified = await asyncio.to_thread(_file_metadata, path)
            if is_directory:
                raise UnsupportedTarget("Directory navigation retains Windows behavior")
            target = FileTarget(
                path=path,
                extension=path.suffix.casefold(),
                mime_type=mimetypes.guess_type(path.name)[0],
                size=size,
                modified_at=modified,
            )
            labels = self._labels_for(path)

        chat = await self._resolve_chat(request, deadline)
        if isinstance(target, FileTarget) and self._provenance is not None:
            if chat is not None and chat.provider in self._enabled_providers:
                await self._remember_provenance(target, chat, deadline)
            elif chat is None and request.source_executable.name.casefold() == "explorer.exe":
                chat = await self._restore_provenance(target, request, deadline)
        if chat is not None:
            labels_key = conversation_label_key(
                chat.provider, chat.account_id_hash, chat.conversation_id or ""
            )
            conversation_tags = self._conversation_labels.get(labels_key, ())
            if conversation_tags:
                chat = chat.model_copy(update={"conversation_labels": conversation_tags})
        return ContextEnvelope(
            request=request,
            target=target,
            source_application=request.source_executable.stem,
            source_account_hash=chat.account_id_hash if chat else None,
            chat_context=chat,
            path_labels=labels,
            open_actions=(),
        )

    async def _resolve_chat(self, request: OpenRequest, deadline: datetime) -> ChatContext | None:
        for provider in self._providers:
            remaining = (deadline - datetime.now(tz=deadline.tzinfo)).total_seconds()
            if remaining <= 0:
                return None
            try:
                if not provider.supports(request):
                    continue
                async with asyncio.timeout(remaining):
                    context = await provider.resolve(request, deadline)
                context = validated_chat(context, request)
            except Exception:
                # Provider evidence is optional. Cancellation still propagates.
                continue
            if context is not None:
                return context
        return None

    async def _remember_provenance(
        self, target: FileTarget, chat: ChatContext, deadline: datetime
    ) -> None:
        remaining = (deadline - datetime.now(tz=deadline.tzinfo)).total_seconds()
        if remaining <= 0 or self._provenance is None:
            return
        try:
            async with asyncio.timeout(min(remaining, 0.1)):
                await self._provenance.remember_provenance(target, chat)
        except Exception:
            # Provenance is auxiliary; failure must not replace verified live context.
            return

    async def _restore_provenance(
        self, target: FileTarget, request: OpenRequest, deadline: datetime
    ) -> ChatContext | None:
        remaining = (deadline - datetime.now(tz=deadline.tzinfo)).total_seconds()
        if remaining <= 0 or self._provenance is None or not self._enabled_providers:
            return None
        try:
            async with asyncio.timeout(min(remaining, 0.1)):
                chat = await self._provenance.restore_provenance(
                    target, set(self._enabled_providers)
                )
        except Exception:
            return None
        if chat is None or chat.provider not in self._enabled_providers:
            return None
        exact_request = request.model_copy(update={"target": str(target.path)})
        chat = validated_chat(chat, exact_request)
        if chat is None:
            return None
        return chat.model_copy(
            update={
                "warnings": (
                    *chat.warnings,
                    "Restored verified file provenance; not live chat context.",
                ),
            }
        )

    @staticmethod
    def _normalize_path(path: str, working_directory: Path | None = None) -> Path:
        value = Path(path)
        if not value.is_absolute():
            value = (working_directory or Path.cwd()) / value
        return Path(os.path.abspath(value))

    def stop(self) -> None:
        for provider in self._providers:
            stop = getattr(provider, "stop", None)
            if stop is not None:
                stop()

    def _labels_for(self, path: Path) -> tuple[str, ...]:
        normalized = os.path.normcase(str(path))
        matches = [
            (prefix, labels)
            for prefix, labels in self._path_labels.items()
            if normalized == prefix or normalized.startswith(prefix.rstrip("\\/") + os.sep)
        ]
        return max(matches, key=lambda item: len(item[0]))[1] if matches else ()


def _looks_like_windows_drive(value: str) -> bool:
    return len(value) >= 2 and value[1] == ":" and value[0].isalpha()


def conversation_label_key(provider: str, account_hash: str, conversation_id: str) -> str:
    """Collision-free config key: compact JSON array of the three identity parts."""
    return json.dumps([provider, account_hash, conversation_id], separators=(",", ":"))


def _file_metadata(path: Path) -> tuple[bool, int | None, datetime | None]:
    try:
        metadata = path.stat()
        return (
            stat.S_ISDIR(metadata.st_mode),
            metadata.st_size,
            datetime.fromtimestamp(metadata.st_mtime, tz=UTC),
        )
    except OSError:
        return False, None, None
