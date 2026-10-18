# Live conversation and Team result delivery (0.12.0)

This contract extends [Workbench](WORKBENCH.md), [Teams](V03_TEAMS.md) and
[Contracts](CONTRACTS.md). IPC/event envelopes remain 1. Schema 11 adds only
`run_lanes` and `assistant_followups`; existing tables, history and provider
references remain intact. No historical Team is armed during migration.

## Guidance within an active turn

`run.steer {run_id,request_id,prompt}` records text for the exact ordinary run.
It cannot change provider, account, model, effort, permissions or attachments.
Retries with the same identity inspect the existing delivery, never duplicate it.
Each message is bounded to 32,000 UTF-8 bytes, including JSON escaping.
The direct/OpenAI and Ollama inbox allows 128 messages per run and each consumed
batch at most 32 messages / 256 KiB; native Codex follows its active-turn transport.

Native Codex uses its declared active-turn steering transport. OpenAI direct and
Ollama use a service-owned inbox and `RunContext.guidance` boundary callback.
The current response/tool finishes normally. Before the next model request,
accepted pending text is appended in order. Unstarted tools in a returned batch
may receive paired `DEFERRED_FOR_USER_GUIDANCE` outcomes so the model can replan.
An in-flight provider response is not rewritten and no private reasoning is
exposed. A final-response seal closes admission atomically only when the inbox
is empty; otherwise the next request consumes its pending guidance.

Delivery is independently recorded in `steering_requests` and sequenced events:
`steering.requested`, `steering.submitted`, `steering.accepted`, `steering.failed`.
The existing table states remain pending/accepted/rejected/unknown; result JSON
carries queued/submitted/provider_accepted/not_delivered/unknown. A queue receipt
has `accepted:false`: this is registration, not provider acceptance. Taking a
batch records exactly one `message.user` per request, with source `steer`.
Provider acceptance establishes transport/response evidence, not comprehension.
Terminal cleanup settles unused text as not delivered and uncertain submission
as unknown. Restart, cancellation and transport uncertainty never replay it.

The composer uses **Invia indicazioni** for an eligible active turn in its own
chat. **Accoda** remains a separate task; attachments use that route. Another
chat's active run is never selected for guidance. The GUI preserves request ID,
run ID and draft fingerprint across reconnect/reopen, merges pending and delivered
rows once, and does not erase text typed while an earlier send is awaiting a reply.

## Ordinary conversation alongside a Team

One active Team may retain exclusive write ownership while one ordinary chat in
that exact project runs with effective `read_only` permission. The exception
requires the same canonical project root and no unrelated overlapping work.
A second ordinary run or another Team remains excluded. Read access is not a
filesystem snapshot: a Team can change files while this conversation reads them.

The ordinary run receives the selected model/account/effort and an explicit
read-only lane for its full lifetime, including after the Team ends. File writes,
arbitrary host commands, project-tool execution, browser mutations and new Team
delegation/resume are blocked. Recorded Team status and mailbox coordination are
available when supported; a mailbox receipt alone never establishes execution.
Explicit new write tasks remain in the project queue. The stored project
permission is unchanged. The reader reservation persists until actual cleanup,
so a new writer cannot enter while that conversation is still active.

`assistant.snapshot.environment.conversation` explains admission and effective
read-only state. Ordinary run metadata includes `execution_lane`,
`related_team_id`, `effective_permission` and `source`. `team_id` on a run remains
reserved for a managed member; ordinary followups must remain visible in chat.

## Automatic result continuation

New chat delegations/resumes arm one durable receipt per Team. Manual
`team.start` and `team.resume` may supply `origin_session_id`: it must be an
ordinary, nonarchived chat in that project, validated again after awaited
preparation. Without an origin chat, a manual Team has no automatic followup.
The receipt freezes the originating provider generation, account, model, effort,
settings, project root and permission. No historical receipt is synthesized.

Confirmed Team cleanup makes completed, failed or paused results eligible. The
service waits for current chat work and project reservations to release; explicit
queued requests, including paused ones, have priority. It rechecks scope after
account preparation and claims the receipt in the same transaction that creates
the new ordinary run. Duplicate completion notifications cannot start another run.

The resulting `team_followup` run receives bounded original objective, newer user
messages, actual Team state/summary and run/event evidence references. Completed
execution does not certify objective achievement. A completed Team permits
continuing the already authorized task; failed or paused work is explained
without automatic retries of uncertain effects. This result turn cannot delegate
or resume another Team recursively. It uses the original chat binding and current
user corrections, not a different member's model or authority.

A service `backend.notice` starts the transcript, never a fabricated user message.
History, knowledge anchors and export retain service provenance. Provider history
uses explicitly marked service data in ordinary data roles, never promoted system
instructions. `assistant.snapshot.followups` exposes receipt state, waiting reason
and the actual run metadata; delegation summaries also include their receipt.
Manual Team events include `team.followup`. The GUI observes/subscribes to that
real run, keeping the Team result distinct from Kira's response state.

Receipt states are armed, pending, running, completed, failed, interrupted,
cancelled or suppressed. Explicit chat Stop suppresses still-pending automatic
responses for that chat. Team cancel or explicit new continuation suppresses
superseded receipts. Changed provider binding/permissions, archived chat,
uncertain cleanup and cancelled/interrupted Teams prevent automatic execution.
Restart reconciles terminal evidence but never replays unfinished or pending
result turns. Followup admission also participates in shutdown/settings guards.
