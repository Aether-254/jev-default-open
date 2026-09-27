from __future__ import annotations

import ntpath
from datetime import UTC, datetime
from pathlib import PureWindowsPath
from uuid import uuid4

from jev_open.domain import ContextEnvelope, PreferenceRule


def canonical_windows_path(value: str) -> str:
    return ntpath.normcase(ntpath.normpath(value.replace("/", "\\")))


def _absolute_path(value: str) -> bool:
    drive, tail = ntpath.splitdrive(value)
    return bool(drive and tail.startswith(("\\", "/")))


def _path_contains(parent: str, child: str) -> bool:
    if not parent or not _absolute_path(parent) or not _absolute_path(child):
        return False
    parent, child = canonical_windows_path(parent), canonical_windows_path(child)
    try:
        return ntpath.commonpath((parent, child)) == parent
    except ValueError:
        return False


def _target_class_matches(selector: dict[str, str], context: ContextEnvelope) -> bool:
    target = context.target
    if selector.get("kind") and selector["kind"] != target.kind:
        return False
    # Legacy exact-target rules represented open, never edit. New rules are explicit.
    if selector.get("verb", "open") != context.request.verb:
        return False
    if "extension" in selector:
        value = selector["extension"]
        if not value or target.kind != "file" or value.casefold() != target.extension.casefold():
            return False
    if "mime" in selector:
        value = selector["mime"]
        if (
            not value
            or target.kind != "file"
            or value.casefold() != (target.mime_type or "").casefold()
        ):
            return False
    if "host" in selector:
        value = selector["host"]
        if not value or target.kind != "url" or value.casefold() != (target.host or "").casefold():
            return False
    if "scheme" in selector:
        if target.kind != "url" or selector["scheme"].casefold() != target.scheme.casefold():
            return False
    if "provider" in selector or "account_id_hash" in selector:
        chat = context.chat_context
        if not chat or not selector.get("provider") or not selector.get("account_id_hash"):
            return False
        if (selector["provider"], selector["account_id_hash"]) != (
            chat.provider,
            chat.account_id_hash,
        ):
            return False
    return True


def rule_matches(rule: PreferenceRule, context: ContextEnvelope) -> bool:
    selector, target = rule.selector, context.target
    if not selector or not _target_class_matches(selector, context):
        return False
    if rule.scope != "exact_target" and (
        selector.get("kind") != target.kind or selector.get("verb") != context.request.verb
    ):
        return False
    if context.open_actions and rule.action_id not in {a.action_id for a in context.open_actions}:
        return False
    if rule.scope == "exact_target":
        expected = selector.get("target", "")
        if not expected:
            return False
        if target.kind == "file":
            return (
                _absolute_path(expected)
                and _absolute_path(str(target.path))
                and canonical_windows_path(expected) == canonical_windows_path(str(target.path))
            )
        return expected == str(target.url)
    if rule.scope == "conversation":
        chat = context.chat_context
        return bool(
            chat
            and selector.get("provider")
            and selector.get("account_id_hash")
            and selector.get("conversation_id")
            and selector["conversation_id"] == chat.conversation_id
        )
    if rule.scope == "path_prefix":
        return target.kind == "file" and _path_contains(selector.get("path", ""), str(target.path))
    if rule.scope == "path_label":
        return bool(selector.get("label") and selector["label"] in context.path_labels)
    if rule.scope == "provider":
        return bool(
            context.chat_context and selector.get("provider") and selector.get("account_id_hash")
        )
    if rule.scope == "url_domain":
        return target.kind == "url" and bool(selector.get("host"))
    if rule.scope == "extension_or_mime":
        return target.kind == "file" and bool(selector.get("extension") or selector.get("mime"))
    return (
        rule.scope == "global"
        and selector.get("kind") == target.kind
        and bool(selector.get("verb"))
    )


def rule_priority(rule: PreferenceRule) -> tuple[int, int, datetime]:
    priority = {
        "exact_target": 8,
        "conversation": 7,
        "path_prefix": 6,
        "path_label": 5,
        "provider": 4,
        "url_domain": 3,
        "extension_or_mime": 3,
        "global": 1,
    }
    specificity = len(PureWindowsPath(rule.selector.get("path", "")).parts)
    if rule.scope != "path_prefix":
        specificity = 0
    timestamp = rule.updated_at
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return priority[rule.scope], specificity, timestamp


def create_preference(
    context: ContextEnvelope,
    action_id: str,
    scope: str,
    *,
    path: str | None = None,
    label: str | None = None,
) -> PreferenceRule:
    """Create a scoped rule from the context the user explicitly confirmed."""
    if action_id not in {action.action_id for action in context.open_actions}:
        raise ValueError("The selected action is not a current candidate.")
    target, chat = context.target, context.chat_context
    selector = {"kind": target.kind, "verb": str(context.request.verb)}
    if target.kind == "file" and target.extension:
        selector["extension"] = target.extension.casefold()
    elif target.kind == "url":
        selector["scheme"] = target.scheme.casefold()
        if target.host:
            selector["host"] = target.host.casefold()
    if scope in {"conversation", "provider"}:
        if not chat or not chat.provider or not chat.account_id_hash:
            raise ValueError("An identified IM provider and account are required for this scope.")
        selector.update(provider=chat.provider, account_id_hash=chat.account_id_hash)
    if scope == "conversation":
        if not chat or not chat.conversation_id:
            raise ValueError("A verified conversation is required for this scope.")
        selector["conversation_id"] = chat.conversation_id
    elif scope == "exact_target":
        selector["target"] = str(target.path if target.kind == "file" else target.url)
    elif scope == "path_prefix":
        if target.kind != "file":
            raise ValueError("A directory preference requires a file target.")
        selector["path"] = path or str(PureWindowsPath(target.path).parent)
    elif scope == "path_label":
        label = label or (context.path_labels[0] if context.path_labels else None)
        if not label or label not in context.path_labels:
            raise ValueError("A matching directory label is required.")
        selector["label"] = label
    elif scope == "extension_or_mime":
        if target.kind != "file" or not (target.extension or target.mime_type):
            raise ValueError("The file must have an extension or MIME type.")
        if target.mime_type:
            selector["mime"] = target.mime_type
    elif scope == "url_domain":
        if target.kind != "url" or not target.host:
            raise ValueError("A URL with a domain is required.")
    elif scope == "global":
        selector = {"kind": target.kind, "verb": str(context.request.verb)}
    now = datetime.now(UTC)
    rule = PreferenceRule(
        rule_id=str(uuid4()),
        scope=scope,
        selector=selector,
        action_id=action_id,
        created_at=now,
        updated_at=now,
    )
    if not rule_matches(rule, context):
        raise ValueError("The preference does not describe the confirmed target.")
    return rule
