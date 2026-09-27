# Native transport and experimental hook

The default build is a **non-intercepting** Windows x64 transport/test build.
It produces `native_host.exe`, `native_claim.dll`, and an explicitly disabled
`open_hook.dll`. `start_hook` reports `ERROR_NOT_SUPPORTED`; it never pretends
that interception became active. `native_host --self-test` reads local artifact
presence and reports protocol/capability JSON without loading or installing hooks.

## Build and tests

From the repository root, use `scripts/build-native.ps1` with an installed MSVC
x64 toolchain, or pass `-ZigPath <existing-zig.exe>` to build the default transport
with an already installed Zig and Ninja. The script installs no global toolchain.
Dependencies are pinned; the nlohmann 3.11.3 single header is SHA256 verified.
Output is `native/build/Release` unless `-BuildDirectory` is supplied.

CTest runs a shared-mapping ownership race test and the read-only host self-test.
The Python protocol tests run additional compiled host/claim checks when
`JEV_NATIVE_TEST_HOST` points to the built host. These tests pass `--test-mode`,
which cannot install a hook even in an experimental build. They never launch a
real document or read a real chat.

The separately gated Detours source requires MSVC and
`-DJEV_ENABLE_EXPERIMENTAL_HOOK=ON` (PowerShell switch `-EnableExperimentalHook`).
This switch is an explicit experimental opt-in, not a claim of validation. The
Detours branch has not been built or exercised against live applications in the
local Zig verification. Desktop-wide thread transaction safety and concurrent
DLL unload remain unverified. Do not enable this branch on a working desktop
until it has been exercised in an isolated Windows test environment.

The build gate requires the Microsoft x64 compiler (`CMAKE_CXX_COMPILER_ID` is
`MSVC`, with an 8-byte target). A non-MSVC configure fails before fetching
Detours. On the 2026-09-27 local audit, the available Zig/CMake/Ninja toolchain
was rejected with that diagnostic; no experimental DLL was produced and no
global hook was installed. `scripts/build-native.ps1 -EnableExperimentalHook`
also checks for `cl.exe` and `link.exe` before invoking CMake and requires an
x64 Visual Studio developer environment.

## Protocol v2

The Python broker starts the host using private inherited stdin/stdout handles
and a random handshake nonce. JSON Lines control commands have correlation IDs,
and `start` reports active only after a matching positive status response. Host
exit, invalid framing, and transport failure clear Python's active status.

The experimental DLL sends an offer using **WM_COPYDATA**, not a named pipe.
The offer includes a unique shared-memory mapping name. The mapping contains a
versioned header, the source PID, a monotonic deadline, the exact source bytes,
and one atomic state: pending, accepted, or cancelled.

The host checks source-process/path/session consistency, the mapping, payload,
deadline, and queue bounds. A successful WM_COPYDATA return acknowledges only
transport; it does not transfer ownership. The broker validates the envelope,
then `native_claim.dll` performs the sole pending-to-accepted compare/exchange.
The source races pending-to-cancelled when its 50 ms budget expires. Thus a late
or lost transport response cannot leave both source and broker launching the
same operation. The source frame cap is 128 KiB; the JSON transport envelope has
a larger independent bound for the escaped source-byte copy.

Stopped hosts reject new offers. Missing hosts and expired/unclaimed requests
use the source's original ShellExecute. Accepted requests belong to the broker;
stopping interception does not silently cancel their pending confirmation UI.
Python fallback calls ShellExecute from the broker process, preserves verb and
arguments, and raises on a failure return instead of silently dropping an open.

## Explicit limitations

- WM_COPYDATA, a discoverable window, and same-user named mappings are **not an
  authenticated security boundary**. PID/path/session consistency is not sender
  authentication. Another same-user process can spoof a source or deny service.
  The handshake nonce authenticates the child stdio exchange only.
- The broker must not be automatically elevated. Routing an untrusted IPC target
  through an elevated launcher would cross a privilege boundary.
- Atomic acceptance prevents the source/broker timeout double-launch race, but
  does not provide durable exactly-once execution. A broker crash after acceptance
  can lose that in-flight operation; there is no automatic replay journal.
- Default builds cannot globally intercept. Hook capability is reported false.
- The experimental path conservatively forwards nonempty parameters, unsupported
  ShellExecuteEx flags, relative/UNC/device paths, non-HTTP(S) schemes, and dangerous
  extensions. It performs no target filesystem I/O on an intercepted source thread.
- The helper's atomic tests do not prove real Detours attach/detach safety, UI
  behavior, IM correlation, source application return-value compatibility, or
  complete Windows Shell coverage.
