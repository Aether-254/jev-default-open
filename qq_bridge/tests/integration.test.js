"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  createAuthenticatedTransport,
  createTrustedAdapter,
  isAuthenticatedTransport,
  isTrustedAdapter,
} = require("../src/integration");

test("trusted adapter factory records a version-specific integration boundary", async () => {
  const adapter = createTrustedAdapter({
    clientVersion: "fixture-qq-9.9",
    resolveClick: async ({ click }) => ({
      target: click.target,
      account_id_hash: "a".repeat(64),
      conversation_id: "fixture-conversation",
      target_message_id: click.message_id,
      messages: [{ message_id: click.message_id, message_type: "link", url: click.target }],
    }),
  });
  assert.equal(isTrustedAdapter(adapter), true);
  assert.equal(Object.isFrozen(adapter), true);
  assert.equal(isTrustedAdapter({ ...adapter }), false);
  const result = await adapter.resolveClick({ click: { target: "https://example.invalid/x", message_id: "m" } });
  assert.equal(result.conversation_id, "fixture-conversation");
});

test("transport checks authentication again for every synthetic replay", async () => {
  let authenticated = false;
  const sent = [];
  const transport = createAuthenticatedTransport({
    peerPid: 4312,
    userSid: "S-1-5-21-4242",
    sessionNonce: "fixture-session-nonce-1234",
    isAuthenticated: () => authenticated,
    getPeerProof: () => ({
      peerPid: 4312,
      userSid: "S-1-5-21-4242",
      sessionNonce: "fixture-session-nonce-1234",
    }),
    send: async (value) => { sent.push(value); },
  });
  assert.equal(isAuthenticatedTransport(transport), true);
  await assert.rejects(transport.send({ target: "blocked" }, { signal: new AbortController().signal }),
    /not authenticated/);
  authenticated = true;
  await transport.send({ target: "synthetic" }, { signal: new AbortController().signal });
  authenticated = false;
  await assert.rejects(transport.send({ target: "late" }, { signal: new AbortController().signal }),
    /not authenticated/);
  assert.deepEqual(sent, [{ target: "synthetic" }]);
});

test("transport rejects a changed peer proof before forwarding", async () => {
  let proof = {
    peerPid: 4312,
    userSid: "S-1-5-21-4242",
    sessionNonce: "fixture-session-nonce-1234",
  };
  let sends = 0;
  const transport = createAuthenticatedTransport({
    ...proof,
    isAuthenticated: () => true,
    getPeerProof: () => proof,
    send: async () => { sends += 1; },
  });
  await transport.send({ target: "first" });
  proof = { ...proof, peerPid: 4313 };
  await assert.rejects(transport.send({ target: "spoofed" }), /not authenticated/);
  assert.equal(sends, 1);
});

test("transport uses a monotonic absolute deadline and aborts a late send", async () => {
  let now = 1000;
  let aborted = false;
  const proof = {
    peerPid: 4312,
    userSid: "S-1-5-21-4242",
    sessionNonce: "fixture-session-nonce-1234",
  };
  const transport = createAuthenticatedTransport({
    ...proof,
    isAuthenticated: () => true,
    getPeerProof: () => proof,
    clock: () => now,
    send: async (_value, options) => {
      options.signal.addEventListener("abort", () => { aborted = true; }, { once: true });
      await new Promise(() => {});
    },
    deadlineMs: 5,
  });
  const pending = transport.send({ target: "slow" });
  now = 1005;
  await assert.rejects(pending, /deadline exceeded/);
  assert.equal(aborted, true);
});

test("factories reject incomplete proof or adapter metadata", () => {
  assert.throws(() => createTrustedAdapter({ clientVersion: "", resolveClick() {} }), TypeError);
  assert.throws(() => createAuthenticatedTransport({
    peerPid: 1,
    userSid: "S-1-5-21-4242",
    sessionNonce: "short",
    isAuthenticated: () => true,
    getPeerProof: () => ({}),
    send() {},
  }), TypeError);
  assert.throws(() => createAuthenticatedTransport({
    peerPid: 1,
    userSid: "S-1-5-21-4242",
    sessionNonce: "fixture-session-nonce-1234",
    isAuthenticated: () => true,
    send() {},
  }), TypeError);
});
