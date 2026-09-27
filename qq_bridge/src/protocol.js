"use strict";

function safeString(value, limit, required = true) {
  return typeof value === "string" && value.length <= limit &&
    (!required || value.length > 0) && !/[\x00-\x08\x0b\x0c\x0e-\x1f\ufffd]/u.test(value);
}

function validateClick(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  if (Object.keys(value).some((key) => !["target", "message_id"].includes(key))) return null;
  if (!safeString(value.target, 32768) || !safeString(value.message_id, 256)) return null;
  return Object.freeze({ target: value.target, message_id: value.message_id });
}

function validateContext(value, click, sourcePid, now = Date.now()) {
  if (!value || !safeString(value.account_id_hash, 64) ||
      !/^[a-f0-9]{64}$/u.test(value.account_id_hash) ||
      !safeString(value.conversation_id, 256) ||
      !Array.isArray(value.messages) || value.messages.length < 1 || value.messages.length > 10) return null;
  if (value.target_message_id !== click.message_id || value.target !== click.target) return null;
  const ids = new Set();
  const messages = [];
  const kinds = new Set(["text", "file", "link", "image", "audio", "video", "card", "system", "unknown"]);
  for (const raw of value.messages) {
    if (!raw || !safeString(raw.message_id, 256) || ids.has(raw.message_id) ||
        !kinds.has(raw.message_type)) return null;
    ids.add(raw.message_id);
    const message = { message_id: raw.message_id, message_type: raw.message_type };
    for (const [key, limit] of [["text", 2000], ["sender", 256], ["url", 32768],
      ["attachment_path", 32768], ["attachment_name", 256]]) {
      if (raw[key] === null || raw[key] === undefined) continue;
      if (!safeString(raw[key], limit, false)) return null;
      message[key] = raw[key];
    }
    if (raw.timestamp !== undefined && raw.timestamp !== null) {
      if (typeof raw.timestamp !== "string" || !Number.isFinite(Date.parse(raw.timestamp)) ||
          !/(Z|[+-]\d\d:\d\d)$/u.test(raw.timestamp)) return null;
      message.timestamp = raw.timestamp;
    }
    messages.push(message);
  }
  const anchor = messages.find((message) => message.message_id === click.message_id);
  if (!anchor || (anchor.url !== click.target && anchor.attachment_path !== click.target)) return null;
  if (value.conversation_title != null && !safeString(value.conversation_title, 256, false)) return null;
  return Object.freeze({
    target: click.target,
    source_pid: sourcePid,
    account_id_hash: value.account_id_hash,
    conversation_id: value.conversation_id,
    conversation_title: value.conversation_title || null,
    target_message_id: click.message_id,
    captured_at: new Date(now).toISOString(),
    messages,
  });
}

module.exports = { validateClick, validateContext };
