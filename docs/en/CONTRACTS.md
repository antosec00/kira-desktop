# Kira Desktop technical contracts

*[Leggi in italiano](../it/CONTRATTI.md)*

This document is the single reference for anyone developing on Kira or wanting
to understand how it works internally: it describes the IPC protocol between
the interface and the service, the available methods with their parameters,
the durable events, the permissions, the interface a provider must implement,
the tools exposed to the model and the numeric limits. For an overview of the
processes read [Architecture](ARCHITECTURE.md); for the practical meaning of
permissions, [Permissions and security](PERMISSIONS_AND_SECURITY.md).

## 1. Processes and data

### Process chain

```
GUI (PySide6/QML)  →  socket Unix privato  →  servizio asyncio  →  provider
```

- The GUI never executes tools and never writes the database: it sends
  requests, receives responses and events, and presents them.
- Only the service writes SQLite. Every state change is recorded in the
  database before being published to clients.
- Providers (Ollama, Codex, OpenAI direct, DeepSeek, Claude Code) receive a
  `RunContext` from the service and use its callbacks to emit events, execute
  tools and request approvals. A provider has no direct access to the database
  or the GUI.
- Every dictionary that crosses a boundary is JSON. IDs are opaque UUID
  strings; timestamps are ISO 8601 in UTC.

The socket lives in `$XDG_RUNTIME_DIR/kira/service.sock` (or in `runtime/`
under `KIRA_HOME`): the folder has `0700` permissions, the socket `0600`, and a
process lock guarantees a single service per data profile.

### Main entities

| Entity | Fields |
| --- | --- |
| Project | `id, name, root, instructions, permission, created_at, updated_at, memory` |
| Session (chat) | `id, project_id, backend, model, settings, backend_ref, created_at`; the title is stored in `settings.chat_title` and never reaches the model; `archived` is a derived flag |
| Run | `id, session_id, request_id, prompt, state, created_at, updated_at, error, outcome, last_seq` |

Every run also exposes derived metadata: `source` (`user`, `team_followup`,
`goal_continuation`), `execution_lane`, `related_team_id` and
`effective_permission` when the chat runs read-only alongside a Team.
`backend_ref` (the provider's native reference, for example a Codex thread)
belongs exclusively to the provider that produced it: a provider switch
retires the current generation and opens a new one without a reference.

Constraints: a single ordinary active run per project (with the sole exception
of a read-only chat alongside an active Team); a session belongs to a project,
a run to a session; the sessions of Team members cannot be started with
`run.start`.

### Project permissions

| Value | Label in the app | Meaning |
| --- | --- | --- |
| `read_only` | **Read only** | Only reading and searching files inside the project; no commands. |
| `project_write` | **Project write** | Writing files with Kira's tools, limited to the project root; for Codex this corresponds to the `workspace-write` sandbox. No arbitrary commands. |
| `trusted_host` | **Trusted host** | Arbitrary commands executed as the current user. **It is not a sandbox.** |
| `full_access` | **Full access** | Like Trusted host, and in addition the file tools accept paths outside the project, within the limits of the system user's permissions. No automatic administrative privilege. |

The sets used by the code are: write = `project_write, trusted_host,
full_access`; host = `trusted_host, full_access`. The **Plan**, **Review** and
**Recap** modes reduce the execution context to `read_only` without modifying
the saved permission. An approval granted during a run can never widen the
project permission.

### Run states

```
queued → running → [awaiting_input → running]* → completed | failed
running / awaiting_input → cancelling → cancelled | interrupted
qualsiasi stato non terminale trovato al riavvio del servizio → interrupted
```

- The terminal states are `completed`, `failed`, `cancelled`, `interrupted`.
- `completed` follows only the provider's positive terminal outcome: an
  accepted request or an interrupted stream do not produce it.
- A cancellation request (`cancelling`) is not a confirmation of cancellation;
  the confirmation comes with `cancelled`, or with `interrupted` if the
  provider could not confirm.
- A run left incomplete by a service restart becomes `interrupted` and **is
  never repeated automatically**: every uncertain effect stays uncertain until
  the user decides to continue.

### Request identity and idempotency

Every mutation that can have external effects carries a client-chosen
`request_id` (UUID). The service stores the identity and a fingerprint of the
input before producing effects, and always follows the same rule:

- a repetition with **the same** input returns the already recorded result
  without repeating the operation;
- a reuse of the identity with a **different** input fails with `CONFLICT`;
- an operation whose outcome remained uncertain (for example an interrupted
  native launch) stays `unknown` and is not repeated on its own.

This applies to every method that accepts `request_id` (runs, queue, guidance,
forks, provider switches, Teams, goals, native actions, Research, project
tools, Soul, context notes, assistant). The GUI stores pending identities
before sending the RPC, so an application restart can verify the outcome
instead of generating a duplicate.

## 2. IPC protocol

### Frames

The channel carries newline-delimited UTF-8 JSON. Every frame (request,
response or notification) must fit in **1 MiB** including the terminator; a
larger frame is rejected and the client disconnected.

```json
{"version": 1, "id": "c1", "method": "run.start", "params": {"...": "..."}}
{"version": 1, "id": "c1", "result": {"...": "..."}}
{"version": 1, "id": "c1", "error": {"code": "INVALID_ARGUMENT", "message": "..."}}
{"version": 1, "event": {"schema_version": 1, "run_id": "...", "seq": 7, "type": "...", "timestamp": "...", "payload": {}}}
{"version": 1, "team_event": {"schema_version": 1, "team_id": "...", "seq": 3, "type": "...", "timestamp": "...", "payload": {}}}
{"version": 1, "browser_event": {"type": "frame", "project_id": "...", "session_id": "...", "browser_id": "...", "generation": 2, "seq": 41, "payload": {}}}
```

- `version` is the protocol version. A value different from the service's is
  rejected **before** any mutation.
- `id` correlates response and request and is chosen by the client; it has
  nothing to do with the durable `request_id` in the parameters.
- Notifications (`event`, `team_event`, `browser_event`) have no `id` and
  reach only the clients that subscribed (`run.subscribe`, `team.subscribe`,
  `session.browser.watch`).

### Handshake

`hello {}` → `{version, app_version, capabilities}`. The GUI sends it first and
refuses to continue if `app_version` does not match its own. The
`capabilities` are boolean flags (`durable_runs`, `replay`, `tools`, `memory`,
`workspaces`, `desktop_actions`, `diagnostics`, `teams`, `input_questions`,
`workbench`, `prompt_queue`, `run_modes`, `chat_goals`, `autonomous_goals`,
`provider_continuity`, `image_attachments`, `embedded_browser`,
`project_research`, `knowledge`, `project_tools`, `personalized_souls`).

### Validation rules

- Every method accepts only the listed fields: an unknown parameter produces
  `INVALID_ARGUMENT`. Types are checked in the service; booleans are not
  accepted where an integer is expected.
- Strings cannot contain `\0`; unless a different limit applies, a string is
  accepted up to 200,000 encoded bytes (the prompt uses this limit).
- No GUI message contains executable strings: native commands are identified
  by fixed IDs, never by shell text.
- Recurring errors: `INVALID_ARGUMENT`, `NOT_FOUND`, `CONFLICT` (identity
  reused with a different input), `BUSY` (active work or settings being
  updated), `ARCHIVED`, `MANAGED_RUN` (session of a Team), `UNAVAILABLE`,
  `UNSAFE_PATH`, `QUEUE_FULL`, `SHUTTING_DOWN`.
- While `settings.update` is in progress, `run.start`, `team.*`, `goal.start`,
  `session.switch/configure/fork/compact` and the `backend.*` methods respond
  `BUSY`. During shutdown every method that starts work responds
  `SHUTTING_DOWN`.

### Slow consumers

Every client has an outgoing queue of 128 messages. If the queue fills up the
client is disconnected: it can never block the service's durable writes. On
reconnection it catches up with `run.subscribe {after_seq}` (and
`team.subscribe`), deduplicating by `(run_id, seq)`. Browser frames
(`browser_event`) do not go through this queue: only the latest one is kept.

## 3. IPC methods

Convention: `method {parameters} → {result}`; `?` marks an optional field.

### Projects

| Method | Parameters → result | Rules |
| --- | --- | --- |
| `project.list` | `{}` → `{projects:[]}` | Sorted by `updated_at` descending. |
| `project.create` | `{name, root, instructions?, permission?}` → `{project}` | `permission` defaults to `read_only`. |
| `project.update` | `{project_id, name?, instructions?, permission?, memory?, share_session_summaries?}` → `{project}` | A permission change is rejected while work is active. `share_session_summaries` is an atomic flag in the project memory. |
| `project.snapshot` | `{project_id}` → `{project, sessions:[], runs:[], artifacts:[], messages:[]}` | Every run includes `last_seq`; messages have `run_id, role, content` and, for the assistant, `item_id` and `complete`. The payload is limited to 700 KB with previews and flagged omissions. |
| `project.context` | `{project_id, session_id?}` → `{content, sources, truncated, enabled, chars}` | Shows the block of excerpts from other chats that Kira will supply on the next turn. |
| `project.files` | `{project_id, query?, limit?}` → `{files:[], truncated, scanned, gitignore, limits:{entries, depth, matches}}` | Search contained in the root for `@` mentions; `limit` 1–200 (40), `query` up to 1,000 characters; at most 10,000 entries and depth 32 explored. |
| `project.changes` | `{project_id, path?}` → `{diff, truncated, ...}` | Staged/unstaged/untracked Git changes as inert text; runs no hooks or external drivers; response limited to 650 KB. |
| `project.export` | `{project_id}` → `{text}` or `{path, format:"json", bytes}` | Up to 700 KB the JSON is inline; beyond that it is written to `exports/` in the data folder and the client copies it to the chosen destination. No truncation of the transcript. |

### Sessions and chats

| Method | Parameters → result | Rules |
| --- | --- | --- |
| `session.create` | `{project_id, backend, model, settings?, handoff?, title?, soul_id?}` → `{session}` | `title` up to 120 characters; `soul_id` binds the Soul before the first message. |
| `session.list` | `{project_id, offset?, limit?, archived?}` → `{sessions, has_more, next_offset}` | Ordinary chats only, most recent first; `limit` ≤ 200, page ≤ 700 KB; `archived` defaults to `false`. |
| `session.snapshot` | `{project_id, session_id}` → `{session, runs, artifacts, messages, ...}` | Last 60 runs in chronological order, at most 200 artifacts, 700 KB. |
| `session.rename` | `{project_id, session_id, title}` → `{session}` | Owned ordinary chats only; does not touch the provider thread. |
| `session.archive` | `{project_id, session_id, archived}` → `{session}` | Requires an inactive chat; an archived chat starts no work but remains readable and exportable. |
| `session.export` | `{project_id, session_id}` → `{text}` or `{path, ...}` | Full Markdown: transcript, accepted guidance, partial streams, activity, goal. Team chats and other users' chats are rejected. |
| `session.fork` | `{project_id, session_id, request_id, through_run_id?, title?}` → `{session, mode:"native"\|"copied_context", ...}` | `through_run_id` must be a completed turn; Codex requires a recorded native turn boundary, otherwise it forks only from the end. The copied context is limited to 8,000,000 UTF-8 bytes, the excerpt supplied to the model to 100,000 characters. Does not undo file changes. |
| `session.configure` | `{project_id, session_id, model, reasoning_effort?, num_ctx?, think?}` → `{session}` | Same provider, inactive chat (even with a paused queue). `reasoning_effort` omitted = unchanged, `""` = default; `num_ctx` 0 removes the override; `think` accepts the Ollama values or `null`. |
| `session.providers` | `{project_id, session_id}` → `{session, current_generation, providers:[{backend, model, reasoning_effort, account_id, used, current}], transitions:[...], transitions_truncated}` | Read-only; up to 100 recent generations. |
| `session.switch` | `{project_id, session_id, request_id, backend, model, reasoning_effort, account_id?}` → `{session, current_generation, replayed, switch:{request_id, generation, backend, model, account_id, changed, transitioned}}` | `reasoning_effort` is required even when empty; `account_id` only for OpenAI direct. Requires an inactive chat with no queues, Teams or reservations. A repetition returns the original receipt plus the **current** session. |
| `session.compact` | `{project_id, session_id, request_id}` → `{operation}` | Native compaction (Codex) of an inactive chat; completion requires the provider's terminal outcome. |
| `session.context` | `{project_id, session_id}` → `{policy, measures, memory, notes, operation}` | Public state of the context managed by Kira for OpenAI direct and DeepSeek Account: capacity, estimates, generated memory, important notes, latest receipt. |
| `session.context.pin` / `.unpin` | `{project_id, session_id, request_id, content}` / `{..., note_id}` | Important user notes, supplied verbatim on every turn. |
| `session.context.compact` | `{project_id, session_id, request_id}` → `{operation}` | Explicit context maintenance, only on an inactive chat; reserves the project root. |
| `session.history.search` | `{project_id, session_id, query, limit?, before_id?}` → `{results, ...}` | Literal Unicode search in the chat's original archive; bounded pages. |
| `session.history.read` | `{project_id, session_id, source_id, offset?, limit?}` → `{content, next_offset, ...}` | Paginated reading of an original record, up to 32,000 characters per page. |
| `session.web.get` | `{project_id, session_id}` → `{settings, browser}` | Web preferences and state/preview of the chat's browser. |
| `session.web.update` | `{project_id, session_id, web_search?, browser_enabled?, browser_persistent?, browser_action_approval?, expected_generation?}` → `{settings, browser}` | `web_search` ∈ `live\|cached\|disabled` (OpenAI direct only); `browser_action_approval` alone (`ask\|always`) is allowed even during a turn and, with `expected_generation`, cannot grant consent to a retired generation; the other preferences require an inactive chat. |
| `session.browser.open` / `.close` | `{project_id, session_id}` | Opens or closes the chat's browser actor; closing waits for the verified cleanup of the process. |
| `session.browser.watch` / `.unwatch` | `{project_id, session_id}` → `{input_stream_id, ...}` | Subscribes the client to `browser_event` frames; the input stream is bound to client and actor and dies with the connection. |
| `session.browser.command` | `{project_id, session_id, stream_id, sequence, request_id, action, arguments}` | Manual input (mouse, keys, text, copy) during **Take control**. `sequence` is monotonic; the last 128 receipts plus a high-water mark reject duplicate, stale or uncertain input. The command must refer to the displayed `surface_id`. |

### Runs

| Method | Parameters → result | Rules |
| --- | --- | --- |
| `run.start` | `{session_id, prompt, request_id, mode?, attachments?:[id]}` → `{run}` | `mode` ∈ `execute\|plan\|review\|recap` (default `execute`) and is part of the durable identity. The prompt can be empty only with attachments. Rejected for archived chats, Team sessions, an already active project or a Team handoff being prepared. |
| `run.cancel` | `{run_id}` → `{run}` | Requests cancellation; pauses the chat's autonomous goal and suppresses automatic replies on pending Team results. For Team members use `team.cancel`. |
| `run.subscribe` | `{run_id, after_seq?}` → `{events:[], last_seq, has_more}` | Bounded replay (250 events) **and** registration for live delivery, atomic with respect to emission. If `has_more` is true another `subscribe` is needed. |
| `run.events` | `{run_id, after_seq?, limit?}` → `{events:[], last_seq, has_more}` | Replay only; `limit` 1–500 (250). |
| `run.approve` | `{run_id, approval_id, decision}` → `{accepted}` | `decision` ∈ `accept\|accept_always\|decline\|cancel`. `accept_always` applies only to a pending browser action recorded by the service with `allow_always:true` and stores `browser_action_approval:always` for that chat. `accepted:false` means nothing was waiting. |
| `run.steer` | `{run_id, request_id, prompt}` → `{run_id, session_id, project_id, status, request_id, accepted, ...}` | Guidance for that chat's active ordinary run; up to 32,000 bytes; does not change model, effort, permissions or attachments. The receipt has `accepted:false` until the provider confirms; a repetition with the same identity returns the recorded delivery state. |

### Queue

| Method | Parameters → result |
| --- | --- |
| `queue.list` | `{project_id}` → `{items, paused, limit}` |
| `queue.add` | `{session_id, request_id, prompt, mode?, attachments?}` → `{item, items, paused, limit}` |
| `queue.edit` | `{project_id, item_id, prompt}` → `{item, items, paused, limit}` (`pending` items only) |
| `queue.reorder` | `{project_id, item_ids}` → updated list (exact order of the waiting items) |
| `queue.remove` | `{project_id, item_id}` → updated list (waiting or finished items only) |
| `queue.pause` / `queue.resume` | `{project_id}` → updated list with `paused` |

An item has `id` (also as `item_id`), `project_id, session_id, request_id,
prompt, mode, position, status, run_id, error, created_at, updated_at,
attachments`; the states are `pending, starting, running, completed, failed,
cancelled, interrupted`. The list shows the active items and the last 10
finished ones, with abbreviated prompt and error. At most 64 unfinished items
per project, each up to 8,000 UTF-8 bytes (JSON escaping included). A removed
identity remains as a tombstone and cannot be resurrected. The queue proceeds
after a confirmed completion and stops on its own after an error, a
cancellation or a service restart. Editing or removing an item never cancels
an operation already sent to the provider.

### Questions

| Method | Parameters → result |
| --- | --- |
| `input.list` | `{project_id, offset?, limit?}` → `{requests, has_more, next_offset}` |
| `input.answer` | `{run_id, request_id, answers:{question_id:{answers:[text]}}}` → `{accepted:true, request}` |

A request contains `run_id, request_id, questions, blocking, status, received,
delivered` and, when present, `answers, reason, delivery_transport`. Pages
contain complete forms within 700 KB: all pending requests and the last 100
closed ones, including those of Team members. Exactly one non-empty answer per
question is required; if the question does not allow "other", the answer must
be one of the choices. Answers do not change permissions, do not approve
commands and do not start runs. Secret questions (`isSecret`) are rejected:
access goes through the provider's login flow.

### Goals

| Method | Parameters → result | Rules |
| --- | --- | --- |
| `goal.get` | `{project_id, session_id}` → `{goal, autonomous?}` | Pure read; `autonomous` contains state, cycle count, summary, next step, error, references and metadata of the current run. |
| `goal.update` | `{project_id, session_id, objective?, status?}` → `{goal}` | The chat's manual goal; `status` ∈ `active\|paused\|completed`. The active goal enters the context of subsequent turns; it starts nothing. |
| `goal.start` | `{project_id, session_id, request_id, objective, max_cycles?}` → `{goal}` | Arms the autonomous goal; `max_cycles` 0 (default) = no cap. Preserves provider, account, model, effort and permission. |
| `goal.pause` / `goal.resume` | `{project_id, session_id, request_id}` → `{goal}` | Pausing lets the current cycle finish; resuming is a new admission check. A service restart does not resume an uncertain cycle on its own. |
