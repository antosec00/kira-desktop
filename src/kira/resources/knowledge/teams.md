# Managed teams, tasks and continuation

Kira schedules managed project teams. Roster entries and mailbox messages are
context, not executable assignments. A message sent with `team.send_message` or
`kira.team_message` does not start a member, create a task or resume a stopped team.
An empty mailbox cannot gain worker results while the coordinator is still
preparing its first plan. Return the plan instead of repeatedly polling messages.

## From a plan to verified work

1. The coordinator returns the structured final plan with `decision: "continue"`
   and concrete `tasks`. Each task names an admitted `member_id`, instructions,
   access and dependencies. In initial automatic composition, also return the
   roster including `coordinator`; manual and later plans return `members: []`.
2. After that coordinator turn finishes and cleanup is confirmed, the service
   validates the plan and schedules the assignments. A narrated intention or a
   recorded message is not evidence that an assigned worker ran.
3. Workers receive their own tasks and the recorded results of completed
   dependencies. Check actual task/run states, reports and evidence. A provider
   turn completing does not itself prove the task's objective was achieved.
4. The next coordinator turn reviews those results and returns another plan:
   continue with further tasks, ask for needed input, or complete with an
   evidence-based summary. Final `completed` and `needs_input` plans have no tasks.
   Required worker execution cannot be replaced by the coordinator's conclusion:
   Kira blocks a completion that has no required worker execution behind it.
   Failed or uncertain assignments must remain visible, not narrated as success.

The `round_limit` counts coordinator turns, including the final review, not worker
tasks, mailbox reads or model reasoning. Its default is **3**. Use at least **2**
for one assignment plan and one final review; further work can need more. A limit
of **1** can pause after workers finish, before the final review. Zero means no
automatic round ceiling. Preserve the chosen limit; no implicit limit override
occurs. An exhausted limit pauses the team and requires explicit continuation.

## Starting and continuing from an ordinary chat

Use `kira.team_delegate` to queue a new managed team with a concrete goal, brief,
scope and success criteria. Manual composition preserves the requested roster;
automatic composition lets its coordinator choose within the configured cap.
The team starts only after the originating chat turn completes successfully and
its cleanup is confirmed. Do not wait for the team inside that originating turn.

Use `kira.team_status` to inspect recorded state and results. To continue an
existing finished or paused delegation, use
`kira.team_resume({"delegation_id":"RETURNED_DELEGATION_ID","instruction":"Review the recorded outcomes and finish the remaining authorized work."})`.
Use an actual delegation ID returned for this chat; `instruction` is optional.
When the user requests a different coordinator limit, supply the optional
`round_limit` integer (0–10,000). Explicit `0` removes the round ceiling for the
new continuation. Omit it to inherit the source team's current limit. This choice
does not change the original team, model, permissions or recorded results.
This continuation is also deferred until the originating turn completes
successfully and cleanup is confirmed. Return to the user after queuing it.
The user can also continue from the interface with **Continue Team / Continua team**.
The chat's Team details and the Team view expose **Rounds on resume / Round alla
ripresa · 0 = illimitati** before resuming. Editing that field alone starts no work.

Continuation preserves original results and links the new team to its predecessor.
It is not a replay of prior tasks: uncertain external effects must be inspected
before deciding what further work is safe and authorized. A mailbox message alone
cannot provide this continuation. Provider, model, reasoning effort, project
permission and scheduling exclusions remain in force. Inspect the actual
registered capabilities before using these tools; reading this guide does not
enable a missing provider transport or grant permission.

## Talking while a Team works and receiving its result

Each delegation arms one automatic result response in its originating chat.
Return from the delegating turn; do not poll for the Team before it can start.
When the Team settles and cleanup is confirmed, Kira receives actual outcomes
and evidence references. A busy chat or explicit user queue delays this response.
Failed or paused outcomes remain incomplete and uncertain effects are not retried.
The automatic result turn cannot create or resume another Team recursively.

A separate ordinary conversation may run alongside the Team in the same project.
Its effective permission is read-only for that full turn; inspect registered
capabilities and explain that write tasks belong in the queue. Team status and
mailbox messages can coordinate work, but a message does not schedule a new task
or prove the Team applied an instruction. New user corrections take precedence
over the original historical objective. Cancellation, changed provider scope or
service restart never cause an uncertain followup to replay automatically.
