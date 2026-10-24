# Chat and conversation tools

*[Leggi in italiano](../it/CHAT_E_STRUMENTI.md)*

This document describes in detail everything about the conversation with Kira:
how projects and chats are organized, how you write, queue and correct a
request, what the tool rows show and which limits apply to each feature. The
[User guide](USER_GUIDE.md) gives the short version; here you find the numbers,
the command names and the behavior in case of error.

## 1. Projects and chats

A **project** is a working folder to which Kira attaches four things: the
**instructions** you write, a **permission** (**Read only**, **Project
write**, **Trusted host**, **Full access**), the **Project memory** and the
set of its chats. Instructions, permission and memory are edited from
**Project memory and instructions** (**Actions → Project permissions**, or
**Project memory → Edit project notes**). Permissions are described
in [Permissions and security](PERMISSIONS_AND_SECURITY.md); memory in
[Context and memory](CONTEXT_AND_MEMORY.md).

Every chat belongs to exactly one project. In the sidebar open the project and
pick a chat under its name; **+ New chat** (**Ctrl+Shift+N**) opens an empty
conversation that keeps the provider, model and reasoning level already
chosen. The chat is saved to the database at the first send, so no empty chats
remain in the list.

### Chat name

The initial name is derived from the first request, with normalized spaces;
until you send something the chat is called **New chat**. The **⋯** button
next to the name opens **Rename chat**: the name can have at most 120
characters and cannot be empty. The name is internal data of Kira: it is not
sent to the model, and renaming a chat does not change the provider's
conversation.

### What Kira remembers

Kira remembers the **last selected chat** of each project (up to 32 projects),
so you reopen a project on the chat you were working in; it also remembers the
provider, model and reasoning chosen for each chat and the unsent draft
(section 4).

### One run per project

In the same project only one request works at a time, whichever chat it was
sent from. Reading another chat of the project does not interrupt the work in
progress; a new request in the same chat or in another chat of the project has
to be queued (section 5). The only exception is a read-only chat alongside an
active Team of the same project, described in [Teams](TEAMS.md).

### What each chat shows

Every chat shows only its own messages, its own tool rows, its own usage and
its own artifacts. The view is limited to the **last 60 runs and 600 rows**:
when a chat exceeds this limit the notice **History is limited to the latest
60 runs** appears, which concerns only what you see, not a deletion of
messages nor the model's working memory. The full history stays in the
database and is obtained with **Export chat as Markdown** (section 7).

When a chat is opened Kira briefly shows "Loading chat…" and then
presents the latest messages. If you scroll up, new results do not move the
view: **Latest updates** brings you back to the bottom.

### Activity

The **Completed** / **Running** boxes do not appear in the conversation: you
find them in the **Activity** panel, with the request excerpt, the start time,
the error if any, **Details** and the **Open chat** button that takes you back
to the originating conversation. No event or export record is
ever deleted from this panel.

## 2. Quick actions

**Actions** in the header opens **Quick actions**: a search by name or
description of the app's features. The same list opens with **Ctrl+Shift+P**,
with **Chat tools** (**⋯**) under the field or by typing `/` at the beginning of
the message or after a space. You can also search with English terms such as
`model`, `plan`, `diff`, `queue`, `fork`.

Among other entries you find: **New project**, **New chat**, **Project
files**, **Sent message history**, **Project queue**, the **Plan** /
**Execute** / **Review** / **Recap** modes, **Changes**, **Chat goal**,
**Manage chat**, **Archived chats**, **Compact context**, **Copy last
response**, **Export chat**, **Model and reasoning**, **Project permissions**,
**Project memory**, **Project team**, **Usage and resources**, **Settings**,
**Diagnostics** and **Desktop actions**. Entries that require a project or a
chat appear disabled until you have selected one.

An action opens the corresponding panel or prepares the request: you do not
have to learn commands to use Kira. **Ctrl+C** keeps copying the selected text
and Tab keeps the normal navigation between controls.

## 3. File references

Press the **Files** chip under the field (or **Project files** in the quick
actions), or type `@` followed by part of the name: Kira searches the project
folder and inserts into the message an **editable reference** to the chosen
file, without sending anything.

The search covers only the project folder and excludes:

- symbolic links;
- generated folders (virtual environments, caches, `node_modules`, build
  output and the like);
- files with sensitive names (keys, credentials, secrets);
- files ignored by Git, when the project is a Git repository.

The search is limited to **10,000 entries examined, 100 matches shown and a
depth of 32 folders**. If it hits a limit, Kira reports it: refine the search
with a more precise name. **Attach** always remains
available to explicitly pick any other file with
[Kira's chooser](USER_GUIDE.md#the-folder-and-file-chooser).

A reference is text in the message: it does not change the project
permissions and does not guarantee that the model can read that type of file;
reading happens with the provider's tools, within the project permissions.

## 4. Drafts and history

### The `composer.json` file

The text you write and do not send is saved privately, chat by chat
(including the "new chat" not yet created), in the file
`$HOME/.config/kira/composer.json` (with `KIRA_HOME`: `$KIRA_HOME/config/`).
The save is **atomic**, happens **300 ms** after the last change and is forced
on orderly window close. On reopening you find the text, the attached images
and the Soul identity chosen for the new chat; the request mode goes back to
**Execute**.

The same file keeps the **sent message history** and the identities of pending
requests. The limits are:

| Content | Limit |
| --- | --- |
| Overall file size | 2 MiB |
| Drafts kept | 100 |
| Bytes per single text entry (UTF-8) | 262,144 |
| Messages in the sent history | 200 (the most recent) |
| Pending request identities | 128 |

To make room for a draft Kira may **discard the oldest messages of the
history**, never another draft. If a draft still does not fit, the error
**Draft not saved** appears: copy the text before closing or switching chat. A
damaged `composer.json` file is not overwritten automatically; make a copy
before intervening by hand. This file is not included in a database-only
backup.

Recovery covers the chat field: free-text answers to questions (section 9)
and the Team's temporary editors are not saved here.

### Sent message history

**Actions → Sent message history** searches among the messages the service
has confirmed receiving, up to the last 200, showing those of the current
project. **Reuse in draft** inserts the text into the field without sending
it, so you can edit it. **Clear local project history** deletes only this
reusable list: conversations stay in the database and drafts stay in place.

### Request identities and **Allow new separate requests…**

For sends, queue, guidance, branches, compactions, provider switches, goals,
context notes and Team messages or resumes from the chat, Kira records the
request identity (an identifier and a fingerprint of the arguments, not a copy
of the text) **before** sending it to the service; the same applies to changes
to Soul, Research and project tools. Closing the window or switching chat does
not turn it into a new operation: a second attempt with the same content reuses
the same identity, and an uncertain outcome is never repeated on its own. Team
commands and native launches keep their identity only while the window is open:
after a restart, check **Activity** or the Team view before repeating them.

If you really want to send a distinct request with the same content, first
check the chat, the queue and **Activity**, then use **Manage chat → Allow new
separate requests…** (it appears when some requests have an uncertain
outcome). The command asks for confirmation and sends nothing: it only enables
a subsequent new request, which could duplicate a previous effect whose
outcome is still uncertain.

## 5. Sending, queuing, guiding

Below the message field, on the right, you find **Send** (**Ctrl+Enter**) and,
while work is in progress, **Queue**; the queue button with the number of
waiting requests opens their list. When the **same chat** has an active turn,
**Send guidance** appears in place of **Send**.

| Button | When it appears | What it does |
| --- | --- | --- |
| **Send** | Idle chat and no work in the project | Starts the request immediately. |
| **Queue** | While work is in progress in the project, including a Team | Saves a separate request to be run in a later turn. |
| **Send guidance** | A turn of this chat is in progress | Adds text to the work in progress, without stopping it. |

When another chat of the project is working, the message stays addressed to
the selected chat: you can only queue it. Kira never sends guidance to the
turn of a different chat, and an Enter pressed by mistake does not send
guidance: the dedicated button or **Ctrl+Enter** while the button is visible
is required.

### Limits

- **Guidance** is text only, up to **32,000 bytes** of UTF-8; attachments
  require a separate (queued) request. It cannot change the provider, account,
  model, reasoning or permissions of the active turn.
- The **queue** accepts up to **64 waiting requests per project**, each of at
  most **8,000 bytes** of text. Every entry remembers the target chat, the mode
  and the attachments.

### Queue states

Every queued request goes through these states: **Waiting**, **Starting**,
**Running**, **Completed**, **Failed**, **Cancelled**, **Interrupted**. While
it is waiting you can edit its text, move it with the arrows or remove it; a
request that has already started cannot be edited or cancelled from the queue
(use **Stop**). A removed entry cannot be brought back to life by a late
attempt. If a queued request contains images, Kira verifies them again before
starting: a missing or modified file makes the entry fail and pauses the
queue.

### Pause, resume, errors and restarts

- The queue **continues on its own** after every successfully completed job.
- After an **error**, a **cancellation** or a **service restart** the queue
  **stays paused**: check the outcome and press **Resume queue**.
- If a request's start is refused only because of the current state (work
  already running, an archived chat or the service shutting down), the request
  stays waiting in the paused queue, with the reason shown on the entry.
- **Pause queue** prevents new starts and lets the current turn finish.
- **Stop** requests cancellation of the selected chat's turn in progress and
  waits for the outcome recorded by the service; it does not empty the queue.
- Closing the window does not stop the service and does not delete the queue;
  on reopening you find the saved requests.

### How providers receive guidance

- **Codex** uses its own delivery to the active turn, when the turn allows it.
- **Direct OpenAI · ChatGPT**, **DeepSeek Account** and **Ollama** receive it
  at the **next request** to the model: the response or tool already in
  progress finishes normally, then the waiting text is added in order. Tools
  of a group not yet started can be deferred with outcome
  `DEFERRED_FOR_USER_GUIDANCE`, so that the model re-plans taking the
  correction into account.
- **DeepSeek Web** and **Claude Code · personal use** do not receive guidance
  during a turn: queue a separate request.
- A response already in progress is not rewritten and private reasoning is
  not exposed.

The chat distinguishes four states: **waiting** (text recorded by Kira),
**sending** (delivery to the provider), **receipt confirmed** (the provider
accepted the message) and **outcome to verify**. The provider's confirmation
proves delivery, **not understanding**: check the response and the tool rows.
A send with an uncertain outcome is not repeated automatically; a restart or a
cancellation leaves it marked as undelivered or unknown.

## 6. Request mode

The mode chip under the field applies to the **next** request:

| Mode | What it does | Effective permissions |
| --- | --- | --- |
| **Execute** | Carries out the request. | The project's. |
| **Plan** | Analyzes and proposes a plan. | Read only. |
| **Review** | Looks for concrete problems in the code or text. | Read only. |
| **Recap** | Produces a readable summary of the conversation. | Read only. |

The read-only modes do not change the project's saved permission: they only
reduce it for that turn. To apply a plan go back to **Execute** and send a new
explicit request; no plan is executed on its own. With Codex, the final plan
appears in the conversation as a formatted response even when it arrives as a
native plan instead of a normal message. **Recap** produces a response you can
read: it does not compact the provider's context and does not replace
compaction (see [Context and memory](CONTEXT_AND_MEMORY.md)).

### Changes

**Actions → Changes** shows the project files changed according to Git: staged
changes, local changes and new files, with a read-only diff. You can pick a
file for a focused view. Sensitive content, unreadable files and size limits
can reduce what you see. This screen does not run repository hooks, does not
use external diff tools and neither applies nor reverts changes. **Request
review** prepares in the field a draft in **Review** mode with the diff
preview: check it and send it. It also works with local providers that cannot
run Git on their own.

## 7. Archiving, branching, exporting

All these actions are in **Actions → Manage chat**, in the **Chat tools**
window.

### Archive chat

**Archive chat** hides from the main list an **idle** chat with no queued
requests, and keeps its history, provider reference and files. In
**Archived chats** you can open it or restore it; a chat opened from there can
be exported. An archived chat cannot start work: restore it before sending new
messages. Archived chats do not feed new excerpts shared between
chats. The archive is Kira's: it has no relationship with the provider's
archive.

### Continue from here

**Continue from here** creates a new chat that starts from the end of the
conversation or from a completed request chosen in the list. The original
chat stays intact and no branch undoes the changes already made to the
project files.

- **Codex** uses a native branch. An intermediate point requires a **turn
  boundary recorded** by the provider; an older chat that does not have one
  can only branch from the end. The inherited context stays in the provider's
  thread and the new chat's local history starts from the new requests; a
  banner with **Open original chat** takes you back to the earlier messages
  kept by Kira.
- **The other providers** receive an **explicit copy of the recorded
  context**. The full copied source is limited to **8,000,000 bytes**
  (beyond that, Kira refuses the branch); at subsequent requests the model
  receives a declared excerpt of at most **100,000 characters** of the source.
  No invented messages or events are inserted, and an uncertain branch
  outcome is not repeated on its own.

### Export chat as Markdown

**Export chat as Markdown** (also with **Export** in the top bar) saves the
**whole** persisted conversation, even when the view shows only part of it,
followed by the retained activity records and the current goal, if any. It
includes the guidance accepted during work and the assistant's partial outputs;
truncation markers already present in the events stay as they are. Artifact
paths remain references: the files are neither read nor embedded. Large exports
go through a private file of the service and a destination you choose. The
export of a native Codex branch contains what Kira recorded in that branch: for
the inherited history, export the starting chat as well. Always check the
content before sharing it.

**Copy last response** instead copies to the clipboard the text of a completed
response present in the visible chat.

## 8. Chat goal

**Actions → Chat goal** opens a field in which to describe the desired
result; **Save without starting** records it without starting any work. While
it is active, Kira includes it in the context of every new turn of the chat.
**Continue with one step** sends a single new explicit request; saving or
resuming a goal starts no work cycle.

**Pause goal** removes the text from new turns (without retracting what the
provider has already received), **Resume goal** puts it back and **Mark
completed** records your assessment, which is not an independent verification
of the result. These commands do not replace **Stop** for a turn already
started. A saved goal appears as a compact box above the conversation only
when an explicit record exists; **Open** shows the same controls.

To have Kira work in successive cycles without sending every step by hand,
use the `/obiettivo` command described in
[Autonomous goals](AUTONOMOUS_GOALS.md).

## 9. Persistent questions

When the model needs a choice and phrases it as a structured question, Kira
opens a box separate from the conversation. For each question you can select
one of the choices or **Other** and write the answer; **Send answer** becomes
active when all the questions of the form have an answer. No option is chosen
or sent automatically.

The box is not modal: you can close it and reopen it from the **Questions**
button in the tool tray or with **Ctrl+Shift+Q**; the questions banner stays
visible as long as there are answers to give. **Asynchronous** questions let
the agent continue its independent work; **blocking** ones stop that turn
until you answer.

Kira distinguishes three answer states: **saved** (recorded by Kira),
**forwarding** and **forwarded to the provider**. The last state proves that the
provider received the answer, not that the model used it. If the turn ends,
is cancelled or the forwarding fails, the request is closed: question and
answer remain readable and copyable for a new turn, without automatic
resends. Questions from Team members appear in the same list.

| Limit | Value |
| --- | --- |
| Question requests per run | 16 |
| Questions per request | 1 to 3 |
| Choices per question | at most 6 |
| Form (encoded) | 64 KiB |
| Answer set | 16 KiB |

Requests and sent answers survive reconnection and restart. Free-text answers
**not yet sent** stay only in memory and are lost when you close the app or
switch project. A question written in the text of a response does not become
a form: the provider's structured format is required. Requests for passwords
or secrets are refused (provider sign-in uses their normal flow). Answers
never change the project permissions, do not approve commands and do not
start or resume work.

## 10. Attachments and images

### Who sees the images

| Provider | Visual content of attachments |
| --- | --- |
| **Direct OpenAI · ChatGPT** | Yes |
| **DeepSeek Account** (experimental) | Yes, only with the models whose catalog entry declares image support |
| **DeepSeek Web** (experimental) | Yes, with DeepSeek V4 Flash (web) and DeepSeek Reasoner (web) |
| **Claude Code · personal use** (experimental) | Yes, up to 4 PNGs |
| **Codex**, **Ollama** | No: the attachment becomes a reference to the path, as text |

A refusal by the model is shown as an error: Kira does not change model on its
own.

### Attaching an image

With a provider that accepts images you can press **Attach** and choose the
files, paste an image from the clipboard with **Ctrl+V**, **Shift+Ins** or
**Paste**, or drag image files when the system forwards the drag. Every image
appears with preview, name and dimensions, and can be removed before sending.
You can send an image-only message or queue images together with text.

| Limit | Value |
| --- | --- |
| Images per message | 4 |
| Accepted formats | PNG, JPEG, WebP, static GIF |
| Source file | 32 MiB and 32 million pixels |
| Each normalized PNG | 8 MiB |
| Normalized total per message | 16 MiB |

Animated GIF, WebP and APNG are refused, not reduced to the first frame. A
file over a limit fails with a message: Kira neither resizes nor discards
silently.

### Normalization and retention

Preparation is local and contacts no service. Kira honors the recorded
orientation, converts colors to **sRGB** and copies the pixels into a new PNG
**without EXIF, comments, text and color profiles**. Dimensions stay the
original ones (width and height are swapped only if the orientation requires
a rotation). The original file is not touched, and modifying it after import
changes neither the preview nor what will be sent. The bytes are transmitted
to the provider only with the message.

Images in the draft are saved per chat and found again after a restart. If
you switch provider or model to one without vision, the images stay in the
draft but sending is blocked: choose a compatible model or remove them.

### Paths as text

**Writing or pasting a path remains text**: it does not attach the bytes and
does not let the model see the image. With **Codex** and **Ollama**,
**Attach** and paste always insert the local path, even for images; with the
other providers the same applies to non-image files and to free-text answers
to questions. An image pasted from the clipboard into an answer to a question
is saved as a private PNG in the `attachments/` folder and inserted as a path.
Reading a path requires the project's tools and permissions and does not
guarantee visual understanding.

A reference import accepts at most **64 files** and **16,384 characters** of
paths; converting an image from the clipboard for a reference has the same
limits of 32 MiB and 32 million pixels. Preparation happens off the UI thread,
shows progress and errors and disables sending until it is finished.

### Windows and WSL

From Windows **File Explorer** copy the files with **Ctrl+C** and paste them
into Kira with **Ctrl+V**: Kira queries the Windows file list only at that
moment (no clipboard monitoring) and recognizes the paths reachable from the
current WSL distribution, including mounted drives. Wait for the preview or
the reference to appear before sending. Missing files, inaccessible mounts and
a different distribution are reported; nothing is downloaded from the network.
Direct drag to the Linux window depends on WSLg: when it does not arrive, use
copy/paste or **Attach**. In the chooser the Windows drives are among the
places (drive C: is `/mnt/c`).

### Where the files end up

Kira keeps the normalized PNGs in `image-attachments/` and the path- or
clipboard-based inputs in `attachments/`, both in the data folder
(`$HOME/.local/share/kira/`, or `$KIRA_HOME/data/`). Imported files are
read-only and private to your user. Removing an attachment from the draft
deletes neither the files already imported nor the images of sent messages;
there is no automatic expiry. A database-only backup does not contain these
bytes: keep the data folder as well (see [Installation](INSTALLATION.md)).

## 11. Tool receipts

Every operation the model performs (reading a file, a command, a text search, a
browser page…) appears in the chat as a **receipt**: a row with a thread in the
theme's accent color on the left, the operation label, the literal path or
command and the outcome. The labels are the same for all providers (**Reading
the file**, **Running the command**, **Searching text**…); the tool's native
name stays in the row's original data. An unknown tool keeps its literal name.

### Kira activity

Consecutive operations of the same request are gathered in a **Kira
activity** group, with the number of operations and their overall status. The
grouping does not cross user or assistant messages, notices, Team entries or
different requests; it is presentation only: order, contents and original
data of the individual rows stay intact. Open the group to see the receipts;
errors and unconfirmed outcomes remain visible even with the group closed.

### Preview and full body

Receipts start compact: they show the first **12 lines**, up to **1,600
characters**, in about 144 pixels of height. Short results have no toggle;
long ones offer **Show all** and **Collapse**, by keyboard and pointer. The
body shown is limited to **131,072 characters** with a notice: expanding does
not recover bytes absent from the recorded output; for very large outputs use
**Export** or the artifacts. When the history is reloaded, receipts start
compact again.

For every receipt you find: **Copy** (always copies the full body, regardless
of the preview), **Copy command** and **Data** (the JSON of the recorded
event). The files recorded as results are in **Files** (**Project artifacts**)
and **Open folder** in the project **Details** opens the working folder,
because not every file created by a command automatically becomes an artifact.
A sentence from the model such as "I ran the command" must always be compared
with the actual receipt.

### Status indicators

Every receipt and the activity row use a color, a symbol and an accessible
label: the theme's **accent color** (amber in the Kira theme) running,
**green** confirmed completion, **red** error, **amber** waiting, paused or
unconfirmed outcome, **gray** queued, cancelled or unknown. Only an operation
actually in progress pulses (1.4-second cycle, no movement); the animation
stops when the window is hidden, with reduced animations (**Reduce motion**)
or outside the active chat. A request that ended with failed or
unconfirmed tools keeps those outcomes visible: the completion of the turn
does not become the success of every tool.

Opening a receipt keeps the reading position and suspends automatic
scrolling; **Latest updates** brings you back to the bottom and re-enables
it.

## 12. Formatted responses

The assistant's responses are rendered as Markdown: headings, bold, italic,
strikethrough, lists, quotes, inline code, code blocks, tables, horizontal
rules and links. This also applies to conversations already saved. Selection
crosses paragraphs and code; **Ctrl+C** copies the text as you see it, so a
command without the block delimiters.

For every response: **Copy** copies the whole original Markdown; **Text**
shows the source and **Formatted** returns to the formatted view (the choice
persists during streaming). Tool results, user messages and labels always
remain literal text.

- **Links** open only on click: HTTP/HTTPS pages with a host and without
  embedded credentials, or **existing, non-executable** documents inside the
  project folder. Scripts, programs, files with an executable bit or an
  executable header are blocked: open them in your editor. Other schemes and
  remote file hosts are refused. Opening a document runs no commands.
- Markdown **images** appear as text descriptions: nothing is downloaded.
- **HTML** written by the model stays visible as literal text.
- `diff` blocks color added and removed lines while keeping the `+` and `-`
  signs; there is no general syntax highlighting and no interactive checkboxes
  for task lists.
- The renderer accepts at most **131,072 characters** of input and 20 nesting
  levels; beyond the output limit it shows the source.

**Smart punctuation** (quotes and dashes) changes only the display of
prose; sent text, code and **Copy** stay identical. Details in
[Appearance and language](APPEARANCE_AND_LANGUAGE.md).

## 13. Request status

Every run of a request has its own final status: several **Completed** rows
in **Activity** refer to different requests, not to repeated work.
**Completed** means the service recorded the end of the run; it is not proof
that every task succeeded, which is why the responses, receipts and artifacts
remain. The **Activity** cards show an excerpt of the request (up to 160
characters) and the start time in the computer's time zone; **Details** shows
in JSON identifiers, request (flagged when truncated), start and event times in
**UTC**, event sequence, status, error and recorded outcome.

If the chat's last request fails or is interrupted, an **error box** appears
above the message field with the available reason and a reference to the
request; **Details** opens the full error with HTTP code and call identifier,
when the provider supplies them. The box reappears when the chat is reopened
and is replaced by the next request; earlier outcomes remain in
**Activity**. It does not appear while the history is loading, during a new
send or for your own cancellations.

An **HTTP 503** means the provider was unavailable for that request; the code
alone does not say whether the cause was overload, a billing problem or
something else. Kira **does not resend** automatically, does not replace the
draft and does not change model: the sent message is saved in the chat
anyway. Before retrying, check in **Activity** the actions already executed.

## 14. Chat preferences

In **Settings → Work** and in the other tabs you find the preferences that
concern the conversation. They are saved immediately in
`$HOME/.config/kira/ui.json` and do not touch project permissions, models or
credentials.

| Preference | Values | Notes |
| --- | --- | --- |
| **Conversation** size (in **Appearance → Fonts**) | 12 to 20 px (default 14) | Interface and code have separate sizes, in the same section. |
| **Interface density** | **Comfortable** (default) / **Compact** | |
| **Reduce motion** (in **Appearance**) | **Follow system** / **Always** / **Never** | Stops animations while keeping the status texts. |
| **Sidebar** | **Whole** / **Icons only** / **Hidden** | Also with **Ctrl+B**. |
| **Labels next to the tool icons** | yes / no | Shows the text next to the icons of the tray under the field. |
| **Open the model options with the chat** | yes / no | Opens **Provider, model and reasoning** when entering a chat. |
| **Show the Details panel** | yes / no | The right-hand panel starts closed; **Details** in the top bar also opens or closes it, and the choice is remembered. |
| **Ask for confirmation before closing the window** | **When work is running** / **Always** / **Never** | Closing the window never stops a turn. |
| **Open files with** | **Default system application** / **Visual Studio Code** | VS Code requires the `code` command; if it is missing, Kira reports it. |
| **New project permissions** | **Read only** (default) and the others | Existing projects do not change; wider permissions must be chosen explicitly. |
| **Show the “Next step” hint** | yes / no | **Hide** closes it; here you re-enable it. It does not delete the saved note. |
| **Show reasoning summaries** | yes / no | Shows or hides the **Reasoning summary** panel under the chat without interrupting the work. |

The **Reasoning summary** is the public summary that some providers (Codex,
Direct OpenAI) supply during processing, or the reasoning that DeepSeek Web
shows on its own site with DeepSeek Reasoner; it does not certify that an
operation happened and is not the final response; with Ollama, DeepSeek Account
and Claude Code it is not available. Themes, fonts, column width and interface
language are in [Appearance and language](APPEARANCE_AND_LANGUAGE.md); the
choice of provider and model in [Providers](PROVIDERS.md).
