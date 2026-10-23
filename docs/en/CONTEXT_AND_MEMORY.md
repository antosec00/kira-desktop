# Context and memory

*[Leggi in italiano](../it/CONTESTO_E_MEMORIA.md)*

This document explains what Kira gives the model at every turn, how the
project memory and the chat notes combine with the provider's context, how
automatic and manual compaction of long conversations works and how the model
can re-read the original history. It is aimed at anyone who uses Kira for work
spanning days and wants to know what the model really "remembers".

## 1. What the model receives at every turn

Besides the text of your request, Kira prepares at every turn a context made
of:

- the **project instructions**, written by you in **Project memory and
  instructions**;
- a reference to the **Project memory**, the JSON you curate (section 2),
  which the model reads with the lookup tools when needed;
- the **Important chat notes** you have kept (section 4);
- the chat's **saved goal**, while it is active (see
  [Chat and tools](CHAT_AND_TOOLS.md));
- the **excerpts from the other chats** of the same project, if enabled
  (section 3);
- the **core of the Soul** chosen for the chat (name, philosophy up to 1,200
  bytes and preferences; see [Soul and knowledge](SOUL_AND_KNOWLEDGE.md));
- Kira's common identity, with the turn's effective capabilities and
  permissions;
- the conversation's **portable context**, when the provider does not keep a
  thread of its own or when you have changed provider in the same chat
  (section 5).

Instructions and notes are binding input, supplied in full. Large material
(project memory, Research record, Kira manual, tool catalog, chat history) is
instead reachable by the model with searches and paged reads (section 8), so as
not to fill the context with what is not needed: a model without tools does not
see it. Team members receive the project memory in full. A compact anchor
reminds the model of the current request and the saved goal, if any; a
historical request never becomes a permanent goal, and a later correction of
yours prevails.

To see exactly what Kira added to the last run open **Context supplied by Kira**
(in the **Details** panel, or with **Supplied context** in **Project memory and
instructions**): it shows Kira's contribution, with the **Copy context** button.
The provider's internal prompt may contain other non-observable context, and
this view does not expose the model's private reasoning. From the **Research**
panel, **Read shared context** shows the accepted records the assistant can
consult with its tools; they are not added to the supplied context.

## 2. Project memory

**Project memory** is an editable JSON text, one per project, with fields for
**goal**, **decisions**, open **questions** and **next step**. You edit it
from **Project memory and instructions** and save it with **Save**; **Export
JSON** writes it to a file.

Keep **hypotheses** separate from **conclusions** and link every conclusion to
a file, an event or an artifact: the model reads this text as context, not as
verified fact, and nothing you enter automatically becomes a confirmed
conclusion. It is the memory curated by you: the model reads it,
but does not rewrite it on its own.

The project instructions and memory are shared by all the project's chats and
by all Soul identities; changing provider in the same chat does not lose them.

## 3. Excerpts from the other chats

Chats have separate histories. To give continuity to the work, at every turn
Kira can add short **recorded excerpts** from the other ordinary chats of the
same project:

| Limit | Value |
| --- | --- |
| Chats excerpted from | at most 5, the most recent |
| For each chat | last completed run: up to 400 characters of request and 800 of response |
| Overall block | 6,000 characters |

Every excerpt carries the reference to the chat and the run. Extraction is
deterministic: no summarization call to the model and no copy of whole
conversations. When something is omitted because of the limits, the omission
is declared in the block.

Excluded from the excerpts: the current chat, **archived chats**, the managed
sessions of a **Team**, chats of **other projects** and chats that use
**another Soul identity**. File permissions do not change: it is a context
boundary, not a sandbox.

In the **Project memory** panel the switch **Use excerpts from this project's
other chats** is on by default and applies to the project. Turning it off
avoids new sends, but **does not retract** what a provider with a persistent
thread has already received in existing conversations. Earlier responses are
context, not verified facts: the block labels them as recorded requests and
assistant statements.

## 4. Important chat notes

In **Context and memory** (the **Context** button under the chat, or **Manage
chat**), the **Important chat notes** section lets you write a constraint or
a detail and press **Keep note**. Notes are supplied to the model in full at
every turn, even after a compaction, and with any provider; **Remove** deletes
a note without touching the original messages. The number and size of notes
have limits visible on save: no note is discarded silently. You can add or
remove notes while no work is running in the project and no requests are
queued.

What sets these tools apart:

| Tool | Scope | Who writes it |
| --- | --- | --- |
| Project instructions | All the project's chats | You |
| **Project memory** | All the project's chats | You (structured JSON) |
| **Important chat notes** | A single chat | You |
| **Automatically generated memory** | A single chat | The model, during compaction |

A note is for what must stay present in **this** conversation ("do not touch
the `legacy/` folder", "the customer wants dates in ISO format"); the project
memory for what applies in all chats.

## 5. Context per provider

Every provider manages working memory differently. The history Kira records
in the database is always complete, regardless of what the model receives.

### Codex

Codex keeps a **native thread** per chat: the conversation stays in the
provider and Kira only adds the turn's context to it. **Compact context** (in
**Manage chat**) asks Codex to compact its own thread; it requires an idle chat
and a confirmation. Kira waits for the provider's outcome and keeps its own
history. If Codex reports a native goal of its own still open, Kira may refuse
the resume or the compaction: it has to be resolved in Codex.

### Ollama

Ollama has no memory between one request and the next: Kira sends a **recent
window** of the chat, by default the last **8 runs** within **32,000 bytes**.
If you set a positive `num_ctx` in the model, the budget becomes **twice that
value in bytes**, between 4,096 and 64,000. It is a transport budget, not a
token count, and does not guarantee that the whole request fits in the model.
Omissions are flagged; instructions and notes are never cut by this window.
The rest of the conversation remains reachable by the model with the history
tools (section 8), if the model supports tools.

### Direct OpenAI · ChatGPT

The direct provider uses a **persistent context** managed by Kira for each
chat, account and model, rebuilt from the original messages and kept across
restarts. The setting is **Automatic — maximum available**: Kira plans
requests on the largest window declared by the catalog for that model and
reserves **18 %** of the window for response, reasoning and tools; when the
catalog declares an output limit, the reserve is that limit plus **3 %** of the
window, if larger. A missing capacity
stays unknown, and the catalog's declaration alone does not prove that a
request as large as the window works on that connection.

Before running out of space, even in the middle of work with tools, Kira
compacts. It prefers the provider's **native compaction**; if that is not
available it uses a **structured summary with sources**, produced by the
selected model and reasoning and identified as managed by Kira. The **Context
and memory** panel shows:

- **Reported default window** and **Catalog-reported maximum**: provider
  values, different across models and accounts;
- **Window used by Kira**, **Space reserved for response and tools** and
  **Compaction threshold**: the values it prepares requests with;
- **Last measured request input**: the usage reported by the provider;
- **Last context estimate** (local estimate) and, when the connection offers
  it, **Last provider preflight count**;
- under **Compaction**: last compaction, number of compactions and method.

Estimate and count describe the last available calculation, which may
precede the most recent response: they are not a prediction of the next send.
After a native compaction part of the context lives in an internal state
readable only by the provider: its size in bytes does not say how many tokens
it occupies. Without a complete count, **Last context estimate** stays **Not
available** and **Last visible content estimate** appears, which does not
allow calculating the free space. Every request must also fit in 32 MiB
encoded, images included.

### DeepSeek Account (experimental)

With DeepSeek the mode is **Automatic — Kira compaction**: the server does not
shorten the history, Kira does. Before every request it estimates the pressure
on the window and calibrates it with the input tokens reported in the
responses. The threshold is the lesser of **80 %** of the model's window and
the window minus a reserve for the output limit plus a **65,536 token** margin;
the reserve never exceeds half of the window, so models with small windows keep
at least half of it for input. Compaction
folds the oldest runs and keeps verbatim at least the most recent **16 %** of
the usable window. The summary uses the selected DeepSeek model and account,
**without tools and without reasoning**, in blocks, validates the source
references and saves a checkpoint only if it reduces the context; it uses
account quota and may add waiting time. If the model data comes from the
fallback catalog, Kira limits the window to **256K** and flags it as
unverified. If the provider responds with an **HTTP 400 or 413** that
explicitly indicates a full context, Kira compacts **once** and retries that
request; other errors are not retried.

### Claude Code · personal use (experimental)

The chat corresponds to a **Claude Code session** that Kira resumes at every
turn: working memory is managed by Claude Code, while its automatic memory is
disabled and replaced by Kira's project memory, notes and Research. After a
provider change the first turn receives Kira's portable context. In **Usage
and resources** you find, for every turn, the turn's tokens, those of the last
request and the **context window occupancy** reported by Claude Code.

## 6. Context and memory: compacting

Open **Context and memory** from the **Context** button under the chat, from
**Manage chat** or from the **Usage** panel. With Direct OpenAI and DeepSeek
Account, **Compact now** starts an explicit compaction: it requires an **idle
chat** and an **empty queue** (complete or remove the waiting requests, even if
the queue is paused). During the operation the chat's button shows
**Compacting…** and the panel updates the status while it is open; closing it
cancels nothing. Compaction uses the selected model, may consume plan quota and
add waiting time, and does not modify the project files. If the confirmation is
missing, press **Refresh**: Kira keeps the request identity and does not repeat
an inference on its own.

If the native compaction's response is unusable (an index or format the local
reader does not accept), Kira shows **Recovering context**, discards that
response and produces a structured summary with the same model and reasoning.
For that context, native compaction is marked as unavailable until a later one
succeeds. If the summary fails as well, the previous checkpoint and the
originals remain valid and the error is shown. Authentication, quota, network
or cancellation errors do not trigger this recovery.

Some guarantees always hold:

- **recorded commands are never re-run** to rebuild the context: historical
  tool calls are inert text;
- the chat's original archive is neither deleted nor modified;
- Kira's limits are in **bytes**, the model's in **tokens**: cached tokens
  still occupy context and the chat's cumulative totals do not indicate the
  free space;
- **image** estimates are approximate; current images take priority and
  historical ones are re-added in order when they fit in the 16 MiB budget,
  with explicit notices for those missing or over budget;
- changing model, account or provider requires a compatible context and may
  involve a new preparation from the originals;
- a service restart leaves an unfinished compaction in an unknown state,
  without repeating it.

**Recap** (request mode) produces a readable response and does not replace
these operations. No compaction guarantees the perfect preservation of every
detail.

## 7. Automatically generated memory and Read source

Still in **Context and memory**, **Automatically generated memory** lists what
compaction kept in readable form: goals, constraints, decisions, corrections,
recorded results, open issues and next steps. Every entry is a **statement by
the model**, even when it reports a result: the source reference proves where
it comes from, not that the model interpreted it correctly. A later correction
prevails over outdated information.

**Read source: …** opens the original passage an entry refers to; long
contents are read with **Previous page** and **Next page**. The provider's
opaque states are never shown as memory text and do not appear in the export.
The full original history remains exportable with **Export chat as Markdown**.

## 8. History the model can consult

During a request the model can search and re-read the current conversation on
its own with `kira_history.search` and `kira_history.read`, and the manual,
the project memory, the Research record and the tool catalog with
`kira.help_search` and `kira.help_read`. Both are read-only tools, bound to
the turn's chat: the model cannot point to another chat or another project.

The search is **literal** (Unicode, with no semantic search and no external
services) and returns limited pages with a cursor to continue; the read keeps
the source identifier, the originating run and the offset. The index includes
the user's and the assistant's text, the partial outputs left visible, the
recorded tool arguments and results and the questions with their answers; it
distinguishes a requested tool from an executed one and a saved answer from a
delivered one. It does not include the model's private reasoning or the
provider's opaque state; material already truncated at recording time is
declared as such.

There is no guarantee that the model recalls every useful piece of data on its
own: if an earlier decision matters, tell it which message to check or save it
as an important note.
