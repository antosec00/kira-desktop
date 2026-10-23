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
GUI (PySide6/QML)  →  private Unix socket  →  asyncio service  →  providers
```

- The GUI never executes tools and never writes the database: it sends
  requests, receives responses and events, and presents them.
- Only the service writes SQLite. Every state change is recorded in the
  database before being published to clients.
- Providers (Ollama, Codex, Direct OpenAI, DeepSeek, Claude Code) receive a
  `RunContext` from the service and use its callbacks to emit events, execute
  tools and request approvals. A provider has no direct access to the database
  or the GUI.
- Every dictionary that crosses a boundary is JSON. IDs are opaque UUID
  strings; timestamps are ISO 8601 in UTC.

The socket lives in `$XDG_RUNTIME_DIR/kira/service.sock` (with `KIRA_HOME`, in
`runtime/` under that folder; without `XDG_RUNTIME_DIR`, in `runtime/` under the
state folder): the folder has `0700` permissions, the socket `0600`, and a
process lock guarantees a single service per data profile.

### Main entities

| Entity | Fields |
| --- | --- |
| Project | `id, name, root, instructions, permission, created_at, updated_at, memory` |
| Session (chat) | `id, project_id, backend, model, settings, backend_ref, created_at`; the title is stored in `settings.chat_title` and is not sent with the chat's own requests (other chats' titles appear in cross-chat excerpts); `archived` is a derived flag |
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
any non-terminal state found at service restart → interrupted
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
tools, Soul, context notes, assistant). For chat requests (runs, queue,
guidance, forks, provider switches, goals, compactions, context notes,
assistant) and for Soul, Research and project tool changes, the GUI stores the
pending identity before sending the RPC, so an application restart can verify
its outcome instead of generating a duplicate. Team commands and native
launches keep their identity only while the window is open: after a restart,
check **Activity** or the Team view before repeating them.

## 2. IPC protocol

### Frames

The channel carries newline-delimited UTF-8 JSON. Every frame (request,
response or notification) must fit in **1 MiB** including the terminator. A
larger request closes the connection; a response or notification that would
exceed the limit is replaced by an `OUTPUT_LIMIT` error, and an oversized
browser frame is discarded.

```json
{"version": 1, "id": "c1", "method": "run.start", "params": {"...": "..."}}
{"version": 1, "id": "c1", "result": {"...": "..."}}
{"version": 1, "id": "c1", "error": {"code": "INVALID_ARGUMENT", "message": "..."}}
{"version": 1, "event": {"schema_version": 1, "run_id": "...", "seq": 7, "type": "...", "timestamp": "...", "payload": {}}}
{"version": 1, "team_event": {"schema_version": 1, "team_id": "...", "seq": 3, "type": "...", "timestamp": "...", "payload": {}}}
{"version": 1, "browser_event": {"type": "frame", "project_id": "...", "session_id": "...", "browser_id": "...", "generation": 2, "seq": 41, "payload": {}}}
```

- `version` is the protocol version. A value different from the service's is
  rejected with `INCOMPATIBLE_VERSION` **before** any mutation.
- `id` correlates response and request and is chosen by the client; it has
  nothing to do with the durable `request_id` in the parameters.
- Notifications (`event`, `team_event`, `browser_event`) have no `id`. `event`
  and `team_event` reach only the clients that subscribed (`run.subscribe`,
  `team.subscribe`); `browser_event` frames reach only the clients watching
  that browser (`session.browser.watch`), while browser state changes reach
  every connected desktop, so it can show a browser opened by the agent.

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
  updated), `ARCHIVED`, `MANAGED_RUN` (session of a Team), `UNSUPPORTED`
  (feature not available with that provider or in that chat),
  `PERMISSION_DENIED`, `UNAVAILABLE`, `UNSAFE_PATH`, `QUEUE_FULL`,
  `OUTPUT_LIMIT`, `SHUTTING_DOWN`. The transport also uses `INVALID_REQUEST`
  (invalid frame), `METHOD_NOT_FOUND` and `INTERNAL_ERROR`.
- While `settings.update` is in progress, `run.start`, `team.start`,
  `team.resume`, `goal.start`, `goal.resume`, `assistant.resume`,
  `session.switch/configure/fork/compact`, another `settings.update` and the
  `backend.*` methods respond `BUSY`. During shutdown every method that starts
  work, native launches included (`desktop.launch`, `workspace.launch`),
  responds `SHUTTING_DOWN`.

### Slow consumers

Every client has an outgoing queue of 128 messages. If the queue fills up, or a
frame cannot be written within 5 seconds or encoded, the client is
disconnected: it can never block the service's durable writes. On reconnection
it catches up with `run.subscribe {after_seq}` (and `team.subscribe`),
deduplicating by `(run_id, seq)`. Browser images (`browser_event` frames) do
not go through this queue: only the latest one is kept.

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
| `project.files` | `{project_id, query?, limit?}` → `{files:[], truncated, scanned, gitignore, limits:{entries, depth, matches}}` | Search contained in the root for `@` mentions; `limit` 1–200 (40), `query` up to 1,000 UTF-8 bytes; at most 10,000 entries and depth 32 explored. |
| `project.changes` | `{project_id, path?}` → `{diff, truncated, ...}` | Staged/unstaged/untracked Git changes as inert text; runs no hooks, filters or external drivers; response limited to 650 KB. |
| `project.export` | `{project_id}` → `{text}` or `{path, format:"json", bytes}` | Up to 700 KB the JSON is inline; beyond that it is written to `exports/` in the data folder and the response gives its path. No truncation of the transcript. The interface does not offer it: use `kira-desktop rpc project.export '{"project_id": "…"}'`. |

### Sessions and chats

| Method | Parameters → result | Rules |
| --- | --- | --- |
| `session.create` | `{project_id, backend, model, settings?, handoff?, title?, soul_id?}` → `{session}` | `title` up to 120 characters; `soul_id` binds the Soul before the first message. Settings the provider does not use are not saved (some are rejected with `UNSUPPORTED_SETTING`, for example `num_ctx` with DeepSeek or Claude Code); malformed values are rejected. |
| `session.list` | `{project_id, offset?, limit?, archived?}` → `{sessions, has_more, next_offset}` | Ordinary chats only, most recent first; `limit` ≤ 200, page ≤ 700 KB; `archived` defaults to `false`. |
| `session.snapshot` | `{project_id, session_id}` → `{session, runs, artifacts, history_truncated}` | Last 60 runs in chronological order, at most 200 artifacts, 700 KB. |
| `session.rename` | `{project_id, session_id, title}` → `{session}` | Owned ordinary chats only; does not touch the provider thread. |
| `session.archive` | `{project_id, session_id, archived}` → `{session}` | Requires a project with no active work and, to archive, no queued requests of the chat; an archived chat starts no work but remains readable and exportable. |
| `session.export` | `{project_id, session_id}` → `{text}` or `{path, ...}` | Full Markdown: transcript, accepted guidance, partial streams, activity, goal. Team chats and chats of other projects are rejected. |
| `session.fork` | `{project_id, session_id, request_id, through_run_id?, title?}` → `{session, mode:"native"\|"copied_context", ...}` | `through_run_id` must be a completed turn; Codex requires a recorded native turn boundary, otherwise it forks only from the end. The copied context is limited to 8,000,000 UTF-8 bytes, the excerpt supplied to the model to 100,000 characters. Does not undo file changes. |
| `session.configure` | `{project_id, session_id, model, reasoning_effort?, num_ctx?, think?}` → `{session}` | Same provider; requires a project with no active work and no queued requests, even when the queue is paused. `reasoning_effort` omitted = unchanged, `""` = default (DeepSeek Web neither uses nor saves it); `num_ctx` (Ollama only) ∈ 0, 32,768, 131,072, 262,144, where 0 removes the override; `think` (Ollama only) accepts `true`, `false`, `low`, `medium`, `high` or `null`. |
| `session.providers` | `{project_id, session_id}` → `{session, current_generation, providers:[{backend, model, reasoning_effort, account_id, used, current}], transitions:[...], transitions_truncated}` | Read-only; up to 100 recent generations. |
| `session.switch` | `{project_id, session_id, request_id, backend, model, reasoning_effort, account_id?}` → `{session, current_generation, replayed, switch:{request_id, generation, backend, model, account_id, changed, transitioned}}` | `reasoning_effort` is required even when empty (DeepSeek Web does not save it); `account_id` only for Direct OpenAI. Settings the new provider does not use are not carried over. Requires an inactive chat with no queues, Teams or reservations. A repetition returns the original receipt plus the **current** session. |
| `session.compact` | `{project_id, session_id, request_id}` → `{operation}` | Native compaction (Codex) of an inactive chat; completion requires the provider's terminal outcome. |
| `session.context` | `{project_id, session_id}` → `{backend, model, account_id, provider_generation, mode, policy, metrics, memory:{pins, summary, sources}, status, operation, revision, has_state, error}` | Public state of the chat's context. With Direct OpenAI and DeepSeek Account, where Kira manages the context, it includes capacity, estimates, generated memory and the latest receipt; the important notes are in `memory.pins`. |
| `session.context.pin` / `.unpin` | `{project_id, session_id, request_id, content}` / `{..., note_id}` | Important user notes, supplied verbatim on every turn. They require an unarchived chat (`ARCHIVED`), no active work in the project or an overlapping one and no queued requests (`BUSY`). |
| `session.context.compact` | `{project_id, session_id, request_id}` → `{context, operation}` | Explicit context maintenance with Direct OpenAI and DeepSeek Account, only on an inactive chat with no queued requests; reserves the project root. |
| `session.history.search` | `{project_id, session_id, query, limit?, before_id?}` → `{results, ...}` | Literal Unicode search in the chat's original archive; bounded pages. |
| `session.history.read` | `{project_id, session_id, source_id, offset?, limit?}` → `{content, next_offset, ...}` | Paginated reading of an original record, up to 32,000 characters per page. |
| `session.web.get` | `{project_id, session_id}` → `{settings, browser}` | Web preferences and state/preview of the chat's browser. |
| `session.web.update` | `{project_id, session_id, web_search?, browser_enabled?, browser_persistent?, browser_action_approval?, expected_generation?}` → `{settings, browser}` | `web_search` ∈ `live\|cached\|disabled` (Direct OpenAI only); `browser_action_approval` alone (`ask\|always`) is allowed even during a turn and, with `expected_generation`, cannot grant consent to a retired generation; the other preferences require an inactive chat. |
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
cancellation or a service restart. A start refused only by the current state
(`BUSY`, `ARCHIVED`, `SHUTTING_DOWN`) leaves the request waiting in the paused
queue, with the reason in `error`. Editing or removing an item never cancels
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

### Teams

```
team.start {project_id, request_id, origin_session_id?, goal,
            mode:"manual"|"automatic", backend:"codex"|"openai_chatgpt"|"ollama"|"deepseek_account",
            model, settings:{reasoning_effort?, service_tier?, ...},
            members?:[{id, name, role, instructions, access:"read"|"write"}],
            max_agents?:4, concurrency?:2, round_limit?:3}  →  {team}
```

| Method | Parameters → result | Rules |
| --- | --- | --- |
| `team.list` | `{project_id}` → `{teams:[summaries]}` | Most recent first, bounded list. |
| `team.snapshot` | `{team_id}` → `{team}` | See the shape below; ≤ 650 KB with `omitted`. |
| `team.events` | `{team_id, after_seq?, limit?}` → `{events, last_seq, has_more}` | `limit` ≤ 250, pages also bounded in bytes. |
| `team.subscribe` | `{team_id, after_seq?}` → like `team.events` | Replay plus gap-free registration; notifications use `team_event`. |
| `team.send_message` | `{team_id, request_id, recipient_id:"all"\|member_id, content}` → `{message}` | The `user` sender is derived by the service. A message to a finished Team is only recorded. |
| `team.cancel` | `{team_id}` → `{team}` | Stops planning and cancels/discards every child. |
| `team.resume` | `{team_id, request_id, instruction?, round_limit?, origin_session_id?}` → `{team}` | New continuation linked by `parent_team_id`; `round_limit` 0–10,000 (0 = no cap; omitted = inherited). Does not repeat old assignments. |
| `team.approve` | `{team_id, run_id, approval_id, decision:"accept"\|"decline"\|"cancel"}` → `{accepted}` | Verifies that the run belongs to the Team; the provider's permission policy stays unchanged. |

Composition rules: in manual mode the roster is exactly the one supplied, with
`coordinator` as the first member; in automatic mode `max_agents` is the total
cap and the coordinator generates the roster in the first plan. Member IDs
match `[a-z][a-z0-9_-]{0,47}`; `all`, `user`, `human` and `system` are
reserved. `access:"read"` corresponds to `read_only`; `access:"write"`
inherits the project permission and is rejected in a read-only project. The
coordinator's planning is always read-only. A completed provider turn does not
certify that the task succeeded: the next member verifies the real effects.

Snapshot of a Team:

```
{id, project_id, goal, mode, state, phase, round_index, round_limit, concurrency,
 max_agents, backend, model, settings, parent_team_id, created_at, updated_at, last_seq,
 summary, error, pause_reason, scheduling,
 completion_evidence:{worker_assignments_completed, basis, coordinator_only_conclusion},
 members:[{id, name, role, instructions, access, state, run_id?, last_output?}],
 steps:[{id, member_id, title, instructions, access, depends_on, state, run_id?, round_index, error?}],
 messages:[{id, seq, sender_id, sender_name, recipient_id, content, created_at}],
 approvals:[{run_id, approval_id, member_id, request}], omitted:{...}}
```

Team states: `planning, running, cancelling, completed, failed, cancelled,
interrupted, paused`. After a terminal state or a pause, only an explicit
continuation starts further work. Approval pages show at most 64 identities at
a time; the GUI can decline or cancel from a truncated preview, but not
approve.

Coordinator output (a single JSON object constrained by the schema):

```
{decision:"continue"|"completed"|"needs_input", summary:string,
 members:[{id, name, role, instructions, access}],
 tasks:[{id, title, member_id, instructions, access, depends_on:[task_id]}]}
```

Only the first automatic plan returns `members` (with `coordinator` first);
the other plans return `members:[]`. Task IDs are unique within the round and
dependencies form an acyclic graph in the same round. With `completed` or
`needs_input`, `tasks` is empty; an empty `continue` is not a successful run
and records `team.plan.feedback`, with at most one corrective turn within the
round budget. A Team with several members completes only with clean worker
receipts (from the current Team or from a chain of continuations in the same
project at most 32 links long); exhausting the budget or a lack of progress
pauses it with `no_progress`.

### Assistant (delegation from the chat)

| Method | Parameters → result |
| --- | --- |
| `assistant.snapshot` | `{project_id, session_id}` → `{environment, delegations:[...], followups:[...], omitted_count}` |
| `assistant.cancel` | `{project_id, session_id, delegation_id}` → `{delegation}` |
| `assistant.message` | `{project_id, session_id, delegation_id, request_id, recipient_id, content}` → `{message}` |
| `assistant.resume` | `{project_id, session_id, delegation_id, request_id, round_limit?}` → `{delegation}` |

Every identity is verified against the current ordinary chat. The snapshot
exposes capability and scope (`environment.capabilities`;
`environment.conversation` explains any read-only lane alongside a Team), a
bounded list of recent delegations with their effective `round_limit` and the
automatic-reply receipts (`followups`, states `armed, pending, running,
completed, failed, interrupted, cancelled, suppressed`). Snapshot ≤ 650 KB;
the GUI keeps at most 128 pending intents without eviction.

### Research

All methods require `project_id`.

| Method | Parameters → result | Rules |
| --- | --- | --- |
| `research.list` | `{project_id, cursor?, limit?, view?, query?}` → `{project_id, records, settings, has_more, next_cursor}` | `view` ∈ `all`, `knowledge` (objectives, hypotheses, decisions), `evidence` (experiments and observations), `next` (open entries), `proposals` (not accepted); `limit` 20 (max 30). |
| `research.get` | `{project_id, record_id, revision?}` → `{project_id, record}` | |
| `research.history` | `{project_id, record_id, before_revision?, limit?}` → `{project_id, revisions, has_more, next_cursor}` | Most recent revisions first. |
| `research.save` | `{project_id, request_id, record_id?, expected_revision?, reason?, reassess?, record}` → `{project_id, record, operation}` | `record` contains only the editable fields; editing an existing record requires `reason`; `reassess` is reserved to the user. |
| `research.configure` | `{project_id, request_id, expected_revision, enabled}` → `{project_id, settings, operation}` | `settings = {enabled, revision}`; disabled by default. |
| `research.sources` | `{project_id, run_id?, cursor?, limit?}` → `{project_id, items, has_more, next_cursor}` | Without `run_id` lists the runs; with `run_id` the eligible events and artifacts. |
| `research.source.read` | `{project_id, source, offset?, limit?}` → `{content, offset, next_offset, has_more, source, provenance, availability, integrity}` | Up to 16,000 characters requested per page. |
| `research.dependencies.capture` | `{project_id, paths}` → `{dependencies:[{path, sha256}]}` | Bounded reads; neither writes nor modifies records. |
| `research.recheck` | `{project_id, request_id, record_id, expected_revision}` → `{project_id, record, operation}` | The outcome is computed by the service. |
| `research.context` | `{project_id}` → `{project_id, enabled, content, records:[{id, revision}], truncated}` | Bounded preview of the accepted records the assistant can read with its tools; it is not added to the model's context. |

A record has `id, project_id, revision, kind` (`objective, hypothesis,
experiment, observation, decision, issue`), `title, body, scope, criteria,
procedure, limitations, status` (`open|closed`), `assessment` (`untested,
supported, contradicted, inconclusive, needs_recheck`), `accepted, sources,
dependencies, links`, plus origin (`user` or `assistant` with the originating
run) and timestamps. Sources are `{kind:"run", run_id}`, `{kind:"event",
run_id, seq}` or `{kind:"artifact", artifact_id}` from the same project;
dependencies `{path, sha256}`; links `{record_id, revision, relation}` with
`relation` ∈ `supports|contradicts|supersedes|relates_to`. `supported` or
`contradicted` require scope, criteria and at least one source. Model
proposals are born not accepted and `untested` and cannot accept themselves.

### Knowledge and project tools

| Method | Parameters → result |
| --- | --- |
| `knowledge.snapshot` | `{project_id, session_id}` → identity, compact anchor, capabilities and summary of the sources |
| `knowledge.search` | `{project_id, session_id, query, scope?, limit?, cursor?}` → `{items:[{ref, title, kind, summary, revision, provenance}], has_more, next_cursor}`; `scope` ∈ `all\|app\|capabilities\|chat\|project\|tools\|soul` |
| `knowledge.read` | `{project_id, session_id, ref, offset?, limit?}` → `{ref, title, kind, revision, content, offset, next_offset, truncated, provenance}` |
| `project.tools.list` | `{project_id, session_id, query?, limit?, cursor?}` → paginated catalog |
| `project.tools.get` | `{project_id, session_id, tool_id, revision?}` → revision with source, input/output definitions, tests and limits |
| `project.tools.history` | `{project_id, session_id, tool_id, cursor?, limit?}` → revisions |
| `project.tools.configure` | `{project_id, session_id, tool_id, expected_revision, request_id, enabled}` → enables or revokes a revision |

`knowledge.read` accepts only recognized references, never arbitrary paths or
URLs. Reads start neither inference nor programs. A project tool is a UTF-8
Python script already present in the project, registered with its expected
SHA-256 hash; the revision is immutable and includes source, description,
input/output definitions, declared tests and limits. It receives a JSON object
on stdin (also readable as `sys.stdin.buffer` or descriptor 0) and writes on
stdout a single JSON value matching `output_schema`; stderr is bounded; the
environment is the standard library with the cwd in the project, and the files
it creates use umask 022. Registering requires **Execute** and `project_write`
or higher; testing and running always require **Execute** and
`trusted_host`/`full_access`. A modified file does not replace the registered
revision.

### Identity (Soul)

All mutations require `request_id`; those on an existing profile also
`expected_revision` (compare-and-swap).

| Method | Parameters |
| --- | --- |
| `soul.list` / `soul.get` | `{offset?, limit?}` / `{soul_id}` |
| `soul.create` | `{request_id, name, core, mode?}` |
| `soul.update` | `{request_id, soul_id, expected_revision, name?, core?, mode?}` |
| `soul.preview` | `{name, core, mode?, preferences?}` → compiled capsule and exact bytes |
| `soul.snapshot` | `{project_id, session_id}` → identity selected in the chat |
| `soul.bind` | `{request_id, project_id, session_id, soul_id}`; `null` clears the selection |
| `soul.preference` | `{request_id, soul_id, expected_revision, key, value}`; `null` deletes |
| `soul.proposals` | `{soul_id, project_id?, session_id?, offset?, limit?}` |
| `soul.decide` | `{request_id, soul_id, expected_revision, proposal_id, decision}` |
| `soul.memories` | `{soul_id, project_id?, session_id?, query?, offset?, limit?}` |
| `soul.forget` | `{request_id, soul_id, expected_revision, memory_id}` |
| `soul.feedback` | `{request_id, soul_id, expected_revision, memory_id, helpful}` |
| `soul.history` / `soul.rollback` | `{soul_id, offset?, limit?}` / `{request_id, soul_id, expected_revision, target_revision}` |
| `soul.archive` | `{request_id, soul_id, expected_revision}` |
| `soul.source` | `{soul_id, source_id, project_id, session_id, offset?, limit?}` |
| `soul.export` / `soul.import` | `{soul_id}` / `{request_id, profile}` (`kira-soul-v1` format: name, core, learning mode and preferences, without lessons, sources or revisions) |

The learning modes are `manual`, `suggest` (default) and `adaptive`. Changing
a chat's identity requires an inactive project and a paused autonomous goal,
retires the provider generation while keeping its settings and closes the
chat's browser (`BUSY` while the browser is under manual control,
`BROWSER_CLEANUP` while an earlier browser cleanup is unconfirmed). The service
freezes the revision and hash of the capsule at the admission of each turn:
later changes apply from the following turn.

### Providers and accounts

| Method | Parameters → result | Rules |
| --- | --- | --- |
| `backend.discover` | `{backend, account_id?}` → `{available, models:[{id, name, capabilities, ...}], capabilities, account?, error?}` | `backend` ∈ `ollama, codex, openai_chatgpt, deepseek_account, deepseek_web, claude_code`; `account_id` only for `openai_chatgpt`. No automatic model change. |
| `backend.login` | `{backend, account_id?}` → login information | Starts the explicit login: Codex (optional), Direct OpenAI (OAuth PKCE with a local listener), DeepSeek Account. DeepSeek Web has no login flow (`INVALID_ARGUMENT`): its token is saved with `backend.account` `login`. |
| `backend.account` | `{backend, action, account_id?, ...}` → state | Direct OpenAI: `status, login, select, logout, cancel`; DeepSeek Account: `status, login, cancel, logout, configure_proxy` (`url`, `api_key`, `enabled` for DeeperSeeker); DeepSeek Web: `status, login` (with `token`), `logout`, where `login` and `logout` answer `BUSY` while DeepSeek Web work is active or queued. Credentials stay in the private profile, never in SQLite, in events or in exports. An unsafe or invalid credential store does not stop the service from starting and makes only that provider unavailable; for DeepSeek Web, `status` answers `UNSAFE_PATH` or `CREDENTIAL_STORE` until the token is saved again. |
| `backend.usage` | `{backend, account_id?}` → reported limits and local measurements | `backend` ∈ `codex, openai_chatgpt, ollama, claude_code`; read-only, no inference and no billing changes; at most 10,000 recent usage events examined. |
| `settings.get` / `settings.update` | `{}` / `{ollama_url?, codex_binary?, tool_timeout?, max_output_bytes?, max_tool_rounds?}` → `{settings}` | Rejected with `BUSY` while there is active work or reservations. No automatic endpoint or model change. |
| `system.resources` | `{}` → `{memory, gpus:[], platform}` | Unknown values are `null`. |

Direct login states: `idle, awaiting_browser, exchanging, completed, failed,
cancelled`; a pending state blocks new direct admissions. Every registration
has a stable local ID bound to new chats in `settings.openai_account_id`;
existing chats keep using their own account.

### Attachments

`attachment.import {path}` → `{attachment:{id, name, mime, width, height,
bytes, sha256, preview_url}}`. The service normalizes the image into a private
PNG under `image-attachments/<id>.png` (orientation applied, colors converted
to sRGB, metadata removed), without modifying the source and without
contacting any provider. The ordered IDs then go into `run.start.attachments`
or `queue.add.attachments`; only the explicit send transmits the bytes to the
provider. Formats: PNG, JPEG, WebP and static GIF; animated files are
rejected.

### Workspace and native actions

| Method | Parameters → result | Rules |
| --- | --- | --- |
| `workspace.get` | `{project_id}` → `{project_id, tools, next_step, resume:{last_run, active_count, interrupted_count}, watches, captures}` | `tools` ⊆ `folder, terminal, editor`; a watch has `path, sha256, current_sha256, state` (`unchanged, changed, missing, unavailable`), `checked_at`. |
| `workspace.update` | `{project_id, tools?, next_step?}` → workspace | Per-project choices; `next_step` is maintained by the user. |
| `workspace.watch` / `.unwatch` | `{project_id, path}` → workspace | Regular files contained in the project only, no symlinks or FIFOs; `watch` creates or replaces the reference hash. |
| `desktop.actions` | `{project_id?, query?}` → `{actions:[{id, label, description, available, reason, requires_project}]}` | Fixed IDs: `project.folder, project.terminal, project.editor, browser.open, browser.search, screenshot.capture, system.settings, system.monitor, system.updates, system.backup`. |
| `desktop.launch` | `{action_id, project_id?, request_id, query?}` → `{request_id, action_id, status, message, artifact?}` | `status` ∈ `requested, unavailable, failed, completed, unknown`. `requested` does not prove that a window appeared; `completed` only for a PNG capture verified by hash. |
| `workspace.launch` | `{project_id, request_id}` → `{request_id, results:[...]}` | Opens the saved tools with the same idempotency rules. |
| `system.diagnostics` | `{}` → restricted report | Version, platform and session type, active runs, availability of native actions; never project contents, credentials, account IDs or environment dumps. |
| `diagnostics.export` | `{}` → `{path}` | Saves the same report in `diagnostics/`. |

Native applications receive a cleaned environment and fixed argv; they start
in a scope separate from the service and stay open when Kira closes.

### Maintenance

| Method | Parameters → result | Rules |
| --- | --- | --- |
| `database.backup` | `{}` → `{path}` | Consistent copy through the SQLite backup API. |
| `service.shutdown` | `{}` → `{stopping:true, pending_cleanup_projects?, message?}` | Only with no active work (runs, Teams, automatic continuations, context maintenance, chat branches or compactions) and no browser under manual control; otherwise `BUSY`, naming it. Unconfirmed provider or browser cleanup does not block the stop: the reply lists the affected projects in `pending_cleanup_projects`, and the next start checks them again. |

## 4. Events

### Envelope

```json
{"schema_version": 1, "run_id": "…", "seq": 12, "type": "tool.completed",
 "timestamp": "2026-01-01T12:00:00Z", "payload": {}}
```

`seq` starts at 1 and increases per run. State and event are written in the
same transaction before publication: a client reading `run.events` sees
exactly what the GUI received live. Events never expire and are never deleted
implicitly.

### Standard types

| Type | Payload | Notes |
| --- | --- | --- |
| `run.state` | `{state, error?, outcome?}` | The `running` event also records `backend, model, reasoning_effort` and the run's derived metadata. |
| `message.user` | `{content, attachments?:[metadata], source?}` | `source:"steer"` for a consumed batch of guidance. |
| `message.delta` | `{content, item_id?}` | Incremental assistant text. |
| `message.completed` | `{content?, item_id?}` | Finished message; snapshots and exports keep `item_id` and `complete`. |
| `context.supplied` | `{content}` | Context supplied by Kira (memory, excerpts, notes, goal), inspectable. |
| `context.status` / `context.compaction` | public metrics and phases | Whitelisted fields only; never the encrypted native state. |
| `tool.started` | `{tool_id, name, arguments}` | |
| `tool.progress` | `{tool_id, name, phase, reason}` | Used with `phase:waiting_for_browser_control\|resuming` and `reason:manual_control`; it is neither an execution nor a question. |
| `tool.completed` | `{tool_id, name, result}` | `result` follows `{ok:bool, ...}`. |
| `artifact.created` | `{path, kind, sha256?, tool_id?}` | Bounded outputs, screenshots, captures. |
| `usage` | `{source, input_tokens?, output_tokens?, ...}` | Absent fields remain unknown; `scope:"context_compaction"` for context maintenance; `measurement:"plan_limits"` for Claude Code plan warnings. |
| `session.updated` | `{backend_ref}` | The provider obtained a native reference. |
| `approval.requested` | `{approval_id, ...}` | For browser actions recorded by the service it includes `allow_always:true`. |
| `backend.notice` | `{message, ...}` | Notices from the service or the provider; also opens `goal_continuation` and `team_followup` runs, and records `accept_always` decisions. |
| `input.requested` / `input.answered` / `input.received` / `input.delivered` / `input.closed` / `input.delivery_failed` | form, answer, recovery, delivery | `input.delivered.transport` ∈ `turn/steer`, `json-rpc-response`, `responses-http`, `ollama-chat`, `claude-code-mcp`; delivery does not prove that the model used the answer. |
| `steering.requested` / `.submitted` / `.accepted` / `.failed` | identity and delivery state | The result reports `queued, submitted, provider_accepted, not_delivered, unknown`. |
| `team.delegation` | `{delegation_id, team_id, goal, state, source:"kira_service", summary?, error?, counts}` | Coalesced progress of the delegation on the originating run. |
| `goal.state` | public state of the autonomous goal | Emitted on the goal's latest run. |
| `reasoning.summary.delta` / `.completed` / `.status` | `{item_id, provider, source:"provider_summary", content?}` / `{..., status, reason?}` | Public summary offered by the provider, or the reasoning that DeepSeek Web shows on its site; 64 parts, 65,536 bytes and 256 ID characters per run. |

General rules: no event contains the model's private reasoning; DeepSeek
Account's internal thinking blocks are discarded, the reasoning that DeepSeek Web
shows on its site reaches only the summary channel and never the answer, and
public summaries are a separate channel. `tool.*` events coming from Codex or Claude Code are **observations**
of their native tools: Kira does not execute them.

### Team events

Teams have an independent stream, delivered as `team_event` with the same kind
of envelope (`schema_version, team_id, seq, type, timestamp, payload`). Types:
`team.state`, `team.plan`, `team.plan.feedback`, `step.state`, `member.state`,
`member.activity` (reference to the run and the sequence, without duplicating
the stream), `team.message`, `team.message.supplied` (`member_id, run_id,
message_ids, via:"read_messages"|"assignment_context"`, plus the provider's
`call_id`), `team.approval`, `team.result`, `team.followup` (receipt of the
automatic reply, for Teams with an originating chat). The detailed tool events
stay in each member's ordinary run.

### `browser_event`

Transient, non-durable envelope: `{type, project_id, session_id, browser_id,
generation, seq, payload}`. It carries the actor state and the view frames
(`surface_id`, `surface_mode` ∈ `page|native`, dimensions, authentication
state). Frames are coalesced to the latest, limited to 10 fps and 500 KiB
encoded, reach only subscribed clients and never enter SQLite, the logs, the
conversation or the model context. A frame that would exceed 1 MiB is
discarded.

### Streaming sanitization

Before durable publication, the service keeps bounded state per run and per
item: 64 open items, identifiers up to 256 characters and 65,536 aggregated
pending characters. A credential name (`TOKEN`, `GITHUB_TOKEN`, `password`,
`api_key`, `secret`, `Authorization`, `Cookie`) followed by `:` or `=` is
recognized even when the remainder is not yet available: a quoted value is
redacted at any length, an unquoted one from eight characters (`token: 5`
stays visible). Registered provider credentials are redacted wherever they
appear. A terminal outcome sanitizes the remaining queues. It is not a
universal detector of unknown secrets.

## 5. Provider interface

The shared definitions are in `src/kira/domain/contracts.py`. Each provider
lives in `src/kira/backends/` (`ollama.py`, `codex.py`, `openai_chatgpt.py`,
`deepseek_account.py`, `deepseek_web.py`, `claude_code.py`).

### `Backend`

```python
class Backend(Protocol):
    async def discover(self) -> Json: ...
    async def run(self, ctx: RunContext) -> Json: ...
    async def close(self) -> None: ...
    # optional: async def login(self) -> Json
```

Reference constructors: `OllamaBackend(endpoint="http://127.0.0.1:11434")`
(the default is `ollama_url` from the settings) and
`CodexBackend(binary="codex")`. Direct OpenAI and DeepSeek receive the
service's account manager; Claude Code receives the private runtime folder,
where it writes its own launcher, and receives Kira's tools from an MCP server
inside the service process.

### `RunContext`

| Field | Type | Use |
| --- | --- | --- |
| `run_id`, `project`, `session`, `prompt` | data | Identity and input of the turn; `ctx.root`, `ctx.model`, `ctx.settings`, `ctx.permission` are convenience properties. |
| `supplied_context` | `str` | Memory, instructions, excerpts and notes that Kira supplies; Codex appends it to every turn as Kira context. |
| `assistant_instructions` | `str` | Kira's common identity, present even with tools disabled. |
| `history` | `list[Json]` | Portable messages with role and content; Ollama may keep the full message and tool fields in the `completed` payload. |
| `image_inputs` | `dict` | Images attached per user turn, for providers that accept them. |
| `mode` | `str` | `execute`, `plan`, `review`, `recap`. |
| `emit(type, payload)` | callback | Publishes a durable event. |
| `execute_tool(name, args)` | callback | Executes a base tool: the service handles permissions, events, IDs and artifacts. |
| `approve(request)` | callback | Stores the request and waits for the correlated decision from the GUI. |
| `cancel` | `asyncio.Event` | Cancellation request; after a grace period the runtime cancels the task. |
| `request_input(form)` / `read_input(request_id, wait)` | callback | Persistent questions to the user (see `questions.*` in §6). |
| `client_tools` / `client_tool_call(call_id, name, args)` | list and callback | Kira tools exposed to the model in the provider's dynamic format; the caller's identity is bound to the context, never to the arguments. |
| `output_schema` | `Json \| None` | Structured output (coordinator of a Team): the final text comes back in `outcome.final_text`. |
| `disable_native_agents` | `bool` | Turns off native subagents when Kira supplies its own tools. |
| `provider_started(pid)` | callback | Registers the child process before inference, for cleanup and recovery. |
| `register_control(handler)` | callback | Registers native control operations (Codex steering, compaction). |
| `guidance(kind, payload)` | callback | Boundary for user guidance in providers with a service-managed inbox. |
| `context_state`, `context_save`, `context_page`, `context_catalog` | state and callbacks | Durable context managed by Kira (Direct OpenAI, DeepSeek Account). |
| `tool_image(ref)` | callback | Retrieves the pixels of a screenshot owned by the service. |

### Result of `run`

`{state:"completed"|"cancelled"|"failed"|"interrupted", outcome?:object,
error?:string}`. An exception is a failure; losing the terminal confirmation
is `interrupted`. The provider emits `session.updated` when it obtains a
native reference and must never change model or account on its own.

### Discovery and capabilities

`discover()` returns `{available, models:[{id, name, capabilities, ...}],
capabilities, account?, error?}`. The capability keys are `streaming, tools,
cancellation, resume, usage, images, context_controls`, plus `steering,
planning, native_fork, native_compaction` where relevant. An unverified
capability is `false` or clearly marked as metadata. The catalog keeps
`context_window`, `max_context_window` and `max_output_tokens` separate; a
listed model does not prove that the account is entitled to use it. With
Ollama and DeepSeek Account, Kira's tools are offered only if the model passes
a side-effect-free check on first use, whose outcome applies to that model on
that endpoint for as long as the service keeps running; screenshots and
Computer only if the model declares image input (with Ollama, through
`/api/show`).

### Base tools

| Tool | Arguments | Rules |
| --- | --- | --- |
| `list_files` | `{path?:"."}` | Contained in the root (or outside it, with Full access). Kinds `file`, `directory`, `symlink`, `special`; a name that is not valid UTF-8 is shown with `\xNN` escapes and `undecodable:true` and cannot be addressed by the file tools. |
| `search_files` | `{query, path?:"."}` | Reports its own limits: 1,000 entries, 100 matches, depth 32, 16 MiB examined; it does not enter `.git`, `.venv` or `node_modules`. Files over the size limit and unreadable or vanished files or folders are skipped, counted (`skipped_large`, `skipped_unreadable`, the first 20 in `skipped`) and set `truncated`. |
| `read_file` | `{path}` | Returns content and `sha256`. A file that is not valid UTF-8 is returned with replacement characters and `lossy:true` (`encoding:"non_utf8"`); devices, FIFOs and sockets are refused without being opened. |
| `write_file` | `{path, content, expected_sha256?:string\|null}` | Requires the expected hash of the existing content; `null` means "create only" and also creates the missing parent folders (listed in `created_directories`). Refuses with `UNSUPPORTED` to replace a file that is not valid UTF-8. New files 0644, new folders 0755. Requires a write permission; paths inside `.git` require **Full access**. |
| `run_command` | `{argv:string[], timeout?:number}` | Explicit `argv`, cwd in the root, no implicit shell; requires `trusted_host` or `full_access`. `timeout` can only shorten **Tool timeout (seconds)**. The command receives only `PATH`, `HOME`, `LANG`, `LC_ALL`, `LC_CTYPE`, `TZ`, `TMPDIR`, `USER`, `LOGNAME` (plus `PYTHONUNBUFFERED=1`) and umask 022. Process groups belong to Kira: timeouts and cancellation terminate descendants with bounded escalation; a process that leaves the group while keeping the output open is reported with `detached_pipe_holder:true`. |

Results are `{ok:bool, ...}`; a rejected input produces a structured
`{ok:false, error:{code, message}}`. An unexpected failure is `TOOL_ERROR`, a
time limit `TIMEOUT`; every `tool.started` is followed by `tool.completed`.

### Default limits

| Setting | Default value |
| --- | --- |
| `tool_timeout` | 120 seconds |
| `max_output_bytes` | 1 MiB captured per command |
| `max_tool_rounds` | 0 = unlimited |
| File search | 1,000 entries, 100 matches, depth 32, 16 MiB |

Truncations and stops for lack of progress produce visible events.

### Permissions → policy per provider

**Codex** (App Server sandbox, `approvalPolicy:"never"`,
`approvalsReviewer:"user"`; the service verifies that the returned sandbox
matches the requested one):

| Permission | Sandbox | Details |
| --- | --- | --- |
| `read_only` | `read-only` | `networkAccess:false` |
| `project_write` | `workspace-write` | `writableRoots:[root]`, `networkAccess:false`, `/tmp` and `$TMPDIR` excluded |
| `trusted_host` | `danger-full-access` | no sandbox |
| `full_access` | `danger-full-access` | no sandbox |

Plan, Review and Recap always require `read-only`.

**Direct OpenAI, Ollama, DeepSeek Account, DeepSeek Web**: tools are executed
by the service through `execute_tool`, so the policy is an allowlist. In
read-only mode the model receives `list_files`, `search_files`, `read_file`;
with a write permission `write_file` is added; with `trusted_host` or
`full_access` `run_command` is added. The service re-checks the permission on
every call: a name outside the allowlist is rejected even if the model invokes
it. Browser and Computer actions that modify a page additionally require
**Execute**, a writable project and the user's consent (single action or the
chat's `always` policy); DeepSeek Web does not receive Computer.

**Claude Code · personal use**: Kira starts the unmodified Claude Code binary
with `permission_mode:"dontAsk"` and an `allowed_tools` list derived from the
permission: `Read, Glob, Grep` in read-only mode; `Edit, Write, NotebookEdit`
with a write permission; `Bash, WebFetch, WebSearch` with `trusted_host` or
`full_access`; plus Kira's MCP tools (`mcp__kira__*`). A `PreToolUse` hook
denies every other tool and every path outside the project before execution
(except with Full access). The execution of the built-ins belongs to Claude
Code and is only observed; Kira neither reads nor stores its credentials.

## 6. Namespaces of the tools exposed to the model

Kira's tools are "client tools": the model calls them, the service executes
them with the run's identity (project, chat, permission, provider, account),
which no argument can change. Recognized namespaces: `team`, `kira`,
`kira_history`, `browser`, `computer`, `goal`, plus `questions` for questions.

| Namespace and tool | Arguments | Notes |
| --- | --- | --- |
| `kira.environment` | `{}` | Current capabilities, scope and state reported by the service. |
| `kira.team_delegate` | `{goal, brief, mode, members?, max_agents?, concurrency?, round_limit?}` | Only in **Execute** with tools enabled and a provider that runs Teams (`codex`, `openai_chatgpt`, `ollama`, `deepseek_account`); one accepted delegation per originating run; the Team starts after the turn completes and is cleaned up. |
| `kira.team_status` | `{delegation_id?}` | Without an ID: lightweight recent summary. |
| `kira.team_message` | `{delegation_id, recipient_id, content}` | Sender `assistant`; does not restart a finished Team. |
| `kira.team_resume` | `{delegation_id, instruction?, round_limit?}` | Same providers as `kira.team_delegate`; continuation queued after the turn; source Team finished and cleaned up. |
| `kira.help_search` | `{query, scope?, limit?, cursor?}` | `scope` ∈ `all\|app\|capabilities\|chat\|project\|tools\|soul`. |
| `kira.help_read` | `{ref, offset?, limit?}` | Recognized references only. |
| `kira.project_tools` | `{action, payload}` | `action` ∈ `describe\|search\|read\|register\|test\|run`. |
| `kira.research_list` / `research_read` / `research_propose` | list, `{record_id, source_index?, offset?, limit?}`, record content | Only with Research sharing enabled; accepted records only; a proposal can neither accept nor assess itself. |
| `kira.soul_propose` | `{statement, applicability, source_run_id, source_seq, kind?, scope?, key?, value?}` | Only with a Soul in `suggest` or `adaptive` mode; it only proposes. |
| `goal.status` | `{}` | Autonomous goal, checkpoints and recent references. |
| `goal.checkpoint` | `{status:"continue"\|"completed"\|"blocked", summary, next_step, deliverable?}` and `evidence:[{run_id, seq}]` (required) | Only with a writable lane; `summary` ≤ 6,000, `next_step` ≤ 3,000, `deliverable` ≤ 12,000 characters, at most 24 references to recorded events (taken from `goal.status`); completion applies after the successful turn. |
| `team.send_message` | `{recipient_id, content}` | Team members; ≤ 8,000 bytes; starts nobody. |
| `team.read_messages` | `{after_seq?:0, limit?:20}` | Pages of up to 50 messages within 48,000 bytes (the first message is always delivered); includes scheduling facts. |
| `kira_history.search` / `kira_history.read` | `{query, limit?, before_id?}` / `{source_id, offset?, limit?}` | Original archive of the current chat; results ≤ 32,000 bytes, reads ≤ 32,000 characters. |
| `questions.request_input` / `questions.read_input` | `{blocking, questions:[1–3]}` / `{request_id, wait}` | Persistent questions; a pending answer is not an approval. |
| `browser.search`, `navigate`, `snapshot`, `find`, `click`, `type`, `press`, `scroll`, `screenshot`, `tabs`, `select_tab`, `close_tab`, `back`, `close`, `manual_login` | see `src/kira/domain/web.py` | Only the run's browser actor; `click/type/press` require consent; no arbitrary JavaScript or selectors. |
| `computer.observe` / `computer.act` | `{}` / `{observation_id, action, ...}` | `action` ∈ `click, double_click, move, scroll, type, key, drag, wait`; every action consumes the observation and returns a new one; 32 cycles per run. |

Wire names: Direct OpenAI uses `kira_browser.*` and `kira_computer.*` (because
`browser` and `computer` are namespaces reserved by the provider); Ollama,
DeepSeek Account and DeepSeek Web receive flat `<namespace>__<tool>`
functions, such as `browser__navigate` and `computer__act` (DeepSeek Web
without Computer); Claude Code sees `mcp__kira__browser__*` and
`mcp__kira__computer__*`; Codex receives, on the `kira` MCP server, the
`kira.*` tools under their own names (for example `help_search`) and the goal
tools as `goal__status` and `goal__checkpoint`. Durable events always use
`browser.*` and `computer.*`. Only the typed definitions and the identity
fields of the calls are translated; user text, results and opaque provider
state stay unchanged.

Common limits: 64 KiB per definition, arguments and result of every call; a
deduplication cache of 2,048 calls and 8 MiB per run; 60 s timeout per call,
except browser, Computer and project tools, which use their own deadlines.
Codex receives these tools through a private per-run MCP relay (`0700` folder,
`0600` descriptor and socket, opaque capability kept out of prompt,
configuration and events, peer UID verification).

## 7. Numeric limits

| Area | Limit |
| --- | --- |
| IPC frame | 1 MiB; outgoing queue of 128 messages per client |
| Prompt and generic strings | 200,000 encoded bytes |
| Project and session snapshots, `session.list` and `input.list` pages | 700 KB |
| `project.changes`, Team and assistant snapshots | 650 KB |
| Event replay | `run.events` 250 (max 500); `team.events` max 250 |
| Queue | 64 unfinished items per project, 8,000 bytes each |
| Guidance (`run.steer`) | 32,000 bytes per message; 128 per run; consumed batch ≤ 32 messages / 256 KiB |
| Questions | 16 requests per run, 1–3 questions, 6 choices, 64 KiB per form, 16 KiB of answers, ID `[A-Za-z0-9_.:-]{1,128}` |
| Teams | 1–12 members, concurrency 1–8, rounds 0–10,000 (3), 24 tasks per plan, goal 64,000 bytes, name 100, role 400, instructions 8,000, message 8,000 bytes, 50 messages and 48,000 bytes per page, 64 approvals per page, 32 continuation links |
| Autonomous goals | `max_cycles` 0 = unlimited; checkpoint 6,000/3,000/12,000 characters, 24 references |
| Research | title 200, body 8,000, scope/criteria/limitations 2,000, procedure 4,000 characters; 8 sources, 8 dependencies, 8 links; 48 KiB per record; list 20 (max 30); source pages 16,000 characters; dependencies read up to 4 MiB |
| Soul | name 96 bytes, core 1,200, preference 160, capsule 3,072; 512 active lessons and 512 proposals per identity; score −2…2 |
| Knowledge and history | `kira_history` results 32,000 bytes, reads 32,000 characters, internal page 8 MiB |
| Client tools | 64 KiB per definition/arguments/result; 2,048 calls and 8 MiB of cache; 60 s (except browser, Computer and project tools) |
| Browser | 2 workers, 4 tabs per worker, 8 embedded frames, 80 element references; screenshots up to 1600×1200, 2 MiB, 16 per run; 2 recent images in context; view frames 10 fps / 500 KiB; 128 input receipts; 32 Computer cycles per run, drag 2–32 points, wait ≤ 1,500 ms; HTTP/HTTPS only on 80/443 through a local proxy; 32 simultaneous connections and 32 MiB per connection; 3,000 requests and 128 MiB per budget, which restarts with every requested navigation and otherwise every five minutes; connections closed after 60 s without traffic |
| Images | 4 per message, 32 MiB and 32 million pixels per source, 8 MiB per PNG, 16 MiB per message; direct request ≤ 32 MiB |
| File references | 64 files and 16,384 path characters per import |
| GUI drafts (`composer.json`) | 2 MiB, 100 drafts, 262,144 bytes per entry, 200 sent messages in history; `ui.json` 16 KiB, 32 projects with last chat |
| Cross-chat context | 5 chats, 400 request and 800 response characters per source, 6,000 total |
| Ollama context | 8 recent runs, 32,000 bytes; with explicit `num_ctx` 2× the value within 4,096–64,000 bytes |
| Direct context | reserve max(18 % of the window, `max_output_tokens` + 3 % of the window) when `max_output_tokens` is reported and smaller than the window, otherwise 18 %; DeepSeek: threshold min(80 % of window, window − min(output + 65,536, 50 % of window)), most recent 16 % verbatim, output up to 65,536 tokens and at most half of the window, summaries 8,192 |
| Reasoning summaries | 64 parts, 65,536 bytes, 256-character IDs |
| Stream sanitization | 64 open items, 256-character IDs, 65,536 pending characters |
| Assistant Markdown | transformed only in the GUI; no remote resource loaded by the rendering; local links only with an explicit click and verification of the current project |

## 8. Compatibility

- **GUI and service must have the same version.** `hello` returns
  `app_version`; the GUI rejects a different service before sending any
  command. To upgrade, follow the single procedure in
  [Installation](INSTALLATION.md): finish the work, pause the queue, close the
  browser, make the backup, close the GUI, stop the service with
  `kira-desktop stop-service`, upgrade, restart.
- **The database upgrades itself.** On startup over an older database the
  service first creates a consistent private backup and then applies the
  changes in a single transaction, without rewriting or deleting the existing
  rows: projects, chats, runs, events, artifacts and settings stay intact.
- **An older version refuses a newer database.** The service does not open a
  database upgraded by a later version: to go back you need the compatible
  backup made before the upgrade.
- The package scripts never terminate work in progress and never touch the
  user's data; the upgrade waits for the verified idle state.
- The GUI preferences (`ui.json`, `composer.json`) are private configuration
  separate from the authoritative database; changing them sends no IPC
  requests and modifies no permissions or provider settings.
