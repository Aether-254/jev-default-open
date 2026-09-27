"use strict";

const { ipcMain } = require("electron");
const { createBridge } = require("./bridge");
const { isAuthenticatedTransport, isTrustedAdapter } = require("./integration");

const CHANNEL = "jev-default-open:target";
const NOT_CONNECTED = "not_connected";
let bridge = null;
let listener = null;

function unavailable() {
  return {
    status: NOT_CONNECTED,
    reason: "requires_verified_adapter_and_authenticated_transport",
  };
}

// Called only by a trusted, version-specific main-process integration.
// No verified QQ adapter or authenticated pipe transport is bundled yet.
exports.configure = function configure(adapter, transport) {
  if (!isTrustedAdapter(adapter) || !isAuthenticatedTransport(transport)) {
    exports.onUnload();
    return unavailable();
  }
  exports.onUnload();
  bridge = createBridge({ adapter, transport, sourcePid: process.pid });
  listener = (event, payload) => { void bridge?.handle(event, payload); };
  ipcMain.on(CHANNEL, listener);
  bridge.start();
  return { status: "configured", client_version: adapter.clientVersion };
};

exports.onLoad = function onLoad() {
  return unavailable();
};

exports.onBrowserWindowCreated = function onBrowserWindowCreated(window) {
  bridge?.register(window);
};

exports.onUnload = function onUnload() {
  if (listener) ipcMain.removeListener(CHANNEL, listener);
  listener = null;
  bridge?.stop();
  bridge = null;
};
