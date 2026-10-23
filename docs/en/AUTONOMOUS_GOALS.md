# Autonomous goals

*[Leggi in italiano](../it/OBIETTIVI_AUTONOMI.md)*

An autonomous goal is a result that Kira pursues on its own, cycle after cycle,
in the chat where you declared it, without you having to press **Continue**
after every reply. This guide explains the difference from a saved goal, the
`/obiettivo` commands, what a cycle does, when Kira stops and how to resume.

## Saved goal and autonomous goal

Open **Actions → Chat goal** and describe the desired result. From here you
can do two different things:

| | Saved goal | Autonomous goal |
| --- | --- | --- |
| How it is activated | **Save without starting** | **Start autonomously** or `/obiettivo …` |
| What Kira does | Includes the text in the context of every new turn | Starts new cycles on its own until the goal is reached or it stops |
| How it proceeds | **Continue with one step** starts a single explicit request | Every cycle starts automatically after the previous one |
| Controls | **Pause goal**, **Resume goal**, **Mark completed** | **Pause goal**, **Resume autonomously**, `/obiettivo pausa|riprendi|stato` |
| Status shown | **Saved goal · manual start** | **Autonomous goal active** and the other statuses described below |

A saved goal is passive: it never starts on its own, neither after a save nor
after a resume. **Mark completed** records your assessment; it does not
replace **Stop** for a turn already started and does not erase the context the
provider has already received.

## The `/obiettivo` commands

In the composer you can use these commands, which Kira interprets as
application commands and does not send to the provider; `/obbiettivo` and
`/goal` are equivalent aliases, and the English forms `status`, `pause` and
`resume` work with each of the three, for example `/goal pause`.

| Command | Effect |
| --- | --- |
| `/obiettivo <text>` | Starts an autonomous goal with that result |
| `/obiettivo` (without text) | Opens the goal controls |
| `/obiettivo stato` | Shows progress, cycles and the reason for any wait |
| `/obiettivo pausa` | Prevents further cycles; the operation in progress may finish |
| `/obiettivo riprendi` | Reactivates a stopped goal after the necessary checks |

Send the command without attached images (the files stay in the draft); it is
not available in an archived chat. From the interface and with `/obiettivo` a
goal starts with no cycle limit and no implicit time limit. A cap can be set
only with the `goal.start` IPC method (`max_cycles`, see
[Contracts](CONTRACTS.md)) and is not a promise that the goal will be reached
within that number. The panel shows **Cycles: N · unlimited** or **Cycles:
N/limit**.

## What a cycle does

Every cycle is an ordinary turn in **Execute** mode in the same chat, with the
provider, account, model, reasoning and project permission you chose.
Autonomous mode grants no additional permissions on files or the browser and
does not bypass approval requests: if a cycle needs a consent, it waits for
you.

At every cycle Kira receives the goal, a compact progress summary, the planned
next step and references to the records kept in previous cycles; your
corrections stay in the conversation and take precedence. When details are
needed, Kira consults the chat history with its own tools. The summary is a
statement by the model: it helps to keep the thread, but it does not replace
the original records nor guarantee a perfect memory. In the history a cycle is
introduced by a service notice, not by a made-up user message. A completed
turn allows another cycle, but does not by itself complete the goal: the two
states stay distinct.

### When a cycle waits

Only the service creates cycles, and before starting one it checks that the
project is free. The panel explains the wait:

- **Waiting for project work**: another job or a project reservation is still
  active.
- **Waiting for requests already queued**: the requests you queued, even when
  paused, take precedence over autonomous cycles.
- **Waiting for browser control to return**: you took manual control of the
  browser; the cycle starts after **Return to Kira**.
- **Waiting for Team results** / **Waiting for the Team summary**: a delegated
  Team finishes and delivers its result in the chat before the next cycle,
  which then decides the following step. A paused Team is not resumed by the
  cycle on its own.
- **Waiting for the chat turn**: you are conversing and Kira is replying.

## Progress summary and completion

The **Chat goal** panel shows status, cycle count, last summary
(**Progress**), **Next step**, **Recorded evidence** and the reason for any
stop, regardless of the state of the turn in progress.

At every cycle the model records a checkpoint with a summary, a next step and a
decision: continue, completed or blocked. To declare completion it must justify
it with results recorded in the project (events, artifacts, tool outcomes) or,
for a purely writing goal, with an explicit written deliverable; proof taken
from other projects does not count, and neither do the copies of Kira tool calls
that Codex and Claude Code also record. Completion is applied only after the
turn has ended successfully and cleanup is confirmed: a failed turn does not
turn a proposed conclusion into a success. The status **Goal completed ·
assessed by the model** says exactly that: Kira checks provenance, execution and
consistency of the records, but cannot guarantee that the model understood
everything or that every real requirement is satisfied. Verify the results
before considering the work closed.

## Blocks, pause and resume

A goal stops and says so (**Autonomous goal blocked**, with the reason) when
permissions are missing, when a decision from you or an external requirement
is needed (browser consent, manual sign-in, approval of a command); when the
provider fails for authentication, quota or protocol reasons, or the cleanup
of a turn is uncertain; when several consecutive cycles produce no useful
recorded progress (**A change is needed to continue**, instead of an endless
stream of continuation text); when the cycle limit, if set, is reached
(**Cycle limit reached**).

**Pause goal** (or `/obiettivo pausa`) prevents new cycles and lets the
operation in progress finish. **Stop** interrupts the current turn and also
pauses future continuations. A Team started in the meantime keeps its own
controls.

**Resume autonomously** (or `/obiettivo riprendi`) is a new explicit request:
Kira repeats the admission checks and restarts from the last checkpoint. If in
the meantime you changed the model, account, folder or project permission, the
new configuration applies only after this resume; to change the identity of a
chat with an active goal, pause it first.

Closing the window does not erase the goal: it belongs to the service. After a
service restart Kira keeps the goal, the checkpoint and the records, but does
not repeat a cycle left half-way or with an unknown outcome: the status becomes
**Autonomous goal interrupted** with **Resume explicitly after restart**. A
repeated command does not create a second goal or a duplicate cycle.

## Supported providers

| Provider | Autonomous goals |
| --- | --- |
| **Codex** | Yes |
| **Direct OpenAI · ChatGPT** | Yes |
| **Ollama** | Yes |
| **DeepSeek Account** (experimental) | Yes (checkpoints use the tools managed by Kira) |
| **Claude Code · personal use** (experimental) | Yes |
| **DeepSeek Web** (experimental) | No |

An old preference that disables Ollama's tools does not disable the tools
managed by Kira that DeepSeek Account uses.

## Tips for continuous research goals

- **Give an explicit success condition.** "Study the authentication module"
  never ends; "List every point where the authentication module reads
  external input, with path and line, and stop when the list covers all the
  files in `src/auth/`" has a finish line. Without a success condition, Kira
  stays active until you pause it or it hits a block, and does not consider
  the research finished just because a reply has ended.
- **Ask for records, not impressions.** Instruct Kira to keep findings in
  project files or in the [Research](RESEARCH.md) registry, so that the
  summary between one cycle and the next rests on something that can be
  re-read.
- **Check `/obiettivo stato`.** If you see **A change is needed to continue**,
  add guidance in the chat and resume.
- **Review progress at intervals.** Pause with `/obiettivo pausa` to check
  the progress before spending more provider quota; one chat, one goal.

For Teams and their automatic reply see [Teams](TEAMS.md); for the other chat
controls see [Chat and tools](CHAT_AND_TOOLS.md).
