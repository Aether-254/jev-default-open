"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { validateClick, validateContext } = require("../src/protocol");

const click = Object.freeze({
  target: "https://example.invalid/report?id=17",
  message_id: "message-17",
});
const capturedAt = Date.parse("2026-09-27T04:00:00Z");

function context() {
  return {
    target: click.target,
    account_id_hash: "a".repeat(64),
    conversation_id: "conversation-17",
    conversation_title: "Finance review",
    target_message_id: click.message_id,
    messages: [{
      message_id: click.message_id,
      message_type: "link",
      sender: "Synthetic reviewer",
      text: "Review the quarterly report.",
      url: click.target,
      timestamp: "2026-09-27T11:59:00+08:00",
    }],
  };
}

test("click accepts only the target and message ID and freezes its own copy", () => {
  const payload = { ...click };
  const result = validateClick(payload);
  assert.deepEqual(result, click);
  assert.notEqual(result, payload);
  assert.equal(Object.isFrozen(result), true);
  payload.target = "https://example.invalid/changed";
  assert.equal(result.target, click.target);
});

test("click rejects renderer-supplied context, identity, and prototype keys", () => {
  for (const key of ["account_id_hash", "conversation_id", "messages", "source_pid", "__proto__"]) {
    const payload = { ...click, [key]: "forged" };
    assert.equal(validateClick(payload), null, key);
  }
});

test("click rejects malformed, missing, oversized, or control-character fields", () => {
  const invalid = [
    null, undefined, [], "click", {},
    { target: click.target },
    { message_id: click.message_id },
    { ...click, target: "" },
    { ...click, target: 123 },
    { ...click, target: "x".repeat(32769) },
    { ...click, target: "bad\u0000path" },
    { ...click, target: "bad\ufffdpath" },
    { ...click, message_id: "" },
    { ...click, message_id: "x".repeat(257) },
  ];
  for (const payload of invalid) assert.equal(validateClick(payload), null);
  assert.notEqual(validateClick({ target: "x".repeat(32768), message_id: "m".repeat(256) }), null);
});

test("context derives sender PID and capture time from trusted inputs", () => {
  const raw = { ...context(), source_pid: 9999, captured_at: "2000-01-01T00:00:00Z", secret: "drop" };
  raw.messages[0].private_metadata = "drop";
  const result = validateContext(raw, click, 4321, capturedAt);
  assert.equal(result.source_pid, 4321);
  assert.equal(result.captured_at, "2026-09-27T04:00:00.000Z");
  assert.equal(result.account_id_hash, "a".repeat(64));
  assert.equal(Object.isFrozen(result), true);
  assert.equal("secret" in result, false);
  assert.equal("private_metadata" in result.messages[0], false);
  assert.notEqual(result.messages[0], raw.messages[0]);
});

test("context requires exactly 64 lowercase hex account characters and a conversation", () => {
  for (const account of [undefined, null, "", "account-name", "a".repeat(63), "a".repeat(65), "g".repeat(64), "A".repeat(64)]) {
    assert.equal(validateContext({ ...context(), account_id_hash: account }, click, 4321), null);
  }
  for (const conversation of [undefined, null, "", "x".repeat(257), "bad\u0000id"]) {
    assert.equal(validateContext({ ...context(), conversation_id: conversation }, click, 4321), null);
  }
});

test("context permits one to ten unique messages and rejects duplicate IDs", () => {
  const raw = context();
  for (let index = 0; index < 9; index += 1) {
    raw.messages.push({ message_id: `neighbor-${index}`, message_type: "text", text: "Synthetic context" });
  }
  assert.equal(validateContext(raw, click, 4321).messages.length, 10);
  assert.equal(validateContext({ ...raw, messages: [] }, click, 4321), null);
  assert.equal(validateContext({ ...raw, messages: [...raw.messages, { message_id: "eleventh", message_type: "text" }] }, click, 4321), null);
  assert.equal(validateContext({ ...raw, messages: [raw.messages[0], raw.messages[0]] }, click, 4321), null);
  assert.equal(validateContext({ ...raw, messages: [null] }, click, 4321), null);
});

test("context requires exact click, target message, and message URL or attachment anchor", () => {
  assert.equal(validateContext({ ...context(), target: "https://example.invalid/other" }, click, 4321), null);
  assert.equal(validateContext({ ...context(), target_message_id: "other-message" }, click, 4321), null);
  const missingAnchor = context();
  missingAnchor.messages[0].message_id = "other-message";
  assert.equal(validateContext(missingAnchor, click, 4321), null);
  const wrongTarget = context();
  wrongTarget.messages[0].url = "https://example.invalid/other";
  wrongTarget.messages[0].text = click.target;
  assert.equal(validateContext(wrongTarget, click, 4321), null);
  const fileClick = { target: "D:\\Fixtures\\report.csv", message_id: click.message_id };
  const fileContext = context();
  fileContext.target = fileClick.target;
  fileContext.messages[0] = { message_id: click.message_id, message_type: "file", attachment_path: fileClick.target };
  assert.equal(validateContext(fileContext, fileClick, 4321).target, fileClick.target);
});

test("context rejects invalid message kinds, metadata limits, and naive timestamps", () => {
  const invalidFields = [
    ["message_type", "executable"],
    ["message_id", ""],
    ["text", "x".repeat(2001)],
    ["sender", "x".repeat(257)],
    ["attachment_name", "x".repeat(257)],
    ["attachment_path", "x".repeat(32769)],
    ["url", "x".repeat(32769)],
    ["text", "bad\u0000text"],
    ["timestamp", "2026-09-27T12:00:00"],
    ["timestamp", "not-a-date"],
    ["timestamp", 1234],
  ];
  for (const [key, value] of invalidFields) {
    const raw = context();
    raw.messages[0][key] = value;
    assert.equal(validateContext(raw, click, 4321), null, key);
  }
  assert.equal(validateContext({ ...context(), conversation_title: "x".repeat(257) }, click, 4321), null);
  const noOptionalMetadata = context();
  delete noOptionalMetadata.conversation_title;
  noOptionalMetadata.messages[0].timestamp = null;
  noOptionalMetadata.messages[0].sender = null;
  assert.equal(validateContext(noOptionalMetadata, click, 4321).conversation_title, null);
});
