"use strict";

exports.onLoad = function onLoad() {
  // TODO: Connect to the session-scoped local named pipe.
  // TODO: Authenticate with the broker-provided one-time nonce.
  // TODO: Forward only context events; never send or mutate QQ messages.
};

exports.onLogin = function onLogin(uid) {
  // TODO: Hash/account-scope the login identity inside the broker.
  void uid;
};

exports.onBrowserWindowCreated = function onBrowserWindowCreated(window) {
  // TODO: Track the active QQ conversation window without retaining DOM objects.
  void window;
};
