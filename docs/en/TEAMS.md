# Agent Teams

*[Leggi in italiano](../it/TEAM.md)*

A Team is a group of agents that Kira coordinates on a project to reach a
shared goal: a coordinator plans and assigns tasks, the members carry them out,
and everyone communicates through a message inbox managed by Kira. This guide
is for anyone who wants several agents to work together on a project and wants
to understand how they organize themselves, how to control them and where the
results end up.

## What a Team is and when to use one

An ordinary chat is a single agent working one turn at a time. A Team is useful
when the work splits into parts that can proceed in parallel or that call for
different skills: a test analysis, an independent review, a search through the
sources, a change to a module and its verification. The Team belongs to the
project: it works on the project's files, respects its permission and keeps
plans, tasks, messages and results in the **Team** view.

A Team pays off when the goal is concrete and verifiable. For a short question
or a sequential task an ordinary chat is simpler and cheaper.

### Supported providers

| Provider | Team |
| --- | --- |
| **Codex** | Yes |
| **Direct OpenAI · ChatGPT** | Yes |
| **Ollama** | Yes (tools depend on the chosen model) |
| **DeepSeek Account** (experimental) | Yes |
| **DeepSeek Web** (experimental) | No |
| **Claude Code · personal use** (experimental) | No |

Provider, model and reasoning level are chosen once for the Team and apply to
every member, coordinator included. No silent switches of model or account
happen during the work. The optional service tier concerns Codex only. With
Ollama, tool availability depends on what the model supports, and the
coordinator must be able to answer with a structured plan.

## Creating a Team from the Team view

Open a project and switch to the **Team** view. Press **New team**, write the
**Team goal** (describe the result to reach and how to verify it) and choose
how to compose the group:

- **Set up manually** (manual composition): you state the exact number of
  agents with **Total agents (including coordinator)** and, for each one, a
  **Name**, a **Specialization**, **Responsibilities and instructions** and
  **Read** or **Write** access. The first member is always the coordinator and
  is included in the total; it can also carry out tasks. The **Write** option
  appears only if the project permission allows it.
- **Let Kira organize** (automatic composition): you state only **Maximum
  agents (including coordinator)**. The coordinator reads the project context,
  composes the group within that maximum, proposes the tasks and starts the
  Team on its own after a valid plan. The composition decided in the first plan
  stays fixed for the whole Team.

In the **Model and limits** section choose the provider, model and reasoning
shared by the Team and the resource limits:

| Setting | Range | Default |
| --- | --- | --- |
| Total agents / maximum agents | 1-12 | — |
| **Maximum concurrency** | 1-8, never more than the agents | — |
| **Coordination rounds · 0 = unlimited** | 0-10,000 | 3 |

The round limit counts the coordinator's planning cycles, not the model's
reasoning or the tool calls. A plan accepts at most 24 tasks. Reaching the
limit pauses the Team with a reason and keeps everything; completion is never
declared because the rounds ran out. A planning cycle and a final synthesis
normally take at least two coordinator turns: that is why the default is three.

Finally press **Start team**. While the Team is working, the button shows
**Team running**.

The coordinator can also be the only member: a single-agent Team is valid and
useful when you want the discipline of task-based planning.

## Requesting a Team from the chat

With **Codex**, **Direct OpenAI · ChatGPT**, **Ollama** or **DeepSeek Account**
and tools enabled, you can ask for a Team directly in the conversation; with
**Ollama** the model must support tools. **Claude Code · personal use** and
**DeepSeek Web** do not run Teams, so their chats cannot request one. For
example:

> Organize a team of three members: a coordinator, a test analyst and an
> independent reviewer. Work read-only on the project files and report
> results, proof and still-open problems in the chat.

You can state the number and specializations or leave the composition to Kira.
The Team inherits provider, account, model, reasoning and effective permission
from the chat. The handover carries the goal, a short brief, the decisions
taken and references to the proof collected; the full chat history is not
repeated in every task.

The path has five phases:

1. **Preparation.** Kira prepares and records the delegation; the chat shows
   the waiting state (**The team will start after the current turn finishes.**).
2. **End of turn.** The current turn ends and the service verifies that the
   provider has really finished its work.
3. **Start.** The Team starts with the requested composition.
4. **TEAM card.** The chat shows progress in a **TEAM** card that updates:
   members, completed tasks, status. From there you can **Open Team** for the
   full view, write **Instructions for the coordinator…** and press **Send to
   team**, or **Stop team**.
5. **Automatic reply.** When the Team finishes, Kira replies on its own in the
   original conversation with the results, without you having to send another
   message (see below).

The **Plan**, **Review** and **Recap** modes stay read-only and do not start
Teams. An existing Codex chat can use the Team tools without losing its own
thread.

## How a Team works

### The coordinator plans

Every coordination round is a read-only coordinator turn: it reads the project
and the Team state and answers with a structured plan that Kira validates
before executing it. The plan contains a decision (continue, done, your input
is needed), a summary and a list of concrete tasks, each with a title, an
assigned member, instructions, the required access and dependencies on other
tasks of the same round. Dependencies must form an order without cycles.

A message in the inbox is not a task: the coordinator can send guidance to the
members, but no member starts until an assigned task exists. Querying idle
members does not advance the plan. Kira makes this step visible.

### Parallel reads, exclusive writes

Independent read-only tasks can proceed together, up to the chosen
concurrency. A task with write access works alone: no other reader or writer
in the Team is active until the provider process has ended and cleanup is
confirmed. An active Team excludes other writers and other Teams on the same
project folder.

### What each member receives

Each member receives its own task, the coordinator's guidance, the messages
waiting in its inbox and the recorded outcomes of the tasks it depends on
(final reports and references to the runs), after those have been completed
and cleaned up. The end of a provider turn does not by itself prove that the
task's goal has been reached: the next member verifies the real effects.

### The coordinator reviews

When the tasks of a round finish, the coordinator receives the actual
outcomes, the messages and the references to the proof and decides: it
concludes, asks for your input or assigns a new round. Failed or uncertain
work cannot be hidden behind a positive summary. A Team with several members
is not concluded without recorded outcomes from the collaborators.

If the coordinator concludes too early or returns an empty plan, Kira asks for
**a single** plan correction within the round limit already set; no round is
added silently. If the correction is not enough, or the limit is reached, the
Team pauses with the reason visible. Repeatedly empty plans or plans without
progress stop the Team instead of letting it spin. A provider error is not
retried automatically.

A conclusion without recorded member outcomes is flagged as **Conclusion
needs review**: the original data stays preserved and it is up to you to
judge.

### Roles are not a sandbox

Specialization, instructions and "responsibility for files" are
organizational context. A member's access is a maximum: **Read** really means
read-only; **Write** is worth as much as the project permission and no more.
With **Read only** no agent modifies files; with **Project write** writers
stay inside the folder; with **Trusted host** or **Full access** writers can
run commands as your user and, with full access, touch files outside the
project too. Planning and read-only tasks stay limited to the project in any
case. Details are in [Permissions and security](PERMISSIONS_AND_SECURITY.md).

A message from a colleague or the result of a tool cannot raise a member's
execution permissions.

## Messages between members

Each member has a message inbox managed by Kira and has two tools:
`team.send_message` to write to a specific colleague or to everyone, and
`team.read_messages` to read its own inbox during a turn. A message can
contain at most 8,000 bytes of text. Waiting messages are also delivered to
the member together with its next task.

You can write to the Team while it works too: in the **Team** view choose the
**Message recipient**, write in **Message to the team…** and press **Send**;
from the **TEAM** card in the chat, guidance goes into the coordinator's inbox
and does not start a new chat turn.

A message is recorded before delivery. Seeing it in the history means that
Kira has kept it and supplied it to the context, not that a model has read or
followed it: in the **Run details** events you can see whether a message was
delivered together with the task or read from the inbox during a turn. A
message sent to a concluded or paused Team stays recorded for the follow-up,
but does not restart the work on its own. Direct messages reach their
recipient; you, as the project owner, see the whole shared history.

## Controlling a Team

- **Stop team** requests that all tasks stop: no new task is scheduled and
  every child process is cancelled. Wait for the confirmation; until a process
  has really stopped, its write reservation stays active.
- **Pause.** The Team stops on its own, with a reason, when it reaches the
  round limit, when planning makes no progress (**The coordinator needs to
  assign work to members…**) or when the coordinator asks for your input. The
  outcomes stay preserved.
- **Resume team** (**Team** view: **Instruction for a new continuation…**
  field and **Resume** button; **TEAM** card in the chat: **Resume team**)
  creates an explicit continuation linked to the previous Team. It keeps the
  model, reasoning, account, constraints and previous results. **Continue
  team** opens a follow-up starting from an already concluded result, with the
  previous proof.
- **Rounds on resume · 0 = unlimited** sits next to the resume buttons, in the
  **Team** view and in the chat's **TEAM** card. It starts from the value of
  the selected Team; if you leave it alone, the continuation inherits that
  limit. The new value applies only to the continuation: the original Team and
  its results do not change. Changing the number starts nothing. With zero,
  the protections against lack of progress, uncertain outcomes and
  unauthorized operations stay active. You can also write it to Kira: "Resume
  this Team with cycle limit 0".
- From the chat you can ask "Resume the Team using the previous results":
  Kira queues a continuation linked to the chosen Team and starts it after the
  current turn. Duplicate requests do not create further copies of the Team.
  If a resume is left without a confirmed outcome, the next attempt reuses the
  same identity and the same limit (**Resume outcome needs verification: retry
  with the same limit.**).
- **Approvals.** When a member asks for an approval (for example a command
  outside the sandbox), the request appears in the **Team approval queue**
  with the member and the task it belongs to; you decide with **Allow**,
  **Decline** or **Cancel request**. A request too large for the preview can
  be declined or cancelled, but not granted.
- **Closing the window** does not stop the Team: the work belongs to the
  service and continues. Shutting down WSL or the system, however, can
  interrupt it. An interrupted Team stays visible with its status and waits
  for an explicit continuation.
- **No automatic repeats.** After an interruption or a resume, previous tasks
  are not re-run on their own and operations with an uncertain outcome are not
  repeated. If the result of a command is doubtful, check the Team history
  before requesting another operation.

## Talking to Kira while the Team works

While a Team holds write control of the project, you can keep talking to Kira
in the chat of the same project: **Read-only conversation** appears (and, at
the top, **Team working · you can keep talking to Kira**). In this
conversation Kira reads the files, consults the recorded state of the Team and
its message inbox, and answers your questions.

The following are blocked: file changes, commands on the host, running
project tools, actions that change the browser, and starting or resuming
other Teams. The conversation keeps these limits until it ends, even if the
Team finishes in the meantime. Only one conversation of this kind is allowed
per project; a second one, or another Team on the same folder, waits. Reading
is not a snapshot: the Team can change the files while the conversation reads
them.

For a new task that requires changes use **Queue**: the request enters the
project queue and starts when the Team has finished. The project's saved
permission does not change.

## Automatic reply with the results

For Teams with an originating chat, the result triggers a reply from Kira in
that conversation on its own, without you sending another message. The
originating chat is the one where you asked for the Team or, for a Team started
or resumed from the **Team** view, the ordinary chat open at that moment;
without an originating chat there is no automatic reply.

- **When it starts.** After the Team's confirmed cleanup, when its status is
  concluded, failed or paused. If Kira is still replying in the chat, the
  reply waits for that turn to end. Explicitly queued requests, even when
  paused, take precedence.
- **What it contains.** The original goal, your most recent messages, the
  Team's actual status and summary and references to the recorded runs and
  events. The reply distinguishes task completion from goal verification: a
  concluded Team allows the already authorized task to continue; a paused or
  failed Team is explained without repeating uncertain effects.
- **What it does not do.** It does not start or resume other Teams. It uses
  the provider, model and account of the original chat, not those of a
  member.
- **Stop** in the chat also cancels the automatic replies still waiting for
  that chat. Stopping the Team or starting a new explicit continuation
  replaces replies that have become obsolete.
- **Conditions that prevent it.** A change of provider, model, account or
  permission since the Team started, an archived chat, uncertain cleanup, a
  cancelled or interrupted Team. After a service restart a reply that has not
  started yet is not replayed: Kira shows the preserved state and waits for an
  explicit continuation from you.

In the history the reply is introduced by a service notice, never by a
made-up user message; exports and memory keep the same provenance.

## Run details

From **Team → Run details** (also **Details** in the **TEAM** card) you read
the events recorded by the service in order, with sequence number, type and
time in the recorded time zone. The full timestamp appears when you hover over
the time. Tool outcomes are kept distinct from model messages.

- **Readable view** indents nested structures and wraps the text of messages
  and context; the `|` symbol introduces a multi-line text block. It is a
  presentation for reading, not a serialization.
- **Original JSON** shows the payload as it was recorded.
- **Copy JSON** always copies the original payload, even from the readable
  view. A shortened preview is flagged and copying is disabled, because an
  incomplete JSON cannot be copied.
- **A−** / **A+** enlarge or shrink only the text of this window.
- **−** / **+** collapse or expand a single event. **Load more events**
  continues reading.

The window shows up to 600 events per run and payload previews of up to
131,072 characters. Nothing you see here modifies the recorded data, the runs
or the provider settings.

## Limits and good practices

- **Write concrete, verifiable goals.** "Improve the code" produces vague
  plans; "Find the causes of the failing tests in `tests/` and propose a fix
  for each, without modifying files" produces precise tasks.
- **Ask for proof, not summaries.** Instruct members to report paths, commands
  run and observed results. The coordinator's summary should be read together
  with **Run details**.
- **Check the files yourself** after a Team with write access. A completed
  turn is not a reached goal; **Review** in the chat helps you examine the
  differences.
- **Keep conclusions in Research.** What a Team discovers stays in the events;
  to avoid losing it, create a record in the registry and link the Team's runs
  as sources (see [Research](RESEARCH.md)).
- **Size the Team.** More members do not mean more quality: every member
  consumes context and provider quota. Start with two or three agents and a
  low round limit; raise the limit on resume if needed.
- **One service, one project.** External editors or a second Kira service on
  the same folder are outside the write coordination: avoid editing files by
  hand while a writer is active.

For the technical reference of the Team methods and events see
[Contracts](CONTRACTS.md); for the goals Kira pursues on its own, which can
wait for a Team's results, see [Autonomous goals](AUTONOMOUS_GOALS.md).
