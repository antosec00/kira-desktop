# Kira Desktop contracts

Integration owner: coordinator. Contract edits are serialized through the coordinator.

Desktop 0.15.1 uses database schema 14 with IPC/event envelopes 1.
[Personalized identities](KIRA_SOULS.md) governs user-level profiles, immutable
run snapshots, scoped source-linked learning, review and identity rebinding.
The additive migration leaves existing chats unselected and existing goals unchanged.
[Public reasoning summaries](REASONING_SUMMARIES.md) defines the optional
provider display channel; private reasoning remains opaque and goal/answer/tool
evidence semantics are unchanged.
[Autonomous goals](AUTONOMOUS_GOALS.md) governs explicitly armed multi-cycle
objectives, durable checkpoints, service provenance and recovery. The migration
is additive and never starts an existing manual goal.
[Direct context](DIRECT_CONTEXT.md) defines the narrow no-tool compaction
recovery path; ordinary inference validation and replay policy remain strict.
[Chat layout](CHAT_LAYOUT.md) defines the visual status mappings and motion
boundaries; those retained visual refinements do not change execution permissions.
[Full access](FULL_ACCESS.md) defines the fourth permission and the targeted
run_lanes constraint migration, plus corrected live browser consent handling. Schema 11 adds
`run_lanes` and `assistant_followups` without rewriting existing tables.
[Live conversation](LIVE_CONVERSATION.md) governs guidance delivery, concurrent
read-only chat and durable Team result continuation; it supersedes older blanket
standalone-run exclusions and passive-only Team result descriptions below.
The [identity and project tools contract](KIRA_IDENTITY.md) defines common
instructions, bounded knowledge retrieval, immutable project tool revisions and
run-owned execution. Schema 10 adds six tables without modifying previous rows.
The [research registry contract](RESEARCH_REGISTRY.md) governs the first project
records/evidence increment. Upgrading a schema-9 profile is additive after a
consistent private backup; earlier binaries require a compatible backup for rollback. The
[workbench contract](WORKBENCH.md) governs additive chat controls, run modes,
durable queues, steering, goals and native thread operations. Historical feature
sections below retain the schema/version at which those features first shipped.

## Compact chat layout (Desktop 0.8.5)

[Chat layout](CHAT_LAYOUT.md) defines the presentation-only activity row,
dismissible workspace hint and consecutive tool groups. The private boolean
`resume_hint_visible` defaults to true and changes only through explicit GUI
preference actions. Hiding it does not remove the saved workspace next step.
Elapsed request time and details belong to the selected chat's recorded run;
they do not predict completion or expose private reasoning. Tool grouping
preserves source row identity, ordering, body/raw data, copy/export semantics
and existing bounds. Activity/evidence views retain individual entries.
Provider/model/effort, service execution, approvals, cancellation confirmation,
schema 8 and IPC 1 are unchanged.

## Process and data

Native targets: Python 3.12 / Ubuntu 24.04 amd64 and Python 3.14 / Ubuntu 26.04
amd64; separate packages share this protocol and schema. See
[platform boundaries](PLATFORM_TARGETS.md).

GUI -> private Unix socket -> asyncio service -> providers. Only the service writes SQLite. The GUI never executes tools. Dictionaries crossing boundaries are JSON. IDs are opaque UUID strings; UTC timestamps are ISO 8601. Project fields: `id,name,root,instructions,permission,created_at,updated_at,memory`. Permissions: `read_only`, `project_write` (Kira file tools only; Codex workspace sandbox), `trusted_host` (arbitrary commands as the user, explicitly NOT a sandbox),
`full_access` (file tools also accept paths outside the project, subject to the
current OS user’s permissions; no automatic administrator access). Session fields: `id,project_id,backend,model,settings,backend_ref,created_at`. Ordinary chats can switch provider with `session.switch` while retaining the session identity; provider references belong to separate generations. See [provider continuity](PROVIDER_CONTINUITY.md). Run fields: `id,session_id,request_id,prompt,state,created_at,updated_at,error,outcome,last_seq`. At most one active run per project.

States: queued -> running -> [awaiting_input -> running] -> completed/failed; active -> cancelling -> cancelled or interrupted; any unfinished run found after service restart -> interrupted, never automatically replayed. Cancel requested is not confirmed cancellation. `completed` only follows a provider's successful terminal outcome.

## IPC v1

Newline-delimited UTF-8 JSON. Frame <= 1 MiB. Request `{version:1,id:string,method:string,params:object}`. Response `{version:1,id,result:object}` or `{version:1,id,error:{code,message}}`. Unsolicited notification `{version:1,event:Event}`. Mismatched version is rejected before mutation. Client request IDs correlate RPC; `run.start.params.request_id` is durable idempotency and MUST survive retries.

Methods and params:

- `hello {}` -> `{version,app_version,capabilities}`
- `project.list {}` -> `{projects:[]}`
- `project.create {name,root,instructions?,permission?}` -> `{project}`
- `project.update {project_id,name?,instructions?,permission?,memory?}` -> `{project}`; reject permission changes during active work.
- `session.create {project_id,backend,model,settings?,handoff?,soul_id?}` -> `{session}`
- `project.snapshot {project_id}` -> `{project,sessions:[],runs:[],artifacts:[],messages:[]}`; each run includes `last_seq`; messages contain `run_id,role,content`.
  Snapshots bound display data to 700 KB; previews and omitted history are explicitly flagged. `run.events` retrieves committed events, and `project.export` retains the full persisted transcript and project metadata.
- `run.start {session_id,prompt,request_id,mode?,attachments?:[id]}` -> `{run}`
- `run.cancel {run_id}` -> `{run}`
- `run.subscribe {run_id,after_seq:integer}` -> `{events:[],last_seq,has_more?}` AND registers live delivery atomically relative to emission. Bounded replay with `has_more` may require another subscribe. Client deduplicates `(run_id,seq)`.
- `run.events {run_id,after_seq?:integer,limit?:integer}` -> `{events:[],last_seq,has_more}`
- `run.approve {run_id,approval_id,decision:string}` -> `{accepted:boolean}`
- `input.list {project_id,offset?,limit?}` / `input.answer {run_id,request_id,answers}`
  expose persistent information questions separately from permissions. See
  [question contracts](INPUT_QUESTIONS.md#ipc-and-events) for full forms,
  pagination, delivery evidence, bounds and recovery.
- `backend.discover {backend:ollama|codex|openai_chatgpt,account_id?}` -> provider catalog.
- `backend.usage {backend:codex|openai_chatgpt|ollama,account_id?}` reads reported account limits or attributed local measurements. It does not run a model or change billing. See [Account usage](ACCOUNT_USAGE.md).
- `backend.login {backend:codex|openai_chatgpt,account_id?}` -> official login information. Direct account operations and private credential storage are defined in [OpenAI direct](OPENAI_DIRECT.md); auth payloads never enter application events or SQLite.
- `settings.get {}` / `settings.update {ollama_url?,codex_binary?,tool_timeout?,max_output_bytes?,max_tool_rounds?}` -> `{settings}`; no automatic endpoint/model changes or weight downloads.
- `system.resources {}` -> `{memory:object,gpus:[],platform:object}`; unknown values are null.
- `project.export {project_id}` -> `{text:string}` for small exports, otherwise `{path,format:'json',bytes}` pointing to a complete private export under the service data directory. Client copies it asynchronously to the chosen file-dialog destination; no transcript truncation.
- `database.backup {}` -> `{path}` using SQLite backup API.
- `service.shutdown {}` -> `{stopping:true}` only when no active runs; otherwise BUSY.

All requests validate types, required fields and permissions in the service; no executable strings in GUI messages. Socket parent 0700, socket 0600; single-owner process lock. Slow consumers are disconnected and recover by replay, never allowed to block durable writes. History persists until user explicitly removes their data; no hidden expiry. Stream output is bounded per run with full bounded output artifacts where appropriate.

## Events

`{schema_version:1,run_id,seq,type,timestamp,payload}`; per-run seq starts at 1. Persist state + event transactionally before publication. Standard types: `run.state {state,error?,outcome?}`, `message.user {content,attachments?:[metadata]}`, `message.delta {content,item_id?}`, `message.completed {content?,item_id?}`, `context.supplied {content}`, `tool.started {tool_id,name,arguments}`, `tool.progress {tool_id,name,phase,reason}`, `tool.completed {tool_id,name,result}`, `artifact.created {path,kind,sha256?,tool_id?}`, `usage {source,input_tokens?,output_tokens?,...}`, `session.updated {backend_ref}`, `approval.requested {approval_id,...}`, `backend.notice {message,...}`. Fields absent in provider metrics stay unknown. Never expose private reasoning content. Tool events from Codex are observations; Kira must not execute them.

Streaming sanitation retains bounded state per run and item before durable
publication. A run permits 64 open items, 256-character item identifiers and
65,536 aggregate pending characters. Usage/notices and GUI disconnection do not
flush or reset that state. Completion/terminal outcomes sanitize pending tails;
known sensitive prefixes may be conservatively obscured when their continuation
is unavailable. Recognized inline assignments are obscured even for short values.
This is not universal detection of unknown secrets. Snapshot/export assistant
messages retain optional `item_id` and `complete` fields so concurrent messages
and interrupted partial content can be reconstructed independently.

## Providers

Shared executable definitions: `src/kira/domain/contracts.py`. Implement `OllamaBackend` in `backends/ollama.py`, constructor `(endpoint: str = 'http://127.0.0.1:11434')`; `CodexBackend` in `backends/codex.py`, constructor `(binary: str = 'codex')`. Each implements `discover()->dict`, `run(RunContext)->dict`, `close()`. Optional Codex `login()->dict`. Run returns `{state:'completed'|'cancelled'|'failed'|'interrupted',outcome?:object,error?:string}`; exceptions are failures, loss of confirmation is interrupted. Cancellation uses `ctx.cancel`; runtime also cancels task after a bounded grace period.

Discovery `{available:bool,models:[{id,name,capabilities:object,...}],capabilities:object,account?:object,error?:string}`. Capability keys: `streaming,tools,cancellation,resume,usage,images,context_controls`; unverified tools are false or clearly marked metadata-only. No automatic model switch. `ctx.session.settings` contains explicit `reasoning_effort`, `num_ctx`, `tools` etc.; `ctx.session.backend_ref` belongs ONLY to its provider. Providers emit `session.updated` when a ref is obtained. `ctx.history` is portable messages with roles/content; Ollama provider may persist full message/tool fields in completed event payload. `ctx.supplied_context` is inspectable memory/instructions; Codex should append this to each turn as Kira-supplied context.

Runtime supplies `ctx.execute_tool(name,args)` (handles event persistence, IDs, permissions and artifacts), `ctx.emit(type,payload)`, and `ctx.approve(request)` (persists request and waits for a correlated GUI decision). Tool names: `list_files {path?:'.'}`, `search_files {query,path?:'.'}`, `read_file {path}`, `write_file {path,content,expected_sha256?:string|null}` (null means create-only), `run_command {argv:string[],timeout?:number}`. Results `{ok:bool,...}`; structured `{ok:false,error:{code,message}}` on rejected input. Reading provides sha256; writes require expected hash for existing content; exec has explicit argv, cwd=root, no implicit shell. Process groups belong to Kira; timeout/cancel terminate descendants with bounded escalation.

## Compatibility

Desktop 0.3.1 adds persistent question events `input.requested`, `input.answered`,
`input.received`, `input.delivered`, `input.closed`, `input.delivery_failed` without
changing database schema 3. Optional RunContext callbacks `request_input(Json)`
and `read_input(request_id,wait)` keep question persistence in the service. The
provider confirms delivery separately; retrieval is not evidence of model use.
The 0.3.1 application handshake requires the matching service version.

Default resource controls: tool timeout 120 seconds, output capture 1 MiB, optional tool-round ceiling 0 (unlimited). Limits are configurable; truncation and no-progress stops produce visible events. File search reports its bounds (1,000 entries, 100 matches, depth 32 and 16 MiB scanned). No automatic event expiry or implicit deletion occurs; artifacts consume local user storage until deliberately removed.

IPC major and event envelope schema are 1. Desktop 0.3 uses database schema 3;
schema-1 and schema-2 databases are backed up privately before an additive
transactional migration.
Existing project/session/run/event/artifact/settings rows remain intact. New
tables hold workspaces, file references, desktop request identities and captures.
Schema 3 adds the project-team entities specified in [V03_TEAMS.md](V03_TEAMS.md).
Application version is checked by hello before commands. Older services refuse
newer database schemas; rollback requires the retained compatible database.
Upgrade must wait for active work to finish and explicitly stop the old service,
then relaunch. Package scripts never terminate active work or touch user data.

## Project teams (Desktop 0.3)

[V03_TEAMS.md](V03_TEAMS.md) defines `team.*` IPC, the independent sequenced
`team_event` envelope, coordinator output, mailbox tools, reservations and
recovery. `domain/teams.py` provides the shared limits and provider schemas.
Ordinary run events retain their shape; team members link to actual runs.
Provider mailbox callbacks are service-bound and distinct from observed native
Codex tools. The service controls scheduling; the UI is not an executor.

## Workspaces and explicit desktop actions

The v0.2 method and result shapes are specified in
[V02_IMPLEMENTATION.md](V02_IMPLEMENTATION.md#service-apis). `workspace.get`,
`workspace.update`, `workspace.watch` and `workspace.unwatch` maintain explicit
per-project choices, next step and reference hashes. Watch checks are bounded
and run outside the service event loop. `workspace.launch` opens saved tools;
`desktop.actions` and `desktop.launch` expose fixed native actions. These APIs
are explicit local-user operations and are not provider tool vocabulary.

Desktop requests persist their identity and input fingerprint before effects.
Identical retries return the stored outcome; conflicting reuse fails. An
interrupted dispatch stays unknown and is never automatically repeated. Native
launches report requested; only a capture with a verified complete PNG and hash
reports completed. User-manager scopes keep native applications independent of
Kira's service cgroup. Unavailable binaries or sessions are visible conditions.

`system.diagnostics` returns a small whitelist of version, platform/session,
active-run count and native-action availability. `diagnostics.export` saves the
same report privately; neither includes project contents, credentials, account
identifiers or environment dumps. First-use/density preferences are GUI-owned
private configuration, outside the authoritative project database.


## Assistant presentation (Desktop 0.3.2)

[MARKDOWN_OUTPUT.md](MARKDOWN_OUTPUT.md) defines derived rich-text presentation.
Assistant Markdown is transformed only in the GUI. Stored events, source-copy,
export, service permissions, IPC1 and database schema3 are unchanged. Tool and
approval content stays literal. No resource is fetched by rendering; local link
activation requires an explicit user click and canonical current-project checks.
The matching-version service handshake applies to 0.3.2.


## Compact model controls (Desktop 0.3.3)

The GUI-only `model_controls_visible` boolean is persisted privately in ui.json,
defaulting to false for missing/invalid values. Show/Hide affects visibility only;
model/session controls remain instantiated, and no IPC request or run change is
triggered. [DESIGN_033.md](DESIGN_033.md) records appearance and icon packaging.
Database schema3, IPC1 and provider execution contracts remain unchanged.

## Compact tool output (Desktop 0.3.4)

Preview and expansion are transient GUI model roles, never persisted events.
Copy/export use the original recorded body; existing bounds remain unchanged.
See [TOOL_OUTPUT.md](TOOL_OUTPUT.md) for limits and scrolling behavior.

## Attachment paths (Desktop 0.3.5)

Explicit paste/drop/file-picker actions in chat and question answers insert
editable path text. GUI-owned image imports use private data/attachments PNGs;
existing files stay in place. Async insertion belongs to one unchanged input
context. Service events, provider payload types, permissions and schema3 remain
unchanged. [Storage and Windows boundary](ATTACHMENT_INPUT.md).

## Chat settings and activity (Desktop 0.3.6)

Private typed GUI preferences control font size, reduced motion, details-panel
visibility, close confirmation, local opener and the initial new-project permission.
Existing project permissions and service/provider settings are never changed by
these preferences. The exact routine session-configured notice is hidden only
from GUI presentation; durable events/export remain intact. Activity indicators
use observed run/tool/message state, never private reasoning. See
[CHAT_SETTINGS.md](CHAT_SETTINGS.md). IPC1 and database schema3 are unchanged.

## Execution context (Desktop 0.3.7)

Activity status cards identify their run through its own request/start metadata
and the state event timestamp, with literal details. Since 0.3.8 these lifecycle
cards are omitted from the conversation. Replay sequence deduplication and
all durable events remain unchanged. [Run status contract](RUN_STATUS.md).

## Project chats and shared context (Desktop 0.3.8)

[PROJECT_CHATS.md](PROJECT_CHATS.md) defines additive session.list,
session.snapshot, session.rename and project.context APIs, optional session title
and atomic project.update.share_session_summaries. Existing schema3 owns project/
session/run records unchanged. Internal chat_title metadata never reaches model
settings. Each view replays only its selected owned session; lifecycle events
remain in Activity and export. The last selected chat is a bounded private GUI
preference; unsent drafts are GUI-memory-only.

Cross-session context is deterministic and project-owned: at most five other
ordinary chats and 6000characters, with source references and unverified-claim
labels. It excludes current/foreign/team sessions and has a project opt-out.
Disabling it does not retract already supplied native-thread context. This does
not change the provider's own history or tool filesystem permission boundary.

## Appearance (Desktop 0.3.9)

[Appearance](APPEARANCE.md) defines the validated private GUI theme/font options,
system color/motion hints, theme exchange and presentation-only punctuation/diff
styles. Preferences apply without IPC, provider requests or persisted-message
changes. Existing chat-size and reduced-animation choices remain compatible.
IPC1, schema3 and backend settings are unchanged; the matching-version handshake
applies to 0.3.9. Renderer caches include local presentation settings and retain
the existing source/output/memory limits and inert-content boundary.

## Interface language (Desktop 0.3.10)

[Localization](LOCALIZATION.md) defines the private GUI language preference,
bundled catalogs and live retranslation. Italian remains the default. Language
changes do not alter persisted conversations, project data, provider parameters,
permissions or execution, and send no IPC requests. Machine status values remain
stable while their display labels are translated. IPC1/schema3 are unchanged;
the existing matching-version handshake applies to 0.3.10.

## In-place model selection and batched history (Desktop 0.4.1)

`session.configure {project_id, session_id, model, reasoning_effort?}` returns
`{session}` for an owned, ordinary, non-archived chat. It preserves the session
ID, backend, native thread reference, recorded events, title and unrelated
settings. Omitted effort preserves its value; an empty string selects the provider
default. Backend changes still create a separate session.

Configuration requires idle state, including overlapping project reservations,
owned native operations and unfinished queued prompts (even a paused queue).
Admission and the database update have no await boundary. New runs snapshot the
updated configuration; new initial running events record backend, model and
reasoning_effort. Historical events are never relabeled. Provider acceptance is
checked normally; no account permissions or access-program settings are altered.

The GUI waits for the service acknowledgment before changing the selector. It
keeps the current transcript and draft, rejects stale callbacks and disables send
and queue while configuration is pending. A model change retains an effort
advertised by the new model; otherwise it uses the provider default.

Historical replay retains ordered, paginated durable events and cursor-based
reconnection. It stages the bounded transcript and publishes one Qt model reset,
then reveals it at the latest message. Live updates remain incremental. Failed
replay shows available evidence but blocks sending until reloaded. Existing
60-run/600-row display limits and complete export semantics remain unchanged.

## Integrated Kira assistant and chat delegation (Desktop 0.5)

Schema 5 adds `assistant_delegations` and `assistant_calls`. Prior tables retain
their rows and native conversation references; migration from schemas 1–4 makes
a private original-schema backup and commits all additions transactionally.

Ordinary Codex chats receive a short service-owned operating contract through
`developerInstructions`. Project text, memory, file content, previous team output
and user requests remain lower-priority data. Identity is the Kira assistant
powered by the explicitly selected provider/model; capabilities and live scope
come from the service, never claims of perfect environment knowledge. Managed
team members retain their coordinator/member roles and mailbox tools.

The fixed `kira` tool namespace exposes `environment`, `team_delegate`,
`team_status` and `team_message`. Explicit delegation status includes bounded
members, roles, assignments, messages and recorded outputs with omission counts;
status without an ID stays a lightweight recent summary. Identities, model, reasoning, root and permission
are bound to the originating ordinary run and cannot be provided by a tool caller.
Delegation is available only in execute mode for supported Codex or OpenAI direct chats with tools
enabled. Plan/review/recap cannot dispatch work. No provider/account permissions
or global configuration are modified. Ollama identity/context is truthful about
its unavailable managed-assistant tool bridge.

Codex cannot attach dynamicTools through thread/resume in the qualified protocol.
A private per-run MCP overlay therefore provides Kira tools to both new and existing
threads. A 0700 directory contains a 0600 descriptor and Unix socket; an opaque
capability stays in the descriptor, outside prompts/configuration/events. Same-UID
peers and the bound callback are checked. Scope expires on completion/cancellation;
bootstrap derives the package root from its installed file, never cwd/PYTHONPATH.
The provider inventory and relay handshake are verified before inference. Native
delegation is disabled and verified when Kira-managed tools are supplied.

`team_delegate` accepts goal, brief, automatic/manual composition and existing
Team member/concurrency/round controls. The chosen model/effort and permission
remain inherited. A bounded source-chat excerpt, goal, decisions and references
accompany the brief. One accepted delegation per origin run is durable and
idempotent; conflicting call identities are rejected. Acceptance is pending,
not evidence that a team started. The model finishes its turn after acceptance.

The service starts the team only after successful origin completion and confirmed
provider cleanup, before releasing admission to queued work. Existing project and
overlapping-root reservations, exclusive writers and Team process ownership
remain authoritative. Failure, cancellation or uncertain cleanup prevents start.
Crashes never automatically replay pending handoffs; explicit continuation is
required. Recovery reattaches a durably created team if the crash preceded link
publication. Continuation identities and uncertain outcomes remain durable.

Progress is coalesced into `team.delegation` events on the origin run, with
delegation/team IDs, goal, state, source `kira_service`, summary/error and progress
counts. The GUI updates one clearly labeled Team card. Results are available in
the original chat and its next supplied context without silently starting a new
model turn. Recorded coordinator reports remain claims requiring evidence checks.
Model-generated messages use an assistant sender; GUI messages use the actual
user sender. Messages to settled teams are recorded only and do not restart work.
Explicit continuation supplies bounded prior messages only to their recipient/all
and records delivery evidence without claiming understanding.

GUI IPC: `assistant.snapshot {project_id,session_id}`, `assistant.cancel` with
`delegation_id`, `assistant.message` with delegation_id/request_id/recipient_id/content,
and `assistant.resume` with delegation_id/request_id. Every identity is checked
against the current ordinary chat. A message ack is separate from consumption.
Message/continuation identities are saved privately before the GUI sends an RPC;
uncertain identities survive GUI restart, with no automatic resend. A failed
local save blocks dispatch. The shared pending-intent limit is 128 without eviction.
Snapshots expose current capability/scope and a bounded recent-delegation list.
Pending/error states, cancellation, explicit continuation and the existing Team
view are available directly from the project chat; neither selection nor drafts
change when team events arrive.

Tool results remain below 64 KiB; snapshots below 650 KiB with explicit omission.
Recent supplied results are bounded, tokens do not trigger progress writes, idle
UI does not poll, and activity refreshes are coalesced. Session export includes
readable Team summaries plus the retained canonical event evidence.


## Direct ChatGPT plan backend (Desktop 0.6)

[OpenAI direct](OPENAI_DIRECT.md) defines the official subscription OAuth flow,
account-bound sessions, additional account IPC, Responses transport and local
tool execution. `openai_chatgpt` is accepted alongside existing backend IDs;
Team accepts Codex and OpenAI direct. Schema 5 and IPC/event envelopes 1 remain
unchanged. The matching-version handshake applies to 0.6.

## Provider continuity and images

Schema 6 adds provider-generation snapshots, idempotent switch receipts, exact
run bindings and image metadata/run/queue links. Migration is additive and creates
a consistent private backup before modifying existing schema 1–5 databases.
IPC and event envelopes remain version 1.

- `session.providers {project_id,session_id}` reads current and previous selections.
- `session.switch {project_id,session_id,request_id,backend,model,reasoning_effort,account_id?}`
  changes the provider of an idle ordinary chat. Replays return the immutable
  receipt together with the current session, never a stale selection.
- `attachment.import {path}` prepares a local private PNG and returns
  `{attachment:{id,name,mime,width,height,bytes,sha256,preview_url}}`.
  It performs no provider request. `run.start` and `queue.add` accept ordered
  `attachments:[id]`. Only explicit submission supplies the bytes to OpenAI direct.

[Provider continuity](PROVIDER_CONTINUITY.md) defines provider/account ownership,
bounded portable context and native-operation constraints. [Image attachments](IMAGE_ATTACHMENTS.md)
defines supported formats, snapshot validation, request budgets and backups.
Native reasoning, opaque provider messages and old tool calls are never replayed
across providers. Tool outcomes are bounded historical data with explicit provenance.
Current selected images must validate before admission; unavailable or over-budget
older images are identified in supplied context. Full transcript and attachment
records remain stored independently of model context limits.

## Durable direct context (Desktop 0.8)

[Direct context](DIRECT_CONTEXT.md) defines token-based history planning,
provider-native private checkpoints, generated source-linked memory, separate
important notes, original-history retrieval and explicit maintenance IPC. Schema 7
adds these entities without changing previous tables. IPC/event envelopes remain 1.
`context.status` and `context.compaction` expose only whitelisted public metrics
and phase records. Opaque native context is private service data, excluded from
ordinary events, UI and conversation exports. Manual context operations have
durable receipts and dedicated overlapping-root reservations.

## Direct web and browser additions

[Web and browser](WEB_BROWSER.md) defines hosted search observations, clickable
citations, service-owned browser tools, explicit interaction decisions, isolated
profiles and screenshot input. `session.web.get` and `session.web.update` are
chat-owned preference/status APIs. Browser execution is direct-backend-only;
Codex's native tool environment remains separate. Read-only/non-execute runs may
read public pages but cannot click, type or enter manual login. Browser workers
have independent durable actor/process ownership, with the existing identity
checks and cleanup rules. Screenshot assets are tool artifacts, never user-message
attachments. Existing schema 7 data is retained; schema 8 adds independent browser
actor/process ownership.
IPC/event envelopes remain version 1.

### Embedded browser (Desktop 0.8.1)

The service owns a browser actor independently of any one model turn. Schema 8
adds `browser_sessions`, `browser_processes` and `browser_process_members` after
a consistent private backup, without modifying schema-7 tables or their rows.
An actor is bound to one chat, provider generation and account. Its worker is
registered before initialization and is cleaned through the same Linux identity
checks as provider children. Completed turns release their own reservations while
the independently owned browser remains available. Restart recovery closes old
owned workers; it never restores pages or repeats uncertain interactions.

The GUI embeds a live view of that worker inside the chat window. It receives
bounded transient JPEG frames over the private IPC channel, displays only the
current chat/actor stream and sends explicit manual input through service APIs.
These frames and manual input text are never model input, durable events,
conversation content, logs or SQLite records. Explicit model screenshot calls
remain separately owned, masked image artifacts under the existing contract.

`session.browser.open`, `watch`, `unwatch`, `command` and `close` require an owned
project/chat and matching browser identity. The service gives each watcher an
ephemeral input stream identity bound to that actor/client. Commands carry a
monotonic sequence and request identity; bounded receipts plus a high-water mark
prevent old or uncertain inputs from being replayed after cache eviction.
Disconnect revokes the stream. Stale actor/session/account/generation messages
cannot navigate a different chat. A view subscription does not authorize manual
input: the user explicitly takes control, pausing agent actions, and explicitly
returns control afterwards.

`browser_event` is an additive, non-durable IPC envelope for actor state and
viewport frames. Frame delivery coalesces to the latest view and does not consume
the durable run-event queue. Hiding or minimizing the view stops frame capture;
it does not delete the browser or chat. Closing the browser stops the worker and
removes a temporary profile only after verified cleanup. Persistent profiles
follow the existing per-chat/account/generation opt-in. See [Web and browser](WEB_BROWSER.md).

### Persistent browser action consent (Desktop 0.10.0)

The [browser consent contract](WEB_BROWSER.md#browser-action-consent) defines
`browser_action_approval:ask|always` in ordinary direct-chat settings. The default
is `ask`. A policy-only `session.web.update` is admitted during activity; other
Web preferences still require idle state. The GUI sends `expected_generation`
with policy updates; the service rejects a retired/malformed generation before
changing consent. The saved policy covers browser and
Computer mutations in that chat and is rechecked before dispatch. It does not
change project/mode, manual-authentication, observation or cancellation contracts.

`run.approve` adds `accept_always` only for trusted pending browser actions with
`allow_always:true`. Service-private actor/generation binding establishes eligibility;
provider JSON cannot enable it. The saved grant and pending decision are applied
without an await boundary. A durable `backend.notice` records the decision and
chat scope; no synthetic tool completion is emitted. The grant survives restarts,
is revocable while working, and is cleared on provider/account transitions and
forking. Database schema 9 and IPC/event envelope 1 are unchanged.

### Native browser authentication and manual waits (Desktop 0.9.1)

Embedded Chromium runs headed on a private, authenticated Xvfb display with no
TCP listener. Page frames remain the automation view. Manual control switches
the viewer to bounded captures of the complete owned browser, including native
dialogs, and XTest input. Model tools cannot capture or operate that native
surface. Each published frame binds its tab, dimensions and mode to a fresh
`surface_id`; queued pointer, keyboard, text and copy commands must retain that
identity. Changes invalidate old input and release held buttons/keys.

In WSL, an available Windows WebAuthn API can be reached through Kira's owned
Chrome `webAuthenticationProxy` extension and an ephemeral Windows helper.
Only explicit desktop takeover authorizes a native authentication request;
model-requested manual login merely pauses automation. Chromium supplies the
trusted origin/RP context. Unsupported options fail before native effects.
Request/response bodies stay in owned pipes and extension memory; only bounded
operation, origin, pending/result metadata reaches the transient GUI state.
Windows PIN/biometric confirmation is exclusively user-operated OS UI. Completion
of WebAuthn does not establish that the website accepted a login. Cancellation
and uncertain completion never cause an automatic replay.

When a browser/computer tool awaits manual control, the service persists
`tool.progress` with exactly `tool_id`, `name`,
`phase:waiting_for_browser_control|resuming` and `reason:manual_control`.
The tool remains pending; the event is neither successful execution nor a
new user-input question. Only explicit return of control resumes the queued
operation, after cancellation and stale-observation checks. The selected-chat
activity row explains the wait without an execution animation. Hiding the browser
does not return control; cancelling the run does not close the user's page.

### Kira Computer (Desktop 0.8.2)

`computer.observe {}` and `computer.act {observation_id,action,...}` are Kira-owned
visual tools for the existing isolated browser actor. The direct-provider wire
namespace is `kira_computer`; these are custom function calls, not OpenAI's native
`computer` tool or an entitlement to control the user's general desktop.
Schema 8 and IPC/event envelopes 1 remain unchanged.

Observe returns owned screenshot pixels, a transient observation identity, tab,
URL and viewport dimensions. Act performs one bounded mouse/keyboard/scroll/wait
operation and returns a new observation. The worker checks the observed page and
masked image before preparation and again before execution, then consumes the
prepared operation once. Changing the document, tab, geometry or manual-control
state invalidates the observation. The service also binds observations to their
generating run and actor; historical images never authorize current actions.

Click, double-click, type, key and drag retain execute/writable-project admission
and user consent for the action or the chat-wide browser policy. Observation, move, scroll and bounded waiting
are available for public-page reads. Manual takeover pauses model operations;
password/file controls require manual input. Service-owned effects, events,
request identity, cancellation and cleanup remain authoritative. An action that
ran but whose follow-up capture failed retains `action_executed:true`; an
interrupted effect is uncertain and is never automatically replayed.

Computer attempts are bounded to 32 visual cycles per run, with image capacity
reserved before effects. Browser screenshot calls retain their separate limit.
Only two recent browser/computer image results remain in working model context;
original image artifacts retain their existing private ownership and audit role.
The GUI's continuous view stays separate from these explicit model observations.

### Visible request failures (Desktop 0.8.3)

The latest failed/interrupted ordinary request in the selected chat has a compact
GUI error panel with access to its recorded details. It is derived from existing
run/event state and survives replay/reopen without additional persistence. It
cannot adopt another chat's error or the current composer draft as its request.
Routine lifecycle history remains in Activity. Details preserve the run request
identity and complete recorded outcome, including provider HTTP/request metadata
when present. Literal provider error content is not translated or executed.
No provider request, automatic retry, account/model change, schema migration or
historical-outcome rewrite is implied by displaying or inspecting a failure.

### Optional hosted web metadata (Desktop 0.8.4)

Hosted web summaries accept missing action metadata and absent/null `open_page`
URLs as unavailable descriptive data. They never synthesize a destination from
another call or from citations. Existing enabled-tool, item identity, completed
status, response-terminal and bounds checks remain authoritative. Required
`find_in_page` fields and unsafe/malformed supplied URLs remain errors. No local
execution or retry is triggered by provider-hosted web observations. Original
native items are retained unchanged for continuation; past interrupted outcomes
are not rewritten when the adapter is updated.


## Team orchestration and chat continuation (Desktop 0.10.1)

The ordinary-chat namespace adds `kira.team_resume {delegation_id,instruction?}`.
The service queues a new continuation delegation, bound to the originating
chat/project/generation, provider, model, effort and account. It starts only after
successful origin completion and confirmed cleanup. Its source Team must be
settled and clean. Pending or existing children prevent duplicate continuations;
final admission rechecks scope after awaits. Model-requested continuations leave
source cards and Team results intact. Children link to their parent and receive
bounded prior evidence without replaying earlier assignments. Model-generated
instructions retain assistant provenance. Recovery reconciles committed child
identities without replaying unconfirmed effects.

Mailbox replies expose service-derived scheduling facts. Messages do not start
members; empty mailbox polling cannot advance coordinator planning. Multi-member
completion needs clean completed worker execution evidence from the current Team
or a bounded explicit same-scope continuation chain. Otherwise one corrective
planning attempt is allowed within the existing round budget; repeated failure
or budget exhaustion pauses with `no_progress`. Unverified summaries and feedback
remain recorded. Genuine single-member coordinator work remains supported.
Historical completed rows are not rewritten; read models expose missing execution
evidence. [Team details](V03_TEAMS.md) defines the scheduling and receipt rules.


## Explicit continuation round limit (Desktop 0.10.2)

`team.resume`, `assistant.resume` and model `kira.team_resume` accept optional
`round_limit`, a strict integer in 0–10,000. Omission inherits the actual source
Team's limit; explicit zero removes the automatic coordinator-round ceiling.
The override affects only the new child. Source limits, outcomes, model, effort,
account, permission and no-progress/cancellation guards remain unchanged.

Validation precedes scheduling; receipt fingerprints distinguish omission and
explicit values, including zero. Identical retries return the recorded result;
reusing an identity with a different limit is rejected. Deferred model requests
persist the selected effective limit and explicit override. Explicit GUI recovery
of an unstarted pending request retains its accepted override unless replaced by
a new user choice. Subsequent continuations of a linked child inherit that actual
child's current limit rather than an older saved override.

Delegation snapshots and accepted handoff receipts expose effective `round_limit`.
The chat details and Team view expose an editable resume field; editing alone
starts no work and does not change the parent. Unedited GUI resumes omit the
parameter. In-flight or uncertain requests retain their original limit/identity;
chat continuation receipts preserve the concrete parent identity across GUI
restart, while the Team view retains its existing in-memory command lifecycle.


## Native workspace (Desktop 0.11.0)

The GUI uses compact tool receipts and a common nonmodal side-panel host for
Team continuation, knowledge, context, research, usage, workspace recovery,
artifacts and original event data. On wide windows the panel reserves reading
space; narrow or expanded views overlay the right workspace. Closing, switching
or resizing a panel never starts, resumes or cancels an execution. Browser view
navigation does not close its actor or return manual control. Approvals and
native authentication retain their existing explicit boundaries.

Receipt labels and previews derive only from bounded recorded tool metadata;
unknown names and all original bodies/commands remain literal. Receipt, output
and tool-group expansion are independent presentation choices. Completed request
status and tool error/unconfirmed counts remain separate; durable terminal states
are not rewritten. Only the selected request supplies the activity summary.

The saved chat goal is fetched once per selected connected scope through
`goal.get`, without inference, queue reservation or composer locking. Late reads
from a prior scope or preceding an explicit goal edit are discarded. This
presentation read cannot advance a goal. Settings categories keep controls and
unsaved field edits instantiated. Existing provider/model/effort, themes,
permissions, schema 10 and IPC/event envelopes 1 remain unchanged.
