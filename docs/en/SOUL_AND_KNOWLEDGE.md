# Kira's Soul, knowledge and project tools

*[Leggi in italiano](../it/ANIMA_E_CONOSCENZA.md)*

This guide explains how Kira presents itself to the model, how to create
personal identities with **Kira's Soul**, how to consult what the model can
know through **Knowledge and tools** and how the project's reusable tools work.
It is for anyone who wants to adapt Kira to their own way of working without
losing control of what the model receives.

## Kira's shared identity

Kira is the identity of the assistant inside the app, independent of the
provider and model you choose. At every request the model receives short
instructions about three things: how to know its own environment (project,
chat, available capabilities), how to retrieve useful sources with the lookup
tools and how to verify results before stating them. These instructions
encourage initiative and verification without attributing to the model
capabilities it does not have.

Two points stay fixed:

- **Provider, model and reasoning level are your choices.** The identity does
  not change them: you set them from the **model** chip in the composer (see
  [Providers](PROVIDERS.md)).
- **The identity adds no capabilities.** If the provider does not support a
  tool (for example images or the browser), Kira cannot use it. A "chat only"
  model keeps the shared identity but cannot look anything up.

The project instructions and the **Important chat notes** stay in the initial
context of every turn. Large material (guides, project memory, Research
registry, tool catalogs) is not loaded all at once: the model searches it and
reads it in pages when needed. A compact reference indicates the current
request and any saved goal; the first request of the chat never becomes a
permanent goal, and later corrections always take precedence.

## Kira's Soul: personal identities

Beyond the shared identity you can create one or more personal identities,
called Kiras or "souls". Each one has a name, a working philosophy written by
you, structured preferences and, if you want, a memory of lessons learned with
sources.

Press **Soul** in the tool tray below the composer, or open **Settings → Work
→ My Kiras**. The panel opens on the right in the **Soul** tab; the editor's
draft survives closing the panel and switching chats.

### Creating an identity

1. Press **Create a Kira**.
2. Choose a **Name** (up to 96 UTF-8 bytes).
3. Write the **Philosophy and collaboration style**: it is the "core" of the
   identity, up to 1,200 UTF-8 bytes. Describe what matters in the way you
   work together, for example testing hypotheses before concluding or
   adapting the depth of explanations to the topic. The core describes habits
   and priorities; it cannot expose the model's private reasoning.
4. Fill in, if you want, the **Explicit preferences**: **Tone**, **Detail**,
   **Collaboration** and **Response language**, each up to 160 bytes. An
   empty preference removes the customization of that field.
5. Press **Create identity** (then **Save identity** for later changes).
6. Press **Use in this chat** to apply it to the open chat, or **Use in the
   new chat** when you are about to open one. **Kira without
   personalization** removes the selection.

In a new chat choose the identity before sending the first message: the choice
is kept together with the draft, even if you close and reopen the window.
Existing chats do not receive an identity until you choose one. You can have
several Kiras for different purposes, for example one for study and one for
design, and choose a different one for each chat.

### What the model receives

The short core and the preferences are supplied to the model at every turn,
even after a context compaction; the lessons learned are searched and read
when needed. **Preview** shows the exact text that will be sent and its size
in bytes: it is not a count of tokens or of the whole context. If the name,
the core or a preference exceeds the limit, the save is rejected with a
visible message: nothing is summarized or truncated.

Preferences are general settings: a specific request always prevails. If you
set "concise answers" as the default detail, you can still ask for an in-depth
analysis in a single message. Likewise, the project instructions and the chat
notes, when specific, take precedence over the identity's preferences.

Editing an identity never grants new permissions or tools: Kira's operating
instructions, the project permissions and the browser consent stay separate.
Changes apply from the next turn; a turn already started keeps the version it
had received.

## How an identity evolves

In the **How it evolves** section choose the **Learning mode**:

| Mode | What happens |
| --- | --- |
| **Manual** | Only you change the identity and preferences. The model cannot propose lessons. |
| **Suggestions** (default) | Kira can propose what it could learn from a message or a result in the chat. You decide what to keep. |
| **Adaptive** | Like Suggestions, plus the `/anima preferenza` command automatically applies an explicit preference. Every change stays tracked. |

In no mode does Kira start an extra model or a background analysis: proposals
arise only during a normal turn with tools enabled, and ordinary natural
language does not become a new personal rule.

### What it learned

The tab has two parts. **Suggestions and decisions** lists the
interpretations proposed by the model in the current context (chat or
project). Every proposal reports the statement, what it applies to, the scope
and a **Source**: your message or the tool result it was drawn from. Open the
source, read it with **Continue reading** if it is long, then choose **Keep**
or **Dismiss**. A proposal can ask to apply to the whole project: by accepting
it, the lesson applies in all the project's chats with the same identity.

**Saved experience** lists the accepted lessons. For each one you can:

- open the original **Source**;
- mark it **Helpful** or **Not helpful**: the rating (from −2 to +2)
  influences the order in which relevant memories are recalled; it does not
  make a lesson true or false, and merely reading a memory does not reinforce
  it;
- press **Forget** to exclude it from future turns; source and history stay
  preserved.

Every identity keeps at most 512 active lessons and 512 pending proposals: at
the limit the panel says so, without deleting data. Search is textual.

### The `/anima preferenza` command

In any chat with a selected identity you can send a message made up **only**
of this command:

```text
/anima preferenza tono=naturale e diretto
```

Rules of the command:

- It must take up the whole message, with no extra lines. A command quoted
  inside a text, a tool result or a service message is not recognized.
- The accepted keys are `tono`, `dettaglio`, `collaborazione` and `lingua`;
  the English equivalents `tone`, `detail`, `collaboration` and `language`
  work too.
- In **Adaptive** the preference is applied immediately, from the turn in
  which you send it. In **Suggestions** it generates a proposal to assess in
  **What it learned**. In **Manual** it changes nothing.
- The message remains a normal provider turn: it is sent to the model and can
  consume your account's quota like any other request.

The **Explicit preferences** fields of the panel stay available in every mode
and do not go through the model.

### Evolution and restore

Every save, accepted preference and decision on a lesson creates an immutable
revision. The **Evolution** tab lists them; **Restore this version** brings
the core, preferences and active lessons back to the state of that revision,
creating a new revision without rewriting the history and invalidating the
pending proposals born afterwards. If a revision more recent than the one you
were working on exists, the editor keeps your draft and asks you to compare it
before saving.

### Scope of lessons and sources

- A lesson belongs to the chat in which it was born. It extends to the project
  only if you accept a proposal with that scope, and applies only to the same
  identity. There is no automatic transfer of lessons between projects.
- Every lesson is linked to its original source (a message of yours or a tool
  result in the chat): the link says where an interpretation comes from, it
  does not make it correct.
- Reasoning summaries and the model's statements about itself are not valid
  sources, and a recalled memory cannot confirm itself, not even through the
  copy of a Kira tool call that Codex or Claude Code records.
- Preferences and lessons are not proof of work done or progress on a goal:
  they describe how to collaborate, not what has been done.

## Switching identity, copying and archiving

### In an existing chat

To choose another identity in a chat already under way, the project's work
must be stopped, the chat must have no queued requests and any autonomous goal
must be paused (see [Autonomous goals](AUTONOMOUS_GOALS.md)); the panel reminds
you with a note. The switch closes the current provider session and opens a
new one with the same provider, model and reasoning settings. It also closes
the chat's browser, keeping a retained profile; if you are controlling the
browser, return control to Kira or close it first.

The chat history remains: the switch is a style choice for the conversation,
not a deletion of what the model had already received. Automatic excerpts
between chats (see [Context and memory](CONTEXT_AND_MEMORY.md)) do not cross
different identities: a chat with identity A receives no excerpts from chats
that used identity B, nor from the turns of the same chat before the switch.
The instructions and memory explicitly shared by the project stay common to
all identities. For truly separate work, open a new chat and choose the
identity there.

### Copy profile, Import profile, Duplicate, Archive

- **Copy profile** copies the profile of the selected identity to the
  clipboard; **Import profile** pastes it on this or another installation with
  **Create from profile**. The profile contains name, core, learning mode and
  preferences, **without** conversations, lessons, sources or previous
  revisions. Importing always creates a new independent identity.
- **Duplicate** creates a local copy with the same profile, from which to start
  a variant.
- **Archive** retires the identity: it will no longer be used in later turns
  and disappears from the active list, but history, sources and revisions stay
  available from **Evolution**, from which you can also restore it. Archiving
  deletes nothing from transcripts, backups or turns already delivered.

### What does not change

Selecting, editing or archiving an identity does not touch the provider,
model, reasoning level, project permissions, browser consent or autonomous
goals. [Team](TEAMS.md) members do not use Souls: they keep the role you
assigned them and receive neither the personal memory nor the proposal tool.
The identity applies to the ordinary chat with Kira.

## Knowledge and tools

Press **Knowledge and tools** in the sidebar, or **Knowledge** in the tray
below the composer. The **Kira · Knowledge and tools** panel has two tabs:
**Knowledge** and **Project tools**.

### The Knowledge tab

At the top you find, for the open chat, the **saved goal** (if there is one)
and the **current request**, kept distinct; the **Chat capabilities**, that
is, registered tools, permissions and what the provider has shown it supports
(unknown support stays unknown, it is not inferred); the number of
**Important notes** and the **Chat summary**, with **Read details** for the
full text; the available sources, with **Open source**.

The **Search the guide and memory…** field searches what the model could
consult: Kira's guides, the chat's original sources, the project memory, the
registered tools and, if there is an identity, its lessons. **Search scope**
narrows the scope: **All**, **Kira guide**, **Capabilities**, **Chat**,
**Project**, **Tools**, **Kira's Soul**. Every result shows title, type,
summary, revision and provenance; **Read** opens it in pages, in its original,
literal content.

Consulting the panel does not start the model and does not run tools: it is a
local read. The [Research](RESEARCH.md) registry is shared with the model only
if you turned sharing on for that project; otherwise it stays a registry of
yours, which the model does not consult.

### What the model can search

The model has two lookup tools, `kira.help_search` and `kira.help_read`, bound
to the chat in progress: it cannot point to another project or another chat.
Search is textual and limited; reading returns one page of a recognized
reference, never an arbitrary path or URL. The scopes are the same as the
panel's: `all`, `app`, `capabilities`, `chat`, `project`, `tools` and `soul`.

The procedures described in the guides are text: reading them runs no step.
The provenance of a source says where content comes from, not that the model
has verified a result.

## Project tools

When a capability is missing, you can ask Kira to prepare a reusable tool for
the project: a Python script that receives a JSON object on stdin and returns
on stdout a JSON value matching its declared output schema. Kira can also
propose one on its own during an authorized activity. Tools belong to the
project.

### The Project tools tab

The tab lists the registered tools with their current revision; **Search for a
tool…** filters the list. **Details** opens the tool and shows:

- **Tool contract**: description, shape of the input and output;
- **Versions**: every revision, with the tests recorded for each one;
- **Use in chat** and **Request a test**: they add to the draft a request to
  use or test the tool; sending it remains up to you;
- **Enable** / **Disable**: disabling blocks future uses; it does not cancel
  activities already started and does not delete revisions.

Creation and tests are always requested in the chat, with a normal request.

### Revisions and contract

The registration keeps an exact copy of the source, verified with the SHA-256
hash of the file in the project, together with the description, the JSON
input and output contract, the declared test cases and the execution limits.
Every revision is immutable: if you edit the file in the project, the
registered tool keeps using the stored copy until you register a new revision,
with new tests. A modified file never silently replaces the registered
implementation and does not inherit the tests of the previous one.

The script runs with the Python standard library, in the project folder, with
a clean environment; output on stderr is limited. Kira does not install
dependencies and does not import browser sessions or credentials.

### Permissions

| Operation | Mode | Project permission |
| --- | --- | --- |
| Register a tool | **Execute** | **Project write** or higher |
| Test (run the declared tests) | **Execute** | **Trusted host** or **Full access** |
| Run | **Execute** | **Trusted host** or **Full access** |

Testing and running require **Trusted host** because the code runs as your
user, not in a sandbox; a passed test does not raise permissions. Running also
requires an enabled revision with the declared tests passed. The tests verify
the declared cases, the validity of the JSON and the exit code; they do not
confirm every external effect (files written, network, services called). See
[Permissions and security](PERMISSIONS_AND_SECURITY.md).

### How the model uses it

The model has a single tool, `kira.project_tools`, with the actions
`describe`, `search`, `read`, `register`, `test` and `run`. `describe` returns
the exact contract of an action; a tool's contract is loaded only after
finding it. The model's arguments cannot select another project, chat,
permission, provider or credential.

Every registration, test and run produces a receipt in the chat, like the
other tools (see [Chat and tools](CHAT_AND_TOOLS.md)), which distinguishes the
outcome of the contract and the tests from unverified external effects. An
interrupted or uncertain operation is not repeated on its own: if a cleanup is
not confirmed, Kira reports it and blocks conflicting work on the project
until it is resolved.
