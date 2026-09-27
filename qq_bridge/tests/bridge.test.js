"use strict";

const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const test = require("node:test");
const { createBridge } = require("../src/bridge");

const click = Object.freeze({ target: "https://example.invalid/report", message_id: "message-1" });

function context() {
  return {
    target: click.target,
    account_id_hash: "b".repeat(64),
    conversation_id: "trusted-conversation",
    target_message_id: click.message_id,
    messages: [{ message_id: click.message_id, message_type: "link", url: click.target }],
  };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((accept, decline) => { resolve = accept; reject = decline; });
  return { promise, resolve, reject };
}

function windowMock(id = 7) {
  const window = new EventEmitter();
  window.webContents = { id, mainFrame: {}, destroyed: false, isDestroyed() { return this.destroyed; } };
  return window;
}

function harness(t, overrides = {}) {
  const calls = { resolve: [], send: [], close: 0 };
  const window = windowMock();
  let now = Date.parse("2026-09-27T04:00:00Z");
  const adapter = {
    async resolveClick(args) {
      calls.resolve.push(args);
      return overrides.resolve ? overrides.resolve(args) : context();
    },
  };
  const transport = {
    async send(value, options) {
      calls.send.push({ value, options });
      if (overrides.send) await overrides.send(value, options);
    },
    close() { calls.close += 1; },
  };
  const bridge = createBridge({ adapter, transport, sourcePid: 4321, clock: () => now });
  bridge.register(window);
  bridge.start();
  t.after(() => bridge.stop());
  return {
    bridge, window, calls,
    event: { sender: window.webContents, senderFrame: window.webContents.mainFrame },
    advance(ms) { now += ms; },
  };
}

test("bridge validates dependencies before use", () => {
  const valid = { adapter: { resolveClick() {} }, transport: { send() {} }, sourcePid: 1 };
  for (const override of [{ adapter: null }, { transport: {} }, { sourcePid: 0 }, { sourcePid: 1.5 }]) {
    assert.throws(() => createBridge({ ...valid, ...override }), TypeError);
  }
});

test("only a registered sender's top frame reaches the trusted adapter", async (t) => {
  const h = harness(t);
  const stranger = windowMock(8).webContents;
  assert.equal(await h.bridge.handle({ sender: stranger, senderFrame: stranger.mainFrame }, click), false);
  const impostor = windowMock(h.window.webContents.id).webContents;
  assert.equal(await h.bridge.handle({ sender: impostor, senderFrame: impostor.mainFrame }, click), false);
  assert.equal(await h.bridge.handle({ ...h.event, senderFrame: {} }, click), false);
  assert.equal(await h.bridge.handle(null, click), false);
  h.window.webContents.destroyed = true;
  assert.equal(await h.bridge.handle(h.event, click), false);
  assert.equal(h.calls.resolve.length, 0);
  assert.equal(h.calls.send.length, 0);
});

test("renderer cannot supply account, conversation, messages, or source PID", async (t) => {
  const h = harness(t);
  for (const key of ["account_id_hash", "conversation_id", "messages", "source_pid"]) {
    assert.equal(await h.bridge.handle(h.event, { ...click, [key]: "renderer-forged" }), false);
  }
  assert.equal(h.calls.resolve.length, 0);
  assert.equal(await h.bridge.handle(h.event, click), true);
  assert.equal(h.calls.resolve.length, 1);
  assert.deepEqual(h.calls.resolve[0].click, click);
  assert.equal(h.calls.resolve[0].contents, h.window.webContents);
  assert.equal(h.calls.send[0].value.source_pid, 4321);
  assert.equal(h.calls.send[0].value.conversation_id, "trusted-conversation");
  assert.equal(h.calls.send[0].value.account_id_hash, "b".repeat(64));
});

test("click rate limiting is per registered window and resets after 100ms", async (t) => {
  const h = harness(t);
  assert.equal(await h.bridge.handle(h.event, click), true);
  h.advance(99);
  assert.equal(await h.bridge.handle(h.event, click), false);
  const second = windowMock(8);
  h.bridge.register(second);
  assert.equal(await h.bridge.handle({ sender: second.webContents, senderFrame: second.webContents.mainFrame }, click), true);
  h.advance(1);
  assert.equal(await h.bridge.handle(h.event, click), true);
  assert.equal(h.calls.send.length, 3);
});

test("invalid or oversized adapter contexts never reach the transport", async (t) => {
  let nextContext = { ...context(), account_id_hash: "plain-account" };
  const h = harness(t, { resolve: () => nextContext });
  assert.equal(await h.bridge.handle(h.event, click), false);
  h.advance(100);
  nextContext = context();
  nextContext.messages[0].attachment_path = "x".repeat(32768);
  nextContext.messages.push({ message_id: "second", message_type: "file", attachment_path: "y".repeat(32768) });
  assert.equal(await h.bridge.handle(h.event, click), false);
  assert.equal(h.calls.send.length, 0);
});

test("adapter and transport errors return false without escaping", async (t) => {
  const adapterFailure = harness(t, { resolve: () => { throw new Error("synthetic adapter failure"); } });
  assert.equal(await adapterFailure.bridge.handle(adapterFailure.event, click), false);
  assert.equal(adapterFailure.calls.send.length, 0);
  const transportFailure = harness(t, { send: () => { throw new Error("synthetic transport failure"); } });
  assert.equal(await transportFailure.bridge.handle(transportFailure.event, click), false);
});

test("stop aborts an in-flight adapter and ignores its eventual result", async (t) => {
  const request = deferred();
  const h = harness(t, { resolve: () => request.promise });
  const handled = h.bridge.handle(h.event, click);
  assert.equal(h.calls.resolve.length, 1);
  h.bridge.stop();
  assert.equal(h.calls.resolve[0].signal.aborted, true);
  assert.equal(h.calls.close, 1);
  assert.equal(await handled, false);
  request.resolve(context());
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(h.calls.send.length, 0);
  assert.equal(await h.bridge.handle(h.event, click), false);
});

test("stop aborts an in-flight transport and reports no successful delivery", async (t) => {
  const sent = deferred();
  const started = deferred();
  const h = harness(t, { send: (_value, { signal }) => { started.resolve(signal); return sent.promise; } });
  const handled = h.bridge.handle(h.event, click);
  const signal = await started.promise;
  h.bridge.stop();
  assert.equal(signal.aborted, true);
  assert.equal(await handled, false);
  sent.resolve();
});

test("adapter timeout aborts and late resolution cannot send context", { timeout: 2000 }, async (t) => {
  const request = deferred();
  const h = harness(t, { resolve: () => request.promise });
  assert.equal(await h.bridge.handle(h.event, click), false);
  assert.equal(h.calls.resolve[0].signal.aborted, true);
  request.resolve(context());
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(h.calls.send.length, 0);
});

test("the same bounded timeout covers a non-responsive transport", { timeout: 2000 }, async (t) => {
  const sent = deferred();
  const h = harness(t, { send: () => sent.promise });
  assert.equal(await h.bridge.handle(h.event, click), false);
  assert.equal(h.calls.send.length, 1);
  assert.equal(h.calls.send[0].options.signal.aborted, true);
  sent.resolve();
});

test("closed windows cannot submit subsequent clicks", async (t) => {
  const h = harness(t);
  h.window.emit("closed");
  assert.equal(await h.bridge.handle(h.event, click), false);
  assert.equal(h.calls.resolve.length, 0);
});

test("window close aborts its pending context resolution", async (t) => {
  const request = deferred();
  const h = harness(t, { resolve: () => request.promise });
  const handled = h.bridge.handle(h.event, click);
  h.window.emit("closed");
  assert.equal(h.calls.resolve[0].signal.aborted, true);
  assert.equal(await handled, false);
  request.resolve(context());
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(h.calls.send.length, 0);
});

test("window close only aborts the affected window", async (t) => {
  const requests = new Map();
  const h = harness(t, { resolve: ({ contents }) => {
    const request = deferred();
    requests.set(contents.id, request);
    return request.promise;
  } });
  const second = windowMock(8);
  h.bridge.register(second);
  const firstHandled = h.bridge.handle(h.event, click);
  const secondHandled = h.bridge.handle({ sender: second.webContents, senderFrame: second.webContents.mainFrame }, click);
  h.window.emit("closed");
  assert.equal(await firstHandled, false);
  requests.get(second.webContents.id).resolve(context());
  assert.equal(await secondHandled, true);
  requests.get(h.window.webContents.id).resolve(context());
  assert.equal(h.calls.send.length, 1);
});

test("navigation or sender destruction during resolution prevents sending", async (t) => {
  for (const mutate of [(window) => { window.webContents.mainFrame = {}; }, (window) => { window.webContents.destroyed = true; }]) {
    const request = deferred();
    const h = harness(t, { resolve: () => request.promise });
    const handled = h.bridge.handle(h.event, click);
    mutate(h.window);
    request.resolve(context());
    assert.equal(await handled, false);
    assert.equal(h.calls.send.length, 0);
  }
});

test("restart does not implicitly trust old registered windows", async (t) => {
  const h = harness(t);
  h.bridge.stop();
  h.bridge.start();
  assert.equal(await h.bridge.handle(h.event, click), false);
  h.bridge.register(h.window);
  assert.equal(await h.bridge.handle(h.event, click), true);
});

test("old request cleanup cannot discard restarted window cancellation state", async (t) => {
  const requests = [];
  const h = harness(t, { resolve: () => {
    const request = deferred();
    requests.push(request);
    return request.promise;
  } });
  const oldHandled = h.bridge.handle(h.event, click);
  h.bridge.stop();
  h.bridge.start();
  h.bridge.register(h.window);
  const currentHandled = h.bridge.handle(h.event, click);
  assert.equal(await oldHandled, false);
  h.window.emit("closed");
  assert.equal(h.calls.resolve[1].signal.aborted, true);
  assert.equal(await currentHandled, false);
  for (const request of requests) request.resolve(context());
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(h.calls.send.length, 0);
});
