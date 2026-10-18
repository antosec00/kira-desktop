# Chat controls and workbench (Desktop 0.4)

This contract records the original 0.4 controls. The current
[live conversation contract](LIVE_CONVERSATION.md) extends steering and Team
conversation admission in 0.12.0. Release
qualification is recorded separately in STATUS. All features have graphical
controls; slash actions and keyboard shortcuts are optional accelerators.
Labels are Italian/English; user content, models and reasoning effort stay literal.

## Ownership and recovery

Schema 4 adds independent tables for chat metadata/goals, operation identities,
run modes, queued prompts and steering delivery. Migration retains every existing
table and creates a consistent private backup before a transactional upgrade.
IPC/event envelopes remain version 1; GUI and service versions must match.
Only the service writes SQLite. Private unsent GUI drafts/history use a separate
bounded atomic file. Existing user profiles must remain untouched during development.

One ordinary run per project and overlapping-root reservations still apply.
The explicit 0.12.0 exception admits one read-only conversation alongside the
same project’s active Team; its reservation persists until actual cleanup.
Pending provider chat controls reserve the same roots. Restart pauses all queued
dispatch; interrupted work and uncertain provider effects are never replayed.

## Run modes and steering

`run.start` adds optional `mode: execute|plan|review|recap` (default execute).
Mode participates in durable request identity. Plan, review and recap downscope
the execution context to read_only without changing stored project permissions.
Plan proposes work; execution is a subsequent explicit user action. Review uses
the selected model and reports findings; recap creates a readable summary.
Neither operation deletes recorded history or claims to reduce native context.

`run.steer {run_id,request_id,prompt}` adds user guidance to that exact active
ordinary run. The service commits an intent before provider dispatch. Native
acknowledgment is recorded separately from model consumption. Uncertain delivery
stays unknown and is never retried automatically. Unsupported providers and stale
turns return explicit errors. Steering cannot change model, effort or permissions.

`session.compact {project_id,session_id,request_id}` is an explicit idle native
context operation. It retains the original Kira transcript; successful completion
requires provider terminal evidence, not just an accepted RPC. Provider support
must be reported accurately. An uncertain operation identity cannot be replayed.

## Persistent queue

- `queue.list {project_id}` returns `items`, `paused` and `limit`.
- `queue.add {session_id,request_id,prompt,mode?}` commits a pending request.
- `queue.edit {project_id,item_id,prompt}` edits only a pending request.
- `queue.reorder {project_id,item_ids}` supplies the exact pending item order.
- `queue.remove {project_id,item_id}` removes a pending or finished item only.
- `queue.pause/resume {project_id}` controls future dispatch, never an active turn.

Items expose id/item_id, project_id, session_id, request_id, prompt, mode,
position, status, run_id, error, created_at and updated_at. States are pending,
starting, running, completed, failed, cancelled and interrupted. At most 64 pending
items per project, each at most 8000 encoded UTF-8 bytes, including JSON escaping.
List responses are bounded. Removed identities remain tombstones so delayed
retries cannot resurrect deliberately removed work.
Already accepted identities cannot be reused with different inputs. The queue
continues after confirmed completion and pauses after failure/cancellation or
service restart. GUI polling observes committed state; closing the GUI does not
discard the queue. Editing/removal never cancels an already dispatched operation.

## Chat organization and goals

`session.list` adds an `archived` boolean filter (default false).
`session.archive {project_id,session_id,archived}` preserves history and permits
restoration. Archived sessions cannot start work; archiving requires idle state.
`session.export` exports complete Markdown to text or a private file; UI previews
and exports remain distinct. Foreign and managed team sessions are rejected.

`session.fork {project_id,session_id,request_id,through_run_id?,title?}` returns
a new session and its native/copied_context mode. The original stays intact.
A selected run must be a completed owned turn. Native branching requires its
known native turn boundary; missing capabilities fail rather than include later
history. Portable branches retain explicit source context and disclose bounded
model context. Native inherited context remains in the provider thread; the GUI
links to the original Kira chat rather than manufacturing duplicate run events.
The new local transcript starts with requests made on the branch. Branching a
chat never rolls project files back.

`goal.get` and `goal.update` take project_id/session_id; update accepts objective
and status active/paused/completed. The active objective is included in subsequent
turn context. Continue is an explicit user action; goal state does not silently
start an unlimited execution loop. A user marking a goal complete is distinct
from independent validation. No implicit time/token budget or model change.

In 0.14.0, [Autonomous goals](AUTONOMOUS_GOALS.md) adds a separately and explicitly
armed mode through `goal.start`. Its scheduler owns successive execution cycles;
the original saved-goal behavior above remains passive until that activation.

## Files, changes and graphical controls

`project.files {project_id,query,limit?}` provides bounded contained path search
for @ mentions and the graphical picker. Skipped files/limits are reported.
`project.changes {project_id,path?}` reports staged/unstaged/untracked Git changes
with bounded inert text. It does not execute repository hooks, external diff
drivers or apply/revert edits. Review supplies a bounded preview to its readonly
run, including with local providers that cannot execute Git commands themselves.

Azioni rapide/Quick actions unifies existing actions with the workbench.
Ctrl+C remains Copy and Tab retains normal focus navigation. Busy composers offer
distinct Queue and Update instructions actions; ordinary Enter never implicitly
steers a running task. Draft recovery and local history are private and bounded.
Pending mutation identities are saved before dispatch and retained across context
switches and restarts; uncertain delivery never silently becomes a fresh action.

Native fork/compaction processes use their own durable ownership registry with
the same origin, process-namespace and PID-reuse checks as ordinary providers.
Unconfirmed cleanup retains the project reservation. Settings changes serialize
provider admission and cannot replace the executable for an accepted operation.

Extensions, procedure catalogs, external connections, lifecycle automations,
imports and managed worktrees remain later roadmap items. Their UI must not
claim unimplemented capabilities or mutate global provider configuration.
