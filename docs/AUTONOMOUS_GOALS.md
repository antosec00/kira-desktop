# Autonomous chat objectives (0.14.0)

This contract extends [Workbench](WORKBENCH.md) and [Live conversation](LIVE_CONVERSATION.md).
Database schema 13 adds goal ownership, cycle receipts and command identities.
Existing chat objectives, runs, transcripts, settings and Team rows are preserved.
No existing manual objective is automatically armed by migration or GUI refresh.
IPC and event envelope versions remain 1.

## Explicit activation and control

`/obiettivo <objective>`, `/obbiettivo <objective>` and `/goal <objective>` are
application commands, not prompts sent verbatim to a provider. The graphical
objective panel provides the same explicit activation. With no argument, the
command opens that panel. `pausa`, `riprendi` and `stato` control or inspect the
current objective. A saved manual objective remains passive until the user
starts autonomous execution.

`goal.start {project_id,session_id,request_id,objective,max_cycles?}` creates a
current autonomous objective and retains prior goal records. `max_cycles:0` is
the default and imposes no cycle ceiling. Positive values are explicit optional
ceilings, not a promise of completion within that number. There is no implicit
time budget. User-selected provider, account, model, reasoning effort and project
permission are preserved. This mode grants no additional filesystem or browser
permissions and cannot dismiss approval or authentication requirements.

`goal.pause/resume {project_id,session_id,request_id}` operate on the same chat.
Command identities are durable: a retry cannot create another goal or dispatch a
duplicate cycle. Pause prevents new cycles while allowing the in-flight operation
to finish; ordinary Stop also pauses future autonomous continuation. Team work
remains independently controlled. Resume is an explicit renewed admission check.
`goal.get` returns the manual objective with an optional `autonomous` object,
including current status, cycle count, summary, next step, error, evidence and
actual run metadata. Inspection never starts execution.

## Durable scheduler

Only the service creates cycles. Each cycle is claimed with its run in the same
SQLite transaction. It uses an ordinary Execute run with source
`goal_continuation`, the originating chat and its durable provider context.
An initial `backend.notice` records service provenance; automatic continuation
is not a fabricated new user request in history, exports or retrieval.

The scheduler waits for active project work and reservations, manually controlled
browsers, explicit queued requests (including paused requests), and pending Team
result delivery. Queued user work has priority. The same root cannot acquire
conflicting execution ownership. Scope and provider binding are checked again
after asynchronous account preparation. Changing a model/account/root/permission
requires explicit resume before autonomous work can use that changed binding.

A completed response permits another cycle; it does not itself complete the
objective. A delegated Team is allowed to finish and deliver its recorded result
before the next autonomous cycle. A Team result turn keeps its existing no-recursive-
delegation rule; the subsequent goal cycle can decide the next authorized step.
A paused Team is not silently resumed by the scheduler itself.

Closing the GUI does not discard a service-owned goal. A service restart retains
its objective, checkpoint and evidence but does not replay an unfinished or
uncertain cycle: explicit resume is required. Authentication, quota, provider
protocol failures and uncertain cleanup are visible stopping conditions.

## Focus, progress and completion

The saved objective is supplied on each ordinary turn, with bounded progress,
next step and evidence references. It stays separate from current user guidance.
New corrections remain in the conversation; historical results never authorize
repeating effects. Original records remain in the archive and can be retrieved
through Kira's scoped history and knowledge tools. A compact summary is a model
claim, not a replacement for source evidence or a guarantee of perfect recall.

Provider-neutral `goal.status` exposes the current goal and usable receipt
references. `goal.checkpoint` records bounded summary, next step and a status
of `continue`, `completed` or `blocked`. Evidence references must resolve to
appropriate recorded sources; unrelated projects cannot be used as proof.
Pure writing objectives can provide an explicit textual deliverable. Checkpoint
completion is applied only after a successful terminal run and confirmed cleanup.
A failed run cannot turn a previously proposed completion into success.

Completion is the model's evidence-backed assessment, not independent proof that
every real-world requirement is satisfied. The application checks provenance,
execution and record consistency; it cannot guarantee model understanding or
semantic correctness. Broad continuous-research objectives need an explicit
success condition to have a finite completion point; otherwise they remain
active until paused or blocked.

Repeated cycles without useful recorded progress become visibly blocked instead
of endlessly emitting continuation text. Missing permissions, user decisions or
external requirements are reported as blockers. The objective panel keeps the
reason and last checkpoint visible, independently of the current run's status.

## Qualification

Use isolated profiles and synthetic providers for deterministic scheduling,
race, cancellation, provenance and migration checks. A passing synthetic suite
is not evidence that a live model will always report useful progress or identify
completion correctly. Current artifact and live limits are recorded in [STATUS](STATUS.md).
