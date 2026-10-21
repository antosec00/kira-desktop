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
**Project memory and instructions** (the **Project** button in the header, or
**Actions → Project memory → Edit project notes**). Permissions are described
in [Permissions and security](PERMISSIONS_AND_SECURITY.md); memory in
[Context and memory](CONTEXT_AND_MEMORY.md).

Every chat belongs to exactly one project. In the sidebar open the project and
pick a chat under its name; **+ New chat** (**Ctrl+Shift+N**) opens an empty
conversation that keeps the provider, model and reasoning level already
chosen. The chat is saved to the database at the first send, so no empty chats
remain in the list.

### Chat name

The initial name is derived from the first request, with normalized spaces;
until you send something the chat is called **New chat**. With the **⋯**
button next to the name choose **Rename chat**: the name can have at most 120
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

When a chat is opened Kira briefly shows "Loading the chat…" and then
presents the latest messages. If you scroll up, new results do not move the
view: **Latest updates** brings you back to the bottom.

### Activity

The **Completed** / **Running** boxes do not appear in the conversation: you
find them in the **Activity** panel, with the request excerpt, the start and
outcome times, the error if any, **Details** and the **Open chat** button that
takes you back to the originating conversation. No event or export record is
ever deleted from this panel.

## 2. Quick actions

**Actions** in the header opens **Quick actions**: a search by name or
description of the app's features. The same list opens with **Ctrl+Shift+P**
or by typing `/` in the message field. You can also search with English terms
such as `model`, `plan`, `diff`, `queue`, `fork`.

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

The search is limited to **1,000 entries examined, 100 matches shown, 32
folders of depth and 16 MiB scanned**. If it hits a limit, Kira reports it:
refine the search with a more precise name. **Attach** always remains
available to explicitly pick any other file with the system picker.

A reference is text in the message: it does not change the project
permissions and does not guarantee that the model can read that type of file;
reading happens with the provider's tools, within the project permissions.

## 4. Drafts and history

### The `composer.json` file

The text you write and do not send is saved privately, chat by chat
(including the "new chat" not yet created), in the file
`$HOME/.config/kira/composer.json` (with `KIRA_HOME`: `$KIRA_HOME/config/`).
The save is **atomic**, happens **300 ms** after the last change and is forced
on orderly window close. On reopening you find the text, the mode, the chosen
Soul identity and the attached images.

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

To queue, send guidance, branch, compact and change provider, Kira records the
request identity (an identifier and a fingerprint of the arguments, not a copy
of the text) **before** sending it to the service. Closing the window or
switching chat does not turn it into a new operation: a second attempt with
the same content reuses the same identity, and an uncertain outcome is never
repeated on its own.

If you really want to send a distinct request with the same content, first
check the chat, the queue and **Activity**, then use **Manage chat → Allow new
separate requests…**. The command asks for confirmation and sends nothing: it
only enables a subsequent new request, which could duplicate a previous effect
whose outcome is still uncertain.

## 5. Sending, queuing, guiding

To the right of the message field you find **Send** (**Ctrl+Enter**) and
**Queue**; **Queue** opens the list of waiting requests. When the **same
chat** has an active turn, **Send guidance** appears in place of **Send**.

| Button | When it appears | What it does |
| --- | --- | --- |
| **Send** | Idle chat and no work in the project | Starts the request immediately. |
| **Queue** | Always | Saves a separate request to be run in a later turn. |
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

Every queued request goes through these states: **waiting**, **starting**,
**running**, **completed**, **failed**, **cancelled**, **interrupted**. While
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
- **Pause queue** prevents new starts and lets the current turn finish.
- **Stop** requests cancellation of the turn in progress and waits for the
  backend's outcome; it does not empty the queue.
- Closing the window does not stop the service and does not delete the queue;
  on reopening you find the saved requests.

### How providers receive guidance

- **Codex** uses its own delivery to the active turn, when the turn allows it.
- **OpenAI direct · ChatGPT**, **DeepSeek Account** and **Ollama** receive it
  at the **next request** to the model: the response or tool already in
  progress finishes normally, then the waiting text is added in order. Tools
  of a group not yet started can be deferred with outcome
  `DEFERRED_FOR_USER_GUIDANCE`, so that the model re-plans taking the
  correction into account.
- A response already in progress is not rewritten and private reasoning is
  not exposed.

The chat distinguishes four states: **waiting** (text recorded by Kira),
**sending** (delivery to the provider), **receipt confirmed** (the provider
accepted the message) and **outcome to verify**. The provider's confirmation
proves delivery, **not understanding**: check the response and the tool rows.
A send with an uncertain outcome is not repeated automatically; a restart or a
cancellation leaves it marked as undelivered or unknown.
