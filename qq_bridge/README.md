# QQ context bridge status

The bridge is a tested integration contract, not a verified QQ client adapter.
OneBot 11 payload normalization is implemented in
`src/jev_open/context/providers/qq_onebot.py`; it remains transport-agnostic and
accepts only an externally HMACed account identity. It does not open a WebSocket
or install NapCat by itself.
It is disabled by default and opens no pipe, socket, or database during load.
The previous generic DOM scrape and unauthenticated fixed-name pipe are removed.

## Required integration

Call `configure(adapter, transport)` from a trusted main-process integration.
Both values must be created with `src/integration.js`: use
`createTrustedAdapter({clientVersion, resolveClick})` for a version-specific
main-process adapter and `createAuthenticatedTransport({peerPid, userSid,
sessionNonce, isAuthenticated, getPeerProof, send, close})` for the transport. `configure`
rejects plain objects even when they happen to expose the same method names.
`adapter.resolveClick({contents, click, signal})` must resolve the exact account,
conversation, clicked message, and no more than 10 neighboring messages using a
verified client version. Honor abort signals without leaving background work.
The renderer only supplies a target and message ID; never trust it for identity.
Account IDs must be HMACed using an application-owned local key before forwarding.

The transport's `isAuthenticated()` callback must authenticate its peer using the named-pipe client PID,
current-user SID ACL, and a per-session random nonce. `getPeerProof()` is
called immediately before every send and its PID, numeric Windows SID, and
nonce must exactly match the factory proof. The send deadline uses a monotonic
clock and aborts the delegated operation when it expires. The receiver passes
`authenticated=True` to `QQContextProvider.ingest_event` only after these
checks, along with the verified peer PID. Neither that boolean nor account
identity may be copied from an unauthenticated wire payload. No working
named-pipe transport is claimed in this version.

The Python provider rejects stale, oversized, ambiguous, or unanchored contexts.
It does not perform account-wide qqcli searches or extract database keys.
Stopping either side clears pending state; context absence leaves normal path
and source-program routing available.

The factory records the integration proof, rechecks the callback and proof
before each send, and enforces a bounded monotonic deadline; it does not
implement named-pipe authentication. No transport or current-version adapter
is bundled. Synthetic replay tests use a fixture transport with an explicit
mutable authentication result and never read QQ.

## Runtime fingerprint boundary

`src/jev_open/context/providers/qq_runtime.py` can read a local absolute
`QQ.exe` file's SHA-256 and Windows `VERSIONINFO` fields. It rejects links,
directories, empty files, oversized files, and non-QQ names. This operation
does not open a process, inspect QQ memory, read a database, or read chat.
An adapter must compare the resulting digest or version against an explicit
allow-list before it can claim support for a client build; version metadata is
descriptive and is not an authentication mechanism.

Run isolated JavaScript tests with `node --test qq_bridge/tests/*.test.js`.
