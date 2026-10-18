# Kira Desktop 0.3: collaborating project teams

This document defines the application contract introduced in Desktop 0.3.
Profile selection and safe upgrades are documented in [operations](OPERATIONS.md).
Historical implementation records follow the
[evidence retention policy](evidence/README.md); current qualification is in
[STATUS](STATUS.md).

Desktop 0.6 also supports [OpenAI direct](OPENAI_DIRECT.md) with account-bound
member sessions, HTTP cleanup and the same scheduler. Service tier applies only
to Codex. Direct project-write members use Kira file tools; arbitrary commands
require trusted-host permission. The Codex-specific details below describe its
original process transport.

## Scope and ownership

- Manual composition: user chooses total members and edits specializations,
  instructions and access. The total includes one visible coordinator.
- Automatic composition: the coordinator reads project context and generates
  a structured roster and assignments, then Kira starts the team automatically.
- Actual member sessions, direct/broadcast mailbox tools, task dependencies,
  coordinated file changes, visible results, stop and explicit continuation.
- Codex is the first supported team backend. Ollama setup remains deferred;
  ordinary Ollama conversations retain their existing behavior.
- Model/reasoning/service-tier choices are explicit at team level and inherited
  by every member, including the coordinator. No silent model/account switch.
- Coordinator owns domain contracts, version/lock/CLI/client, integration tests,
  packaging and top-level docs. Runtime owns service/tools and service tests;
  providers owns backends/backend tests/BACKENDS and `protocol-schema/teams-0.162.1`;
  desktop owns UI and UI tests. These were the original implementation roles;
  new work assigns explicit disjoint ownership for each task.

## Execution policy

The coordinator is member `coordinator`; manual rosters include it as their first
entry. Other member IDs are stable, bounded ASCII keys. Automatic mode creates
that coordinator first and freezes the returned roster after the first plan.
One service owns all state and project reservations. Separate service instances
or external editors are outside this scheduler's write coordination.

Each coordination round is a genuine read-only Codex turn with structured output.
Its plan supplies dependency-ordered tasks. Independent read-only tasks can run
concurrently. A writer runs exclusively, with no other team reader or writer
active, until its provider task and cleanup actually finish. Role descriptions
and suggested file ownership are organizational context, not a sandbox.
Effective write access never exceeds the project's selected permission;
`trusted_host` retains its explicit non-sandbox meaning.

After the assignments finish, the coordinator receives their actual outcomes,
messages and evidence and either completes, requests user input, or assigns a
new round. Failed/uncertain work cannot be hidden by a success narrative. A
stopped process, not only a terminal database row, releases a writer/reservation.
Each assignee also receives its current task and the recorded outcomes of its
declared dependencies, after completion and cleanup have been confirmed. This
handoff includes bounded final reports and run/evidence references, independently
of direct-message routing. A provider turn's completion alone does not certify
that its task objective was achieved; the next member checks the actual effects.
An active team excludes other writers and other teams on overlapping project
roots. Desktop 0.12.0 permits one same-project ordinary conversation with effective
read-only permissions for its full run; see [Live conversation](LIVE_CONVERSATION.md). A team's internal sessions/runs cannot be started independently by IPC.

Resource controls are visible and configurable: total members 1–12, concurrency
1–8 (and no more than members), coordinator round limit default 3, configurable
from 1 to 10,000 with 0 meaning unlimited. The limit counts planning/coordination rounds, not model reasoning or
tool calls. Exhausting it pauses the team with a reason, never claims completion.
At most 24 tasks are accepted in one plan. Repeated empty/no-progress plans stop
visibly rather than spinning. Provider/output/frame limits remain documented.

## IPC v1

Database schema advances additively to 3 with a private consistent backup and
transactional 0/1/2 migrations. Existing ten tables retain their data/columns.
Team state, members, steps, messages, sequenced events and owned provider-process
identities are separate persisted entities; linking tables may associate ordinary
sessions/runs. Ordinary run/event envelopes remain unchanged.

`team.start` parameters:

```
{project_id, request_id, origin_session_id?, goal, mode:"manual"|"automatic", backend:"codex",
 model, settings:{reasoning_effort?,service_tier?,...existing validated settings},
 members?:[{id,name,role,instructions,access:"read"|"write"}],
 max_agents?:4, concurrency?:2, round_limit?:3}
```

Manual mode uses the exact supplied roster; automatic mode uses `max_agents` as
its total cap. Access is a maximum capability: `read` maps to actual read_only;
`write` maps to the project's permission and is rejected for a read-only project.
Coordinator planning always downscopes to read_only. IDs are unique and match
`[a-z][a-z0-9_-]{0,47}`; `all`, `user`, `human` and `system` are reserved for
broadcast/human/service identities. Names max 100 UTF-8 bytes, roles max 400, instructions
max 8,000; goal max 64,000. All integers reject booleans. Exact retries return
the existing team; conflicting reuse of request_id fails before side effects.

- `team.list {project_id}` -> `{teams:[summary...]}` (newest first, bounded).
- `team.snapshot {team_id}` -> `{team: snapshot}`.
- `team.events {team_id,after_seq?:0,limit?:100}` ->
  `{events,last_seq,has_more}`; limit max 250 and byte-bounded pages.
- `team.subscribe {team_id,after_seq?:0}` -> same replay plus gap-free registration.
  Notification `{version:1,team_event:{schema_version:1,team_id,seq,type,timestamp,payload}}`.
- `team.send_message {team_id,request_id,recipient_id:"all"|member_id,content}` ->
  `{message}`. Human sender ID `user` is derived by the service, not a supplied field.
- `team.cancel {team_id}` -> `{team}`; stop new scheduling, cancel/drain every child.
- `team.resume {team_id,request_id,instruction?:""}` -> `{team}` for a new explicit
  continuation linked by parent_team_id; preserve earlier outcomes and uncertain
  effects, do not automatically replay old assignments. Reservation rules apply.
- `team.approve {team_id,run_id,approval_id,decision:"accept"|"decline"|"cancel"}`
  -> `{accepted}`; validate run membership and preserve backend permission policy.

Snapshots are bounded and contain:

```
{id,project_id,goal,mode,state,phase,round_index,round_limit,concurrency,
 backend,model,settings,parent_team_id,created_at,updated_at,last_seq,
 summary,error,pause_reason,
 members:[{id,name,role,instructions,access,state,run_id?,last_output?}],
 steps:[{id,member_id,title,instructions,access,depends_on,state,run_id?,round_index,error?}],
 messages:[{id,seq,sender_id,sender_name,recipient_id,content,created_at}],
 approvals:[{run_id,approval_id,member_id,request}], omitted:{...}}
```

States: planning, running, cancelling, completed, failed, cancelled, interrupted,
paused. Only an explicit continuation starts more work after terminal/pause.
State/step/message/result changes commit with team events before publication.
Use events such as `team.state`, `team.plan`, `step.state`, `member.state`,
`team.message`, `team.message.supplied`, `team.approval`, `team.result`.
Detailed tool/stream evidence remains in linked ordinary run events; a small
`member.activity` may reference that run and sequence without duplicating full
streams. UI restores a snapshot and per-team replay cursor after reconnect.
Snapshots fit 650 KB. Approval pages retain at most 64 identities at once and
report omitted requests; later requests appear after earlier decisions. Large
requests have explicit previews and a link to run evidence. The GUI permits
decline/cancel but does not grant approval from a truncated preview.

## Coordinator output

One JSON object constrained with turn/start.outputSchema:

```
{decision:"continue"|"completed"|"needs_input",summary:string,
 members:[{id,name,role,instructions,access:"read"|"write"}],
 tasks:[{id,title,member_id,instructions,access:"read"|"write",depends_on:[task_id]}]}
```

Initial automatic planning returns the roster including `coordinator`; manual
and later plans return `members:[]`. Task IDs are unique within the round;
dependencies reference that same round and must form an acyclic graph. Requested
access must be within both member capability and project permission. Validate
strictly in the service despite the provider schema. For completed/needs_input,
tasks is empty; an empty continue plan is not a successful task execution.
Persist the actual plan and validation errors. A completed coordinator turn is
not by itself a completed team objective.

## Provider seam and mailbox

RunContext adds optional fields, preserving standalone callers:

- `client_tools:list[Json]` in actual AppServer DynamicToolSpec format.
- `client_tool_call(call_id:str,name:str,args:Json)->Awaitable[Json]`.
- `output_schema:Json|None`; structured final text is returned as
  `outcome.final_text` alongside normal durable message events.
- `disable_native_agents:bool` and `provider_started(pid:int)->Awaitable[None]`.

The fixed `team` namespace has `send_message {recipient_id,content}` and
`read_messages {after_seq?:0,limit?:20}`. Message content is at most 8,000 UTF-8
bytes; mailbox pages fit the 64 KiB callback limit (max 50 messages). Callback
sender/team/run identities come exclusively from the bound execution context.
Results always use `{ok:bool,...}`, including ordinary validation errors.
Durable call-ID/fingerprint deduplication commits before sending a response.
Tool results and teammate content cannot raise execution permissions. Sanitize
before storing/broadcasting; never log authentication payloads or credentials.

Messages are recorded before delivery. Agents can read their mailbox during a
turn; pending messages are also supplied to their next assignment. The UI must
distinguish recorded/supplied context from a claim that a model understood it.
`team.message.supplied` identifies `member_id`, `run_id`, `message_ids` and
`via: "read_messages" | "assignment_context"`; reads also retain the provider
`call_id`, including an empty mailbox read. Direct messages are routed to their
recipient; the project owner can inspect the shared UI history.
Completed/paused teams do not silently start new work because a message arrives.

Codex dynamic tools require experimentalApi opt-in, per current official
[App Server documentation](https://learn.chatgpt.com/docs/app-server).
Recorded 0.162.0 and 0.162.1 protocol probes accept the same schemas. Native Codex
delegation is disabled and verified for Kira-managed members so user-visible
roster/resource choices are authoritative. Native tool notifications remain
observations; never dispatch them through Kira's file/command tool handler.

## Recovery, UI and acceptance

Register provider PID identity before inference, including boot/start-time/UID
and private store/run ownership. On recovery, only verified owned processes may
be signalled. Unknown cleanup blocks automatic writers; PID reuse never permits
signalling an unrelated process. Interrupted teams remain visible and are never
blindly replayed. UI closure alone does not stop a service-owned team.
Ownership also binds to the canonical database path/device/inode, the observing
PID namespace and the registering service's process identity. Copying a backup
does not transfer signalling authority over the original instance. Mismatched or
incomplete provenance remains unknown; confirming absence in the same namespace
can release stale records without signalling. Ordinary same-store crash recovery
continues to reconcile verified orphaned children.

The Team view provides manual/automatic setup, explicit model/effort, member and
task status, selectable shared messages/results, a per-run approval queue, stop
and explicit continuation. Drafts and user scroll/focus survive refresh/errors.
Do not show invented completion percentages or describe queued messages as read.

Required gates: migration/rollback and populated 0.2 data preservation; exclusion
and writer cleanup; genuine read-only parallelism; mailbox privacy/correlation/
idempotence; cancellation and crash without replay; UI reconnect and approvals;
real automatic and manual Codex team operation using the configured model/effort,
with independent filesystem/message verification; isolated package/UI validation.
Fixtures never substitute for an actual provider turn. Run checks against an
isolated disposable profile and project; preserve active user work and global
Codex settings.

## Chat-native orchestration (0.5)

The ordinary Kira assistant can now request a durable managed delegation. The
existing scheduler and mailbox remain authoritative; provider-native delegation
is disabled for this managed path. Parent-turn completion/cleanup precedes team
admission. The original chat receives coalesced service records, and subsequent
turn context includes bounded results and source references. Manual/automatic
composition, explicit continuation and message provenance are preserved.
[Canonical assistant contract](CONTRACTS.md#integrated-kira-assistant-and-chat-delegation-desktop-05)
and [user workflow](USER_GUIDE_IT.md#lassistente-kira-e-team-dalla-conversazione).


## Readable run evidence (Desktop 0.6.1)

The Team run-evidence dialog keeps chronological event identifiers and recorded
time zones, with expandable event blocks, local text zoom and two presentation
modes. Readable payloads use indented literal `|` blocks for multiline strings
and conservatively format standalone nested JSON. Code, values and event names
are not translated or executed. This is a derived JSON-like view, not JSON
serialization. The original payload view and Copy JSON use the retained source.

Presentation remains bounded to 600 events and the existing 131072-character
payload preview. Truncated original previews are visibly marked and cannot be
copied as complete JSON. Formatter limits include depth24,4096nodes and64embedded
parse attempts. No IPC, schema, provider policy or persisted event changes occur.


## Corrective planning and model continuation (Desktop 0.10.1)

Coordinator instructions and mailbox replies distinguish messages from assignments:
`send_message` stores context; the coordinator returns `decision:continue` with
concrete `tasks` before members start. Polling idle members cannot advance planning.

A multi-member `completed` plan requires clean completed non-coordinator assignment
receipts. Explicit continuations can use genuine prior worker receipts along at
most 32 same-project/same-goal parent links, with bounded source result previews;
summaries alone are insufficient. Unresolved work and uncertain cleanup block
completion. A true one-member coordinator Team remains valid.

Missing participation or an empty continue plan records `team.plan.feedback` and
permits at most one corrected coordinator turn within the configured round budget.
No extra round is silently added. Budget exhaustion or repeated unproductive
planning pauses with `no_progress` and an unverified summary. Provider failures
are not retried automatically. Snapshots expose `scheduling` and
`completion_evidence`; successful results record `completion_basis`. Historical
states stay intact while the GUI identifies unsupported conclusions for review.

Ordinary chats can queue `kira.team_resume {delegation_id,instruction?}` after
inspecting a settled delegation. It starts after the requesting turn successfully
finishes and cleans up, preserves the selected provider/model/effort/account, and
creates a linked continuation card without replacing the old model-source card.
Duplicate, foreign, active, stale-scope or uncertain continuations are rejected.
Model continuation instructions retain assistant provenance; GUI instructions
retain user provenance. No old assignment is automatically replayed. The existing
GUI continuation remains available. A planning round and final synthesis normally
need at least two coordinator turns; the default limit remains three.


## Choosing continuation rounds (Desktop 0.10.2)

Resume accepts an optional `round_limit` integer from 0 to 10,000. Explicit zero
means no automatic coordinator-round ceiling; omission inherits the current
source limit. Only the linked child changes. The original Team and its evidence
are retained; normal scope, cleanup, duplicate and no-progress checks still apply.
This is available through the Team view, ordinary-chat Team details and
`kira.team_resume`, where a changed limit should reflect an explicit user request.
