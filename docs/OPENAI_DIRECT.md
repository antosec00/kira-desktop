# OpenAI direct with a ChatGPT plan

Kira Desktop provides the `openai_chatgpt` backend. It uses the official Sign in with
ChatGPT public-client flow and the public Responses API. Kira owns the tool loop,
permissions, project context, questions and Team scheduling. Codex App Server is
not involved in this backend. An ordinary chat can change providers while retaining its visible history; each provider keeps separate execution references. See [provider continuity](PROVIDER_CONTINUITY.md).

## Account and billing boundary

Choose **OpenAI direct · ChatGPT**, then **Continue with ChatGPT** in Models or
Settings. Authorize Kira and enable **Use your ChatGPT plan** in the official
browser flow. A valid identity without that permission cannot run inference.
Eligible plans and workspace policies are controlled by OpenAI. The account's
model catalog does not prove successful inference or entitlement to an access
program. Kira does not assume Daybreak availability from a different client.

This route shares the account's existing allowance and app-specific limits.
Manage these at [ChatGPT Settings → Usage](https://chatgpt.com/settings/usage).
A dismissible first-use notice is remembered in private GUI preferences. The
composer opens Kira’s usage dialog, which exposes recorded consumption and the
official plan-management link; a measured quota error makes that action prominent.
The direct integration does not expose app quota/credit readings. The separate
Codex account view must not be attributed to the direct registration. See
[Account usage](ACCOUNT_USAGE.md).
Kira neither configures paid API keys nor silently changes billing, model or
provider when authentication, quota or policy rejects a request.

Each registration has a stable local account ID. New chats bind that ID in
`settings.openai_account_id`; subsequent model changes and Team continuations
preserve it. Switching the active account affects new chats and discovery.
Existing chats continue to address their bound account. Account changes are
blocked while direct work, queues or reservations are outstanding. New direct
work is blocked while sign-in is pending. Codex work has its own identity.

## Authentication architecture

`backends/openai_auth.py` manages a stable host ID, multiple registrations and
rotating tokens. `service/direct_accounts.py` owns its lifecycle and admission
boundary. Credential records live in the private `openai-chatgpt` directory below
the resolved Kira configuration directory. They are excluded from SQLite,
events, project context, exports and diagnostics. Current and preceding credential
values are also registered privately with the service redactor before any backend
can run, including after restart. Invalid existing private credential storage
fails service startup closed rather than accepting unprotected execution. This
protects literal
accidental tool echoes without retaining an unbounded token history. This is not
universal detection of transformed or unrelated secrets. Never publish that directory.

The public client uses PKCE S256, random OAuth state and OIDC nonce, an ephemeral
`127.0.0.1` listener and the stable `/auth/callback` path. Initial dynamic
registration retains the issued client ID; returning authorization reuses it.
ID tokens are verified against the issuer's JWKS and selected registration.
The GUI receives a temporary local start URL rather than an authorization URL
containing an ID-token hint. Only the explicit sign-in action opens the browser.

Private atomic writes and a process lock protect stored state. Refresh is
serialized and replaces rotating credentials together. Logout attempts remote
revocation, clears local tokens and retains the registration/host mapping. A
failed remote revocation is reported. No existing Codex credentials or global
configuration are read or modified.

## Inference and tools

Discovery uses `GET https://api.openai.com/v1/models` with the selected account,
preserving visible model order and sending the selected slug unchanged.
GUI catalogs are cached by account, including when an existing chat is bound to
an account different from the active account for new chats and Teams. Explicit
unsupported provider settings are rejected; they are never silently ignored.
Inference uses `POST https://api.openai.com/v1/responses`, `store:false` and
`stream:true`. HTTP history is supplied explicitly; there is no server-stored
thread or `previous_response_id`. No unsupported system-role or sampling fields
are sent. Reasoning effort is explicit when selected.

Only a successful `response.completed` authorizes completed tool calls or a
successful run. Incomplete, failed and disconnected streams have distinct
outcomes. A missing Content-Type is accepted only through the bounded SSE/JSON
parser; an explicitly incompatible MIME type is rejected. Completed output items
are accumulated by index and reconciled before use, including when the terminal
output array is empty. Partial items never authorize execution. Namespaced functions call the existing service executors: file reads,
hash-checked writes, trusted-host commands, structured questions and scoped
Kira/Team operations. Tool results go back to the model with the matching call
identity. Tool replay does not repeat a completed side effect.

The direct provider has no child agent process. HTTP requests close with the
adapter, while locally executed command groups remain owned by the service.
Team reservations require confirmed cleanup; interrupted commands with uncertain
effects are not automatically replayed. Cancellation closes local transport;
this is not proof of zero remote computation or refunded usage.

## Context and capability limits

Kira retains typed Responses state across turns and restarts, keyed to the chat,
provider generation, bound account and model. The durable direct path reads all
original history chronologically and automatically compacts near the selected
model's working limit. It does not apply a fixed request-count history cutoff.
Default and maximum catalog windows remain distinct; the UI identifies estimated
counts separately from provider measurements. Private reasoning stays opaque.
See [Direct context](DIRECT_CONTEXT.md) for checkpoints, important notes, original
source retrieval, limits, recovery and actual route qualification.

Direct chat supports service-managed tools and Team. Native Codex steering,
native subagents and native thread forks are unavailable;
portable branches still work. Explicit image attachments are sent as Responses
`input_image` parts, paired with their original user turn. Pasted path text alone
does not send image bytes. See [image attachments](IMAGE_ATTACHMENTS.md) for
previews, format/size limits and bounded historical resends. Transport capability
does not establish vision entitlement for every model/account.
The current source adds hosted web search and a separate service-owned Chromium
browser; see [Web and browser](WEB_BROWSER.md). Kira executes browser function
tools locally and supplies screenshots as image tool outputs. Desktop 0.8.2 adds
its own `computer.observe`/`computer.act` visual controller, sent as custom tools
in the `kira_computer` namespace with owned image results. It uses the existing
model/account route. OpenAI's provider-native computer tool remains unavailable
on the ChatGPT-plan route; Kira's local tool does not request that entitlement.
Connectors, image generation, audio/video and uploaded-file APIs remain outside
this backend's implemented capabilities.

## IPC additions and evidence

- `backend.discover {backend:"openai_chatgpt",account_id?}` returns a catalog and capabilities.
- `backend.login {backend:"openai_chatgpt",account_id?}` starts explicit sign-in.
- `backend.account {backend:"openai_chatgpt",action,account_id?}` supports `status`,
  `select`, `logout` and `cancel`. Status contains credential-free account labels,
  connection/plan flags, active ID, login state and mutation availability.
- Login states are `idle`, `awaiting_browser`, `exchanging`, `completed`, `failed`
  and `cancelled`. Pending states block new direct admissions.
- `input.delivered` may use `transport:"responses-http"` after the follow-up
  request is accepted. It records delivery, not comprehension.

Database schema 7 adds durable direct context to provider generations and image links; IPC 1 and event
envelope 1 remain unchanged. See
[STATUS](STATUS.md) for current synthetic tests, actual account/inference checks
and package hashes. Synthetic transport success cannot establish plan eligibility.

## Official references

- [Registration and sign-in](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [Accounts and sessions](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
- [Models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [Preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
- [Errors and recovery](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery)

- [Image inputs](https://developers.openai.com/api/docs/guides/images-vision?api-mode=responses)

Desktop 0.13.2 keeps ordinary response validation strict. A typed local output
envelope failure in the separate no-tool native-fold request can select the
existing readable summary, without adopting partial native data or repeating
user work. See [Direct context](DIRECT_CONTEXT.md#unusable-native-output-envelopes-0132).


## Public summary display

Desktop 0.14.1 adds the bounded [public provider summary channel](REASONING_SUMMARIES.md).
It preserves private native reasoning and ordinary execution contracts. Schema 13
is unchanged; activate an update only after verified idle state and a consistent
backup. No active response or historical work is replayed to produce a summary.
