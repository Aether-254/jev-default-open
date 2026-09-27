# Architecture

## System shape

Jev Default Open is a brokered Windows desktop system. A tiny native hook captures
eligible ShellExecute calls, acknowledges ownership quickly, and hands work to a
Python broker. The broker assembles context, discovers compatible Open Actions,
asks Jev typed questions, and presents a user confirmation UI.

## Deep modules

### Interception Module

Interface: start, stop, emergency stop, and validated Open Request delivery.

The implementation hides SetWindowsHookEx, Detours, native-host supervision,
named-pipe framing, ACK deadlines, recursion prevention, and failure-open logic.

### Context Module

Interface: assemble one Context Envelope from one Open Request.

The implementation hides target parsing, dangerous-target filtering, IM provider
selection, chat correlation, provenance recovery, path labels, account scoping,
and provider deadlines.

### Open Action Module

Interface: discover compatible Open Actions and launch one selected action.

The implementation hides Windows association APIs, application/Profile discovery,
candidate normalization, the 255-option cap, invocation validation, and safe argv
construction.

### Decision Module

Interface: decide Scene and Open Action for a Context Envelope before a deadline.

The implementation hides Jev request construction, independent typed questions,
thresholds, exact-context caching, bounded retries, and single/no-candidate rules.

### State Module

Interface: resolve preferences, save preferences, record decisions, and clear state.

The implementation hides SQLCipher, DPAPI, schema migrations, history trimming,
provenance retention, account HMACs, and sensitive export handling.

### User Interface Module

Interface: run the UI, enqueue a decision, and show recovery state.

The implementation hides onboarding, tray behavior, 500ms batching, request
coalescing, Scene/action corrections, preference scopes, and failure recovery.

## End-to-end data flow

1. Hook DLL receives an eligible ShellExecute call.
2. Broker validates and ACKs within 50ms; otherwise the original call continues.
3. Context Module assembles target, IM context, provenance, and path labels.
4. Open Action Module discovers compatible application/Profile actions.
5. State Module resolves the most specific explicit preference.
6. Decision Module asks Jev for Scene and Open Action when no preference applies.
7. UI batches and presents the result for explicit confirmation.
8. Open Action Module launches the confirmed action.
9. State Module records the encrypted audit result and any explicit preference.

## TODO implementation order

1. Make domain types and IPC protocol fixtures executable in tests.
2. Implement a fake Interception Module and fake providers for full broker tests.
3. Implement Jev request/response parsing with deterministic fixtures.
4. Implement profile/action discovery without launching anything.
5. Implement SQLCipher/DPAPI state and migrations.
6. Implement PySide6 onboarding and confirmation UI against fakes.
7. Implement native host and failure-open hook behavior.
8. Implement QQ and WeChat providers.
9. Run native and real-client integration tests on a disposable test account.
