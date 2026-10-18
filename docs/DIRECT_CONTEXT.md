# OpenAI direct context and chat memory

Kira maintains a private working context for each direct chat, provider generation,
account and model. Original messages, questions, tool observations and image assets
remain independent archival records. Compaction replaces the working representation;
it does not delete the conversation or modify project files.

## Capacity and request planning

The model catalog retains `context_window`, `max_context_window` and any reported
`max_output_tokens` separately. Automatic maximum mode selects the largest valid
reported context window for that model/account. Missing metadata remains unknown.
Catalog metadata is not a stress test or proof of entitlement to every model.

Kira reserves 18% of the window for output/reasoning/tool growth. A valid reported
maximum output allowance can increase that reservation with a further 3% planning
margin. Small-window test configurations retain appropriate bounded reserves.
These are local planning margins, not claims about a provider's output cap. The
ChatGPT route's unsupported `max_output_tokens` parameter is never sent.

The previous 30-run / 200,000-byte policy is not used by the durable direct path.
All original history is paged chronologically. Token planning precedes ordinary
requests and tool-loop continuations. A supported provider preflight counter is
preferred; otherwise the UI labels the calibrated estimate distinctly from the
actual token counts returned for completed requests. Cached input still occupies
context. Images remain approximate. Opaque native state cannot be measured from
its encrypted byte length: without provider preflight counting, Kira reports a
partial estimate of visible content and leaves its total size unknown.
Provider-native context management evaluates its actual token threshold. Ciphertext
size never creates a fabricated context-full result; the transport bound still
applies to all bytes.

A 32 MiB encoded request bound remains separate from the token window. Existing
file, image and tool output limits also remain explicit. Transport pressure must
not cause silent loss of original records or unbounded in-memory bootstrap.

## Native compaction and fallbacks

The preferred mechanism is Responses `context_management` with a model-relative
`compact_threshold`, using `store:false` and `stream:true`. A successful response
can contain opaque `compaction` items. Kira preserves them privately and retains
the appropriate canonical continuation at a fully paired tool boundary. The
content is never decoded into a user-visible reasoning trace.

For explicit maintenance, Kira uses the exact selected model and reasoning effort.
A no-tool native pass keeps ordinary conversation instructions and input; only a
confirmed compaction emitted before new assistant output can become the next
working checkpoint. Newly generated continuation is discarded. A separate
no-tool branch produces source-linked readable memory. Its summarization prompt,
output format and provider-private items never become ordinary conversation state.
This separation prevents internal handoff requests from becoming the user's next
task. Maintenance can consume plan allowance and add latency, including separate
native and readable-memory requests. It is recorded as context maintenance, not
as fabricated user chat turns. Empty context is an explicit no-op.

The adapter attempts provider preflight counting when supported by the
authenticated route. A capability-specific rejection can mark that auxiliary
interface unavailable; an ordinary authentication or network
failure is not treated as permission to change providers or billing. An explicit
HTTP admission rejection of `context_management` may select the structured
fallback before any response is accepted. Accepted or uncertain inference is
never blindly retried.

If native compaction is unavailable, a structured checkpoint carries a readable
historical summary with validated source references. It uses the selected model
and effort and is identified as Kira-managed summarization. Failure retains the
previous valid checkpoint and original records. Neither native compaction nor a
generated summary guarantees perfect semantic retention.

## Unusable native output envelopes (0.13.2)

A local `OutputItemValidationError` distinguishes invalid item index type,
negative index, the local 64-item limit and non-object output envelopes. Safe
metadata records only the fixed event/item categories, bounded numeric index,
Python JSON shape types, local limit and maintenance phase. It never retains
rejected item text, IDs, opaque state or raw SSE. The former generic message
alone cannot establish which field failed in an earlier request.

Only the no-tool auxiliary native-fold request may recover from this exact local
validation exception. It closes/discards the unusable response, emits a visible
service recovery notice and uses the existing structured-summary branch over the
original ordinary context. No ordinary request, tool or external action is
replayed. Partial native output, including tool-like items, is never adopted.
The selected model, account, effort, scope and original archive are retained.

Native server compaction is marked unavailable for this saved context after the
successful fallback. A new identity or subsequently validated native compaction
can establish support again. The previous checkpoint is retained until a valid,
source-grounded summary succeeds; a summary failure leaves it and the original
history intact. Readable summaries remain fallible and exact sources stay
retrievable. An accepted native request may consume allowance even when its
output cannot be used; missing usage is never represented as zero.

Authentication, quota, transport errors, cancellation and ordinary-turn output
errors do not trigger this recovery. Generic provider error codes cannot impersonate
the local typed exception. Other malformed envelopes remain explicit failures.
No stream item limits are relaxed. The GUI distinguishes this service notice
from model text and retains original non-content diagnostics in its details.

## Durable state and recovery

Schema 7 adds private direct-context states/checkpoints, operation receipts and
reservations, important notes, and a derived source index. Existing schema 1–6
profiles receive a consistent private backup and an additive transactional
migration. Original application tables and conversation events remain intact.

State is keyed by session, provider generation, bound account and exact model.
Updates compare revisions and commit atomically. Normal paired saves replace the
latest state; compactions retain the preceding recovery checkpoint. Opaque native
state, capability observations, source cursor and generated memory survive restart.
If the same model's reported window shrinks and its prior state cannot be
validated against the smaller window, Kira rebuilds from original records and
archives the preceding checkpoint before committing a replacement. Current image
priority is preserved during the rebuild.
Model changes use only compatible owned state and append portable original records
since its saved cursor. Different providers/accounts never receive another
identity's encrypted state.

Only confirmed complete responses and paired tool results enter reusable native
state. Historical tool requests are data and are never replayed as executable
callbacks during bootstrap. Existing execution idempotency and uncertain-outcome
rules remain authoritative. Manual operations keep request identities; retrying a
receipt does not start a duplicate compaction. Service restart marks unfinished
maintenance unknown and does not automatically repeat it.

## Important information and original sources

Desktop 0.10.0 makes explicit pinned chat notes available to all ordinary chat
providers. Only OpenAI direct uses this document's automatic compaction contract.
The common knowledge gateway retrieves original sources and the latest readable
summary with provenance; it never transfers encrypted provider-private state.

User-maintained chat notes remain separate from generated memory and project notes.
They are supplied literally with current project instructions. Limits on note
count and aggregate size are visible validation bounds, never silent eviction.

Generated memory identifies objectives, constraints, decisions, corrections,
recorded results, open questions and next steps. Entries reference original
chat-owned source IDs. Model-produced entries remain claims; valid references
establish provenance, not proof that the model interpreted the source correctly.
Later corrections must take precedence over superseded information.

The `kira_history` namespace exposes read-only `search` and `read` for the current chat.
Search is Unicode-aware literal matching with bounded paginated results. Reads
preserve source IDs, run/event identity and continuation offsets. The initial
implementation does not use an external embedding service or semantic ranking.

The index includes original user/assistant text, incomplete visible assistant
output, recorded tool arguments/results and structured questions/answers.
Requested tools and recorded answers are distinguished from confirmed execution
and delivery. Service-owned full event artifacts are recovered only with validated
ownership, path and hash; missing or previously truncated material is disclosed.
The archive is bootstrapped from stored originals, not the older clipped portable
history. Previously unpersisted native reasoning cannot be reconstructed.

Images are stored as private immutable asset references, not base64 database fields.
Current selected images have priority within the image request budget. Historical
missing/over-budget or already supplied assets have explicit context notices;
original image files and transcript attachment links remain available.

## IPC and interface

- `session.context {project_id,session_id}` returns public policy, measures,
  generated memory, important notes and the latest operation receipt.
- `session.context.pin {project_id,session_id,request_id,content}` records an
  important user note; `session.context.unpin` uses `note_id`.
- `session.context.compact {project_id,session_id,request_id}` starts explicit
  maintenance only after idle admission and returns a durable operation receipt.
- `session.history.search {project_id,session_id,query,limit?,before_id?}` and
  `session.history.read {project_id,session_id,source_id,offset?,limit?}` expose
  bounded original records after ownership checks.

Maintenance reserves the project/root, blocking overlapping execution and account
mutation. Read-only snapshots do not run compaction. The dialog can poll a pending
manual operation while visible; normal runs update from durable `context.status`
and `context.compaction` events. These events contain whitelisted public metadata,
never native encrypted payloads.

The interface separates default/maximum/active capacity, reserved room, last
measured input and the last context estimate or provider preflight count. These
planning measurements may precede the newest response; they are not a live meter
of the complete next request. It provides **Context and memory**,
**Compact now**, important notes and paginated original sources. Current model,
account, project permissions and reasoning effort are preserved.

## Qualification and future work

See [STATUS](STATUS.md) for exact current synthetic, installed and live evidence.
Small-window synthetic/live tests exercise repeated compaction and retention;
they do not establish successful inference at every model's maximum window.
A future research comparison can evaluate ranked retrieval, versioned decisions,
subtask memories and stable prompt prefixes using retention, correction accuracy,
source fidelity, latency and provider usage. Such proposals are not release claims.

References: [Responses compaction](https://developers.openai.com/api/docs/guides/compaction),
[token counting](https://developers.openai.com/api/docs/guides/token-counting),
[ChatGPT route limits](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations).
