# Continuing a chat with a different provider

An ordinary Kira chat can continue with Codex, OpenAI direct · ChatGPT, or Ollama
without creating another visible conversation. The chat ID, project, transcript,
draft, title and goal remain the same. Select the target provider and its model;
Kira waits for the service to acknowledge the change before enabling a new turn.
Changing a provider does not submit a prompt or start a model.

## Conversation and native state

Kira's conversation is the durable local record. A provider generation identifies
one continuous configuration of the selected provider and account. On a real
provider or direct-account change, Kira retires the old generation with its exact
settings and native reference, then creates a new generation with no native
reference. Previous events and run identities remain unchanged.

Returning to Codex starts a fresh internal native thread with portable history
from the same Kira conversation. The previously used Codex thread remains
recorded, but cannot be resumed as though the intervening direct or local turns
had happened inside it. Subsequent Codex turns resume the new generation normally.
Changing only a model or reasoning effort within the same provider/account keeps
the current generation and native reference.

A native Codex branch at a specific historical request is available only when
that request belongs to the active Codex generation and has a verified native
turn boundary. Requests from a different provider or an older Codex generation
cannot identify a turn in the current native thread. Branching at the current
chat end remains available; portable branches with direct or Ollama can copy
historical dialogue across provider boundaries.

Codex and Ollama transitions use bounded portable dialogue and tool-outcome
excerpts. OpenAI direct instead pages the complete stored archive into its
[durable working context](DIRECT_CONTEXT.md), compacting according to model
capacity. Project instructions, pinned chat notes and the current goal are supplied
separately. Project reference memory is retrieved through the scoped
[knowledge gateway](KIRA_IDENTITY.md). Historical content remains data: previous tool calls are never
executed as part of a transition. Provider-private reasoning and encrypted native
state are not transferred. The full local transcript remains available even when
an older part exceeds the model context budget. Switching providers does not
promise identical native memory or capabilities.

## Configuration and accounts

The first use of a provider carries only shared tool and resource-limit settings.
Provider-specific fields, such as Codex service tier or Ollama context size, remain
with their original configuration. Returning to a provider restores its saved
options while applying the explicitly selected target model and reasoning effort.
An empty effort selects the provider default. Current chat title and portable
handoff text are preserved across transitions. Unsupported saved provider options
are rejected rather than silently removed.

The first direct transition binds the selected or active local ChatGPT account.
Subsequent transitions back to direct retain that chat's previous binding, even if
the active account for new chats has changed. A different account requires an
explicit account selection. Account validation checks sign-in and plan permission
before any transition is committed. The operation never changes account
registrations, credentials, billing, project permissions or global configuration.
See [OpenAI direct](OPENAI_DIRECT.md) for the account boundary.

The target model string is preserved exactly; selecting it does not guarantee
provider acceptance or entitlement. Provider failures never trigger an automatic
model, account or backend fallback.

## Admission and recovery

Only owned, non-archived ordinary chats can change provider. Managed team-member
chats cannot be changed independently. Active work, overlapping project
reservations, native control operations, unfinished cleanup, active project
teams/delegations, and unfinished project queues block the change. A paused queue
with pending items still blocks it.

Account validation can yield. The service repeats admission checks afterwards and
verifies that the source chat configuration and generation are unchanged. It then
commits the retired generation, new configuration and idempotency receipt in one
SQLite transaction, with no await between the final checks and commit. A failure
leaves the original chat selected and usable.

Each request uses a stable `request_id`. Retrying the same input returns its
original receipt without another transition. Reusing the identifier with
different input fails. A retry after a later successful switch returns the
original receipt alongside the **current** chat row; clients must adopt the
returned current session, rather than replaying the provider in an old receipt.

## IPC

`session.providers {project_id, session_id}` is a read-only operation. It performs
no model discovery or authentication and returns:

- `session`: the current enriched Kira session;
- `current_generation`: the active generation number;
- `providers`: one row for each backend, containing `backend`, `model`,
  `reasoning_effort`, `account_id`, `used` and `current`;
- `transitions`: up to 100 recent generations with their provider/model/account,
  effort, generation number and creation time;
- `transitions_truncated`: whether older generations were omitted from that view.

`session.switch {project_id, session_id, request_id, backend, model,
reasoning_effort, account_id?}` selects the configuration. `reasoning_effort` is
required and may be empty. `account_id` is accepted only for OpenAI direct. The
response contains `session`, `current_generation`, `replayed` and an immutable
`switch` receipt. The receipt contains `request_id`, `generation`, `backend`,
`model`, `account_id`, `changed` and `transitioned`. `changed` includes ordinary
configuration edits; `transitioned` specifically indicates a new provider
generation.

The generation ledger stores native references privately in the application
database. Provider choices and transition summaries do not expose them. Per-run
bindings preserve historical attribution independently of the current session
configuration. These records belong to the user profile and must not be copied
into source fixtures or publication artifacts.

Synthetic admission, rollback, concurrency, account-binding and idempotency checks
are in `tests/test_provider_routing.py`. Current qualification and live-provider
limits are recorded in [Status](STATUS.md).

## Personalized identities

Desktop 0.15.0 can select a different Kira profile in an idle ordinary chat.
This explicit change retires the native generation while retaining exactly the
configured provider/model/account/settings and the ordinary conversation archive.
The new profile receives current task context through the established portable
paths. Its user-level capsule is versioned independently; core edits apply at
the next admitted turn without modifying provider settings. This is not erasure
of already delivered information. [Anima di Kira](KIRA_SOULS.md) defines ownership,
first-message selection, source scope and compaction boundaries.
