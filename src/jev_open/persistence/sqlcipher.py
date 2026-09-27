from __future__ import annotations

import asyncio
import ctypes
import hashlib
import hmac
import importlib
import json
import os
import secrets
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from jev_open.domain import ChatContext, ContextEnvelope, FileTarget, OpenDecision, PreferenceRule

from .dpapi import protect, unprotect
from .preferences import rule_matches, rule_priority
from .provenance import fingerprint_file, known_path_exists


def _move_no_replace(source: Path, destination: Path) -> None:
    """Atomically publish a new file without replacing an existing one.

    Python's ``os.rename`` can return ERROR_NOT_SAME_DEVICE inside an EFS
    encrypted directory even when both paths share that directory. The Win32
    API used directly does not have that CRT failure mode.
    """
    if os.name != "nt":
        os.link(source, destination)
        return
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    move_file = kernel32.MoveFileW
    move_file.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    move_file.restype = ctypes.c_int
    if move_file(str(source), str(destination)):
        return
    error = ctypes.get_last_error()
    if error in {80, 183}:
        raise FileExistsError(error, os.strerror(error), str(destination))
    raise OSError(error, os.strerror(error), str(source), str(destination))


class StateSecurityError(RuntimeError):
    """Secure state cannot be opened; callers must not fall back to plaintext."""


def _cipher_driver() -> Any:
    try:
        driver = importlib.import_module("sqlcipher3.dbapi2")
    except ImportError as exc:
        raise StateSecurityError(
            "SQLCipher is required. Install the pinned sqlcipher3 dependency; "
            "plaintext SQLite fallback is disabled."
        ) from exc
    connection = driver.connect(":memory:")
    try:
        version = connection.execute("PRAGMA cipher_version").fetchone()
        if not version or not version[0]:
            raise StateSecurityError("The database driver does not provide SQLCipher encryption.")
    finally:
        connection.close()
    return driver


class SQLCipherStateModule:
    """SQLCipher encrypted state with a DPAPI CurrentUser-wrapped random key.

    Existing plaintext files are rejected, never silently migrated or deleted.
    Operations own short-lived connections; executor threads never share connections.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._key_path = self._path.with_name(self._path.name + ".key")
        self._lock = threading.RLock()
        self._cache_epoch = 0
        self._provenance_epoch = 0
        self._provider_epochs: dict[str, int] = {}
        self._driver = _cipher_driver()
        if self._path.exists():
            with self._path.open("rb") as source:
                if source.read(16) == b"SQLite format 3\x00":
                    raise StateSecurityError(
                        "The existing state file is plaintext SQLite. Preserve it and explicitly "
                        "choose a new state location; automatic plaintext migration is disabled."
                    )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._key = self._load_key()
        self._initialize()

    def _load_key(self) -> bytes:
        if self._key_path.exists():
            try:
                key = unprotect(self._key_path.read_bytes())
            except (OSError, ValueError) as exc:
                raise StateSecurityError(
                    "The state key cannot be unwrapped by this Windows user. "
                    "The database and key have not been reset."
                ) from exc
            if len(key) != 32:
                raise StateSecurityError("Invalid state key length; refusing to open the database.")
            return key
        if self._path.exists() and self._path.stat().st_size:
            raise StateSecurityError("The database exists but its DPAPI key file is missing.")
        key = secrets.token_bytes(32)
        protected = protect(key, "Jev Default Open SQLCipher key")
        temporary = self._key_path.with_name(self._key_path.name + "." + secrets.token_hex(8))
        try:
            with temporary.open("xb") as destination:
                destination.write(protected)
                destination.flush()
                os.fsync(destination.fileno())
            # Never overwrite a key created by another simultaneous first launch.
            try:
                _move_no_replace(temporary, self._key_path)
            except FileExistsError:
                return self._load_key()
        finally:
            temporary.unlink(missing_ok=True)
        return key

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        with self._lock:
            connection = self._driver.connect(str(self._path), timeout=5)
            try:
                connection.execute(f"PRAGMA key = \"x'{self._key.hex()}'\"")
                connection.execute("PRAGMA cipher_compatibility = 4")
                connection.execute("PRAGMA temp_store = MEMORY")
                connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("PRAGMA secure_delete = ON")
                connection.row_factory = self._driver.Row
                with connection:
                    yield connection
            except self._driver.DatabaseError as exc:
                raise StateSecurityError(
                    "Encrypted state could not be read or updated; verify the key and database "
                    "integrity. No plaintext fallback was attempted."
                ) from exc
            finally:
                connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise StateSecurityError(
                    "The encrypted state schema is newer than this application."
                )
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS preference_rules (
                    rule_id TEXT PRIMARY KEY, rule_json TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS decision_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL UNIQUE,
                    context_json TEXT NOT NULL, decision_json TEXT NOT NULL,
                    final_action_id TEXT, final_scene_id TEXT, status TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (
                    name TEXT PRIMARY KEY, value_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS exact_model_cache (
                    cache_key TEXT PRIMARY KEY, decision_json TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS file_provenance (
                    record_id TEXT PRIMARY KEY, provider TEXT NOT NULL,
                    account_id_hash TEXT NOT NULL, path TEXT NOT NULL, payload_json TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL, missing_since TEXT
                );
                PRAGMA user_version = 1;
                """
            )
            self.cipher_version: str = connection.execute("PRAGMA cipher_version").fetchone()[0]

    async def find_preference(self, context: ContextEnvelope) -> PreferenceRule | None:
        matching = [rule for rule in await self.list_preferences() if rule_matches(rule, context)]
        return max(matching, key=rule_priority, default=None)

    async def save_preference(self, rule: PreferenceRule) -> None:
        await asyncio.to_thread(self._save_preference_sync, rule)

    def _save_preference_sync(self, rule: PreferenceRule) -> None:
        if not rule.selector or not rule.action_id.strip():
            raise ValueError("A preference requires a nonempty selector and action ID.")
        if rule.created_at.tzinfo is None or rule.updated_at.tzinfo is None:
            raise ValueError("Preference timestamps must include a timezone.")
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO preference_rules(rule_id, rule_json, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(rule_id) DO UPDATE SET rule_json=excluded.rule_json,
                    updated_at=excluded.updated_at""",
                (rule.rule_id, rule.model_dump_json(), rule.updated_at.astimezone(UTC).isoformat()),
            )
            self._invalidate_cache(connection)

    async def list_preferences(self) -> list[PreferenceRule]:
        return await asyncio.to_thread(self._list_preferences_sync)

    def _list_preferences_sync(self) -> list[PreferenceRule]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT rule_json FROM preference_rules ORDER BY updated_at DESC, rule_id"
            ).fetchall()
        return [PreferenceRule.model_validate_json(row[0]) for row in rows]

    async def delete_preference(self, rule_id: str) -> None:
        await asyncio.to_thread(self._delete_preference_sync, rule_id)

    def _delete_preference_sync(self, rule_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM preference_rules WHERE rule_id=?", (rule_id,))
            self._invalidate_cache(connection)

    async def record_decision(
        self,
        context: ContextEnvelope,
        decision: OpenDecision,
        final_action_id: str | None,
        *,
        final_scene_id: str | None = None,
        status: str | None = None,
    ) -> None:
        await asyncio.to_thread(
            self._record_decision_sync,
            context,
            decision,
            final_action_id,
            final_scene_id,
            status,
        )

    def _record_decision_sync(
        self,
        context: ContextEnvelope,
        decision: OpenDecision,
        final_action_id: str | None,
        final_scene_id: str | None = None,
        status: str | None = None,
    ) -> None:
        if status is None:
            status = "launched" if final_action_id else "cancelled"
            if decision.source == "system_fallback":
                status = "fallback"
        allowed = {
            "received",
            "deciding",
            "awaiting_confirmation",
            "launched",
            "cancelled",
            "fallback",
            "failed",
            "cancelled_by_user",
        }
        if status not in allowed:
            raise ValueError("Unknown decision outcome.")
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO decision_history
                    (request_id, context_json, decision_json, final_action_id, final_scene_id,
                     status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(request_id) DO UPDATE SET context_json=excluded.context_json,
                    decision_json=excluded.decision_json, final_action_id=excluded.final_action_id,
                    final_scene_id=excluded.final_scene_id, status=excluded.status,
                    updated_at=excluded.updated_at""",
                (
                    context.request.request_id,
                    context.model_dump_json(),
                    decision.model_dump_json(),
                    final_action_id,
                    final_scene_id,
                    status,
                    now,
                    now,
                ),
            )
            connection.execute(
                """DELETE FROM decision_history WHERE id NOT IN
                    (SELECT id FROM decision_history ORDER BY id DESC LIMIT 1000)"""
            )

    async def list_history(
        self,
        *,
        limit: int = 1000,
        offset: int = 0,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 1000 or offset < 0:
            raise ValueError("History limit must be 1..1000 and offset must be nonnegative.")
        return await asyncio.to_thread(self._list_history_sync, limit, offset, query)

    def _list_history_sync(
        self, limit: int, offset: int, query: str | None
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM decision_history ORDER BY id DESC").fetchall()
        records: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["context"] = json.loads(item.pop("context_json"))
            item["decision"] = json.loads(item.pop("decision_json"))
            if not query or query.casefold() in _json(item).casefold():
                records.append(item)
        return records[offset : offset + limit]

    async def export_history(self, path: Path, *, confirmed: bool = False) -> int:
        if not confirmed:
            raise PermissionError(
                "Export includes plaintext paths and chat excerpts; confirm first."
            )
        records = await self.list_history()
        destination = Path(path)
        protected_paths = {self._key_path.resolve()} | {
            Path(str(self._path) + suffix).resolve() for suffix in ("", "-wal", "-shm", "-journal")
        }
        if destination.resolve() in protected_paths or (
            destination.exists()
            and any(item.exists() and destination.samefile(item) for item in protected_paths)
        ):
            raise ValueError("Export cannot overwrite the database or its key.")
        await asyncio.to_thread(_export_json, destination, records)
        return len(records)

    async def clear_history(self) -> None:
        await asyncio.to_thread(self._clear_tables, ("decision_history", "exact_model_cache"))

    async def get_setting(self, name: str, default: Any = None) -> Any:
        return await asyncio.to_thread(self._get_setting_sync, name, default)

    def _get_setting_sync(self, name: str, default: Any) -> Any:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM settings WHERE name=?", (name,)
            ).fetchone()
        return json.loads(row[0]) if row else default

    async def set_setting(self, name: str, value: Any) -> None:
        if not name.strip():
            raise ValueError("Setting name must not be empty.")
        if _credential_name(name) or _contains_credential(value):
            raise ValueError("Credential settings cannot be stored in application state.")
        await asyncio.to_thread(self._set_setting_sync, name, _json(value))

    def _set_setting_sync(self, name: str, value_json: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO settings(name,value_json) VALUES (?,?)
                ON CONFLICT(name) DO UPDATE SET value_json=excluded.value_json""",
                (name, value_json),
            )
            if name != "clean_shutdown":
                self._invalidate_cache(connection)

    async def get_exact_cache(
        self,
        context: ContextEnvelope,
        *,
        model: str,
        configuration: dict[str, Any] | None = None,
    ) -> OpenDecision | None:
        return await asyncio.to_thread(
            self._get_exact_cache_sync,
            self._cache_key(context, model, configuration),
        )

    def _get_exact_cache_sync(self, key: str) -> OpenDecision | None:
        with self._connect() as connection:
            now = datetime.now(UTC).isoformat()
            connection.execute("DELETE FROM exact_model_cache WHERE expires_at<=?", (now,))
            row = connection.execute(
                "SELECT decision_json FROM exact_model_cache WHERE cache_key=?",
                (key,),
            ).fetchone()
        if not row:
            return None
        return OpenDecision.model_validate_json(row[0]).model_copy(update={"source": "exact_cache"})

    async def set_exact_cache(
        self,
        context: ContextEnvelope,
        decision: OpenDecision,
        *,
        model: str,
        ttl_seconds: int = 86400,
        configuration: dict[str, Any] | None = None,
    ) -> None:
        # CPython 3.12 reads this integer atomically; never wait for a worker lock on the loop.
        # The authoritative epoch check and write remain inside _connect's worker-side RLock.
        epoch = self._cache_epoch
        if decision.source != "jev":
            raise ValueError("Only a Jev decision may populate the exact model cache.")
        if not 1 <= ttl_seconds <= 86400:
            raise ValueError("Cache TTL must be between 1 and 86400 seconds.")
        key = self._cache_key(context, model, configuration)
        expires = (datetime.now(UTC) + timedelta(seconds=ttl_seconds)).isoformat()
        await asyncio.to_thread(
            self._set_exact_cache_sync, key, decision.model_dump_json(), expires, epoch
        )

    def _set_exact_cache_sync(
        self,
        key: str,
        decision_json: str,
        expires: str,
        epoch: int | None = None,
    ) -> None:
        with self._connect() as connection:
            # The check and write share the same lock/transaction as all invalidations.
            if epoch is not None and epoch != self._cache_epoch:
                return
            connection.execute(
                """INSERT INTO exact_model_cache(cache_key,decision_json,expires_at) VALUES (?,?,?)
                ON CONFLICT(cache_key) DO UPDATE SET decision_json=excluded.decision_json,
                    expires_at=excluded.expires_at""",
                (key, decision_json, expires),
            )
            connection.execute(
                """DELETE FROM exact_model_cache WHERE cache_key NOT IN
                (SELECT cache_key FROM exact_model_cache ORDER BY expires_at DESC LIMIT 1000)"""
            )

    def _cache_key(
        self,
        context: ContextEnvelope,
        model: str,
        configuration: dict[str, Any] | None = None,
    ) -> str:
        payload = context.model_dump(mode="json")
        request = payload["request"]
        for name in ("request_id", "captured_at", "source_pid", "foreground_hwnd"):
            request.pop(name, None)
        canonical = _json({"model": model, "configuration": configuration, "context": payload})
        return hmac.new(self._key, canonical.encode("utf-8"), hashlib.sha256).hexdigest()

    def hash_account(self, provider: str, account_id: str) -> str:
        if not provider or not account_id:
            raise ValueError("Provider and account ID are required.")
        payload = _json(["account-v1", provider, account_id]).encode("utf-8")
        return hmac.new(self._key, payload, hashlib.sha256).hexdigest()

    async def remember_provenance(self, target: FileTarget, chat: ChatContext) -> None:
        epoch = self._provenance_epoch, self._provider_epochs.get(chat.provider, 0)
        await asyncio.to_thread(self._remember_provenance_sync, target, chat, epoch)

    def _remember_provenance_sync(
        self,
        target: FileTarget,
        chat: ChatContext,
        epoch: tuple[int, int] | None = None,
    ) -> None:
        if not _anchored_chat(target, chat):
            raise ValueError("Only verified, exactly anchored file chat context may be remembered.")
        fingerprint = fingerprint_file(target)
        if fingerprint is None:
            return
        identity = _json(
            [
                chat.provider,
                chat.account_id_hash,
                chat.conversation_id,
                chat.target_message_id,
                fingerprint.path,
            ]
        )
        record_id = hmac.new(self._key, identity.encode("utf-8"), hashlib.sha256).hexdigest()
        payload = _json(
            {"fingerprint": fingerprint.as_dict(), "chat": chat.model_dump(mode="json")}
        )
        with self._connect() as connection:
            if epoch is not None and epoch != (
                self._provenance_epoch,
                self._provider_epochs.get(chat.provider, 0),
            ):
                return
            connection.execute(
                """INSERT INTO file_provenance
                (record_id,provider,account_id_hash,path,payload_json,last_seen_at,missing_since)
                VALUES (?,?,?,?,?,?,NULL) ON CONFLICT(record_id) DO UPDATE SET
                    payload_json=excluded.payload_json, last_seen_at=excluded.last_seen_at,
                    missing_since=NULL""",
                (
                    record_id,
                    chat.provider,
                    chat.account_id_hash,
                    fingerprint.path,
                    payload,
                    datetime.now(UTC).isoformat(),
                ),
            )

    async def restore_provenance(
        self,
        target: FileTarget,
        enabled_providers: set[str],
    ) -> ChatContext | None:
        if not enabled_providers:
            return None
        return await asyncio.to_thread(
            self._restore_provenance_sync, target, frozenset(enabled_providers)
        )

    def _restore_provenance_sync(
        self,
        target: FileTarget,
        enabled_providers: frozenset[str],
    ) -> ChatContext | None:
        fingerprint = fingerprint_file(target)
        if fingerprint is None:
            return None
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM file_provenance").fetchall()
        matches: dict[tuple[str, str, str | None, str | None], tuple[Any, ChatContext]] = {}
        for row in rows:
            if row["provider"] not in enabled_providers:
                continue
            payload = json.loads(row["payload_json"])
            if not fingerprint.matches(payload["fingerprint"]):
                continue
            chat = ChatContext.model_validate(payload["chat"])
            if (chat.provider, chat.account_id_hash) != (row["provider"], row["account_id_hash"]):
                continue
            origin = FileTarget(path=Path(row["path"]), extension=target.extension)
            if not _anchored_chat(origin, chat):
                continue
            identity = (
                chat.provider,
                chat.account_id_hash,
                chat.conversation_id,
                chat.target_message_id,
            )
            messages = tuple(
                message.model_copy(update={"attachment_path": target.path})
                if message.message_id == chat.target_message_id
                else message
                for message in chat.messages
            )
            warning = "File origin restored from encrypted history; not live chat."
            restored = chat.model_copy(
                update={
                    "messages": messages,
                    "warnings": tuple(dict.fromkeys((*chat.warnings, warning))),
                }
            )
            if _anchored_chat(target, restored):
                matches[identity] = (row, restored)
        if len(matches) != 1:
            return None
        row, restored = next(iter(matches.values()))
        with self._connect() as connection:
            connection.execute(
                "UPDATE file_provenance SET last_seen_at=?,missing_since=NULL,"
                "path=?,payload_json=? "
                "WHERE record_id=?",
                (
                    datetime.now(UTC).isoformat(),
                    fingerprint.path,
                    _json(
                        {
                            "fingerprint": fingerprint.as_dict(),
                            "chat": restored.model_dump(mode="json"),
                        }
                    ),
                    row["record_id"],
                ),
            )
        return restored

    async def clear_provider_data(self, provider: str) -> None:
        await asyncio.to_thread(self._clear_provider_data_sync, provider)

    def _clear_provider_data_sync(self, provider: str) -> None:
        with self._connect() as connection:
            self._provider_epochs[provider] = self._provider_epochs.get(provider, 0) + 1
            connection.execute("DELETE FROM file_provenance WHERE provider=?", (provider,))
            rules = connection.execute("SELECT rule_id,rule_json FROM preference_rules").fetchall()
            for row in rules:
                rule = PreferenceRule.model_validate_json(row["rule_json"])
                if rule.selector.get("provider") == provider:
                    connection.execute(
                        "DELETE FROM preference_rules WHERE rule_id=?", (row["rule_id"],)
                    )
            self._invalidate_cache(connection)

    async def prune_provenance(self, *, now: datetime | None = None) -> int:
        timestamp = now or datetime.now(UTC)
        if timestamp.tzinfo is None:
            raise ValueError("Pruning timestamp must include a timezone.")
        return await asyncio.to_thread(self._prune_provenance_sync, timestamp.astimezone(UTC))

    def _prune_provenance_sync(self, now: datetime) -> int:
        cutoff = (now - timedelta(days=30)).isoformat()
        deleted = 0
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT record_id,path,missing_since FROM file_provenance"
            ).fetchall()
            for row in rows:
                exists = known_path_exists(row["path"])
                if exists is True:
                    connection.execute(
                        "UPDATE file_provenance SET missing_since=NULL,last_seen_at=? "
                        "WHERE record_id=?",
                        (now.isoformat(), row["record_id"]),
                    )
                elif exists is False:
                    if row["missing_since"] and row["missing_since"] <= cutoff:
                        connection.execute(
                            "DELETE FROM file_provenance WHERE record_id=?", (row["record_id"],)
                        )
                        deleted += 1
                    elif not row["missing_since"]:
                        connection.execute(
                            "UPDATE file_provenance SET missing_since=? WHERE record_id=?",
                            (now.isoformat(), row["record_id"]),
                        )
        return deleted

    async def clear_all(self) -> None:
        await asyncio.to_thread(
            self._clear_tables,
            (
                "preference_rules",
                "decision_history",
                "settings",
                "exact_model_cache",
                "file_provenance",
            ),
        )

    def _clear_tables(self, tables: tuple[str, ...]) -> None:
        with self._connect() as connection:
            for table in tables:
                if table == "exact_model_cache":
                    self._invalidate_cache(connection)
                else:
                    if table == "file_provenance":
                        self._provenance_epoch += 1
                    connection.execute(f"DELETE FROM {table}")

    def _invalidate_cache(self, connection: Any) -> None:
        # Called only under _connect's RLock. Even a rolled-back clear retires stale writers.
        self._cache_epoch += 1
        connection.execute("DELETE FROM exact_model_cache")


def _json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _anchored_chat(target: FileTarget, chat: ChatContext) -> bool:
    from jev_open.context.providers.validation import validated_chat
    from jev_open.domain import OpenRequest, OpenVerb

    request = OpenRequest(
        request_id="provenance-check",
        source_pid=1,
        source_executable=Path("provenance"),
        verb=OpenVerb.OPEN,
        target=str(target.path),
        captured_at=datetime.now(UTC),
    )
    return bool(chat.provider and validated_chat(chat, request))


def _credential_name(name: str) -> bool:
    normalized = name.casefold().replace("-", "_")
    return normalized in {
        "key",
        "password",
        "secret",
        "token",
        "credential",
        "credentials",
        "authorization",
    } or any(
        normalized.endswith(suffix)
        for suffix in (
            "_api_key",
            "api_key",
            "_password",
            "_secret",
            "_token",
            "database_key",
            "_credentials",
            "_credential",
        )
    )


def _contains_credential(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            _credential_name(str(key)) or _contains_credential(item) for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_credential(item) for item in value)
    return False


def _export_json(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as destination:
        json.dump(records, destination, ensure_ascii=False, indent=2, allow_nan=False)
        destination.write("\n")


_rule_matches = rule_matches
