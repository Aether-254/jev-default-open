"use strict";

// These symbols are intentionally private to this module.  A caller must use
// the factories below to make the integration boundary explicit; setting a
// similarly named property on an arbitrary object is not sufficient.
const TRUSTED_ADAPTER = Symbol("jev.qq.trusted-adapter");
const AUTHENTICATED_TRANSPORT = Symbol("jev.qq.authenticated-transport");
const USER_SID = /^S-1-(?:\d+-)+\d+$/u;

function monotonicNowMs() {
  return Number(process.hrtime.bigint()) / 1e6;
}

function validProof(value) {
  return Boolean(value && typeof value === "object" &&
    Number.isInteger(value.peerPid) && value.peerPid > 0 &&
    typeof value.userSid === "string" && value.userSid.length >= 8 &&
    value.userSid.length <= 256 && USER_SID.test(value.userSid) &&
    typeof value.sessionNonce === "string" && value.sessionNonce.length >= 16 &&
    value.sessionNonce.length <= 256 && !/[\x00-\x1f\x7f]/u.test(value.sessionNonce));
}

function validateTransportProof(actual, expected) {
  return validProof(actual) && validProof(expected) &&
    actual.peerPid === expected.peerPid && actual.userSid === expected.userSid &&
    actual.sessionNonce === expected.sessionNonce;
}

function createTrustedAdapter({ clientVersion, resolveClick }) {
  if (typeof clientVersion !== "string" || clientVersion.length < 1 || clientVersion.length > 128 ||
      typeof resolveClick !== "function") {
    throw new TypeError("A client version and resolveClick function are required");
  }
  const adapter = {
    clientVersion,
    resolveClick,
  };
  Object.defineProperty(adapter, TRUSTED_ADAPTER, { value: true });
  return Object.freeze(adapter);
}

function isTrustedAdapter(value) {
  return Boolean(value && value[TRUSTED_ADAPTER] === true &&
    typeof value.resolveClick === "function" && typeof value.clientVersion === "string");
}

function createAuthenticatedTransport({
  peerPid,
  userSid,
  sessionNonce,
  isAuthenticated,
  getPeerProof,
  send,
  close,
  clock = monotonicNowMs,
  deadlineMs = 400,
}) {
  const expected = Object.freeze({ peerPid, userSid, sessionNonce });
  if (!validProof(expected) || typeof isAuthenticated !== "function" ||
      typeof getPeerProof !== "function" || typeof send !== "function" ||
      typeof clock !== "function" || !Number.isFinite(deadlineMs) ||
      deadlineMs <= 0 || deadlineMs > 10_000) {
    throw new TypeError("Authenticated transport proof, verifier, and send function are required");
  }
  let closed = false;
  const peerReady = () => {
    try {
      return isAuthenticated() && validateTransportProof(getPeerProof(), expected);
    } catch {
      return false;
    }
  };
  const transport = {
    // The peer proof is re-read for every request. A factory call alone does
    // not claim that a named-pipe peer has been authenticated.
    async send(value, options = {}) {
      if (closed || !options || typeof options !== "object") {
        throw new Error("QQ transport is closed or options are invalid");
      }
      const start = clock();
      const deadline = options.deadline === undefined ? start + deadlineMs : options.deadline;
      if (!Number.isFinite(deadline) || deadline <= start) {
        throw new Error("QQ transport deadline exceeded");
      }
      if (!peerReady()) {
        throw new Error("QQ transport peer is not authenticated");
      }
      const controller = new AbortController();
      const callerSignal = options.signal;
      const abortCaller = () => controller.abort();
      if (callerSignal) {
        if (callerSignal.aborted) controller.abort();
        else callerSignal.addEventListener("abort", abortCaller, { once: true });
      }
      let timer;
      try {
        if (controller.signal.aborted || clock() >= deadline) {
          controller.abort();
          throw new Error("QQ transport deadline exceeded");
        }
        // Re-check immediately before forwarding to the platform transport so
        // a peer disconnect cannot turn into a late context delivery.
        if (!peerReady()) {
          throw new Error("QQ transport peer is not authenticated");
        }
        const forwarded = { ...options, signal: controller.signal, deadline };
        const operation = Promise.resolve().then(() => send(value, forwarded));
        const timeout = new Promise((_, reject) => {
          timer = setTimeout(() => {
            controller.abort();
            reject(new Error("QQ transport deadline exceeded"));
          }, Math.max(0, deadline - clock()));
        });
        return await Promise.race([operation, timeout]);
      } finally {
        if (timer !== undefined) clearTimeout(timer);
        if (callerSignal) callerSignal.removeEventListener("abort", abortCaller);
      }
    },
    close() {
      closed = true;
      if (typeof close === "function") close();
    },
    authentication: expected,
  };
  Object.defineProperty(transport, AUTHENTICATED_TRANSPORT, { value: true });
  return Object.freeze(transport);
}

function isAuthenticatedTransport(value) {
  return Boolean(value && value[AUTHENTICATED_TRANSPORT] === true &&
    typeof value.send === "function" && value.authentication &&
    Number.isInteger(value.authentication.peerPid) &&
    typeof value.authentication.userSid === "string" &&
    typeof value.authentication.sessionNonce === "string");
}

module.exports = {
  createTrustedAdapter,
  createAuthenticatedTransport,
  isTrustedAdapter,
  isAuthenticatedTransport,
  monotonicNowMs,
  validateTransportProof,
};
