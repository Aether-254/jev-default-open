# Open-With Decision Context

This context defines how the project describes targets, contextual evidence, and the user-confirmed action used to open them.

## Language

**Open Target**:
A user-supplied resource for which the project must select an appropriate opener, such as a local file or a link.
_Avoid_: Item, thing, input

**Open Request**:
An observed request by an application to open or edit an Open Target.
_Avoid_: Event, interception

**Context Envelope**:
The complete set of target, source, conversation, path, and candidate evidence used for one decision.
_Avoid_: Prompt, payload

**Open Profile**:
A named application identity or working environment, such as a personal browser profile or a data-analysis editor profile.
_Avoid_: Account, mode

**Open Action**:
A user-executable choice of application and optional Open Profile for an Open Target.
_Avoid_: Opener, tool, program

**Scene**:
A typed description of the user's situation, such as personal, finance, or data analysis.
_Avoid_: Intent, category

**Open Decision**:
A typed selection, produced using Jev or an explicit preference, that maps a Context Envelope to an Open Action.
_Avoid_: Guess, routing result

**Preference Rule**:
A user-confirmed mapping from an explicit scope, such as a conversation or path label, to an Open Action.
_Avoid_: Learning, model memory

**Chat Context**:
The conversation and nearby messages associated with an Open Target originating from an instant-messaging application.
_Avoid_: Chat dump, transcript archive

**System Fallback**:
The original Windows open behavior used when the project cannot make or safely execute an Open Decision.
_Avoid_: Failure, default guess
