# Providers and models

*[Leggi in italiano](../it/PROVIDER.md)*

Kira is not a model: it is the application that talks to the model you choose.
This guide explains how to connect each supported provider, who executes the
tools in each case, how context is handled, which limits exist and how to read
**Usage and resources**. It is written for people who want to use Kira with
their own account or with a local server and understand what really happens
behind every answer.

## Overview

| Provider | How it connects | Who executes the tools | Isolated browser and Kira's Computer | Attached images | Team | Autonomous goals | Research (proposals from the model) | Reasoning summary |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **Ollama** | An Ollama server already installed; endpoint in **Settings → Connections → Ollama endpoint** | Kira | Yes (tools verified on first use; screenshots only with a model that accepts images); no Computer without vision | No (paths as text only) | Yes | Yes | Yes | No |
| **Codex** | Codex installed separately; **Models → Codex → Sign in with Codex** | The provider (Codex native tools, observed by Kira) | No (uses its own native tools) | No (paths as text) | Yes | Yes | Yes | Yes |
| **Direct OpenAI · ChatGPT** | **Continue with ChatGPT** in **Models** or **Settings → Connections** | Kira | Yes | Yes (up to 4 PNG/JPEG/WebP/static GIF) | Yes | Yes | Yes | Yes |
| **DeepSeek Account** (experimental) | **Models → DeepSeek Account → Sign in with DeepSeek**, or **Use DeeperSeeker** | Kira | Yes | Yes, vision models only | Yes | Yes | Yes | No |
| **DeepSeek Web** (experimental) | The web session's `userToken` pasted into the **DeepSeek Web · direct access** panel | Kira (text protocol) | Browser yes, Computer no | Yes (DeepSeek V4 Flash web, DeepSeek Reasoner web) | No | No | Yes | Yes, with DeepSeek Reasoner (web) |
| **Claude Code · personal use** (experimental) | `claude-code` extra from source; sign-in already done in Claude Code | The provider (Claude Code built-in tools) plus the Kira tools via MCP | Yes | Yes (up to 4 PNG) | No | Yes | Yes | No |

The "Who executes the tools" column is the most important difference between
providers. With Ollama, Direct OpenAI and DeepSeek the model *asks* Kira to
read a file, run a command or open a page, and Kira performs the operation
applying the project's permissions, modes and approvals. With Codex and Claude
Code, file operations and commands are executed by the provider's runtime; Kira
observes them, records them as receipts in the chat and binds them to the
project permissions through the provider's sandbox or filters.

## Principles shared by all providers

**You choose the model, for every chat.** Open **Models** (also from the model
chip below the composer, which opens **Provider, model and reasoning**).
Providers are a row of buttons; each model is a card with its identifier,
declared tools, size when known and available reasoning levels. **Use model**
selects it for the current chat.

**The provider status is a colored dot** below the row of buttons:

| Color | Meaning |
| --- | --- |
| Green | Connected, catalog available |
| Amber | To be connected, or connected but without models |
| Red | Unavailable (server unreachable, executable missing, sign-in expired) |

**A listed model is not a right to use it.** The catalog says what the provider
exposes to your account or your server; only a completed response confirms that
the model is really usable with your permissions and your plan. This applies to
special access programs, vision models and reasoning levels.

**Kira never changes model, account or provider on its own.** If a request is
rejected because of authentication, quota, account policy or an unavailable
model, the error stays visible and the chat keeps the configuration you chose.
No alternative model is selected, no paid API key is used, no billing tier is
changed.

**Credentials stay within the provider's official flow.** Kira does not read
browser cookies, does not import authentication files from other applications
and does not copy tokens into the database, the events, the exports or the
diagnostics. When Kira manages a sign-in (ChatGPT, DeepSeek Account, DeepSeek
Web), the credentials live in private files of the Kira profile, separate from
the conversation data.

**Provider data** shows the raw JSON returned by the catalog, collapsed until
you open it. No value is invented: what the provider does not declare (size,
context window, image support) remains unknown in the model card too.

**Shared settings** are in **Settings → Connections**: **Tool timeout
(seconds)**, **Tool round limit (0 = unlimited)** and **Maximum output per
run** apply to all providers and take effect with **Save settings**. While work
is running, connections and limits cannot be edited; interface preferences can.

## Ollama

Ollama is the provider for local models (or models on a server of your own).
Kira connects to Ollama's native API and manages the whole tool loop itself.

### Connecting

You need an Ollama server already running with at least one model installed.
Kira does not install Ollama, does not download models and does not download
weights: if the catalog is empty, the dot stays amber until you install a model
with Ollama's tools.

The default endpoint is `http://127.0.0.1:11434` and refers to the system where
the Kira service runs. To use a server on another machine, enter the full
address (HTTP or HTTPS) in **Settings → Connections → Ollama endpoint** and
press **Save settings**. Keep in mind that a remote endpoint receives the whole
chat context (files read, project memory, screenshots). An address with embedded
credentials (`http://user:password@…`) is rejected. Kira never changes the
endpoint on its own.

To use Kira's isolated browser with Ollama too, install the engine once with:

```sh
kira-desktop browser-install
```

### Tools: verification on first use

Ollama's catalog declares whether a model "supports tools", but the declaration
is not enough. The first time a chat asks a model for tools, Kira runs a short
check with no side effects: it asks the model to return a random value through
a function call and checks that the response is a valid call with that exact
value. The outcome applies to that model on that endpoint for as long as the
service keeps running; the check is repeated if the model is updated or the
chat's reasoning setting changes. If it succeeds, tools are enabled; if the
model answers without the expected call, it remains available for conversation
only and the chat flags it with the notice
`Chat-only mode: tool capability was not verified`. A connection error during
the check stops the turn with its message, and the check runs again on the next
turn. The outcome stays among the run's recorded events.

On the **Models** page the model card shows **Verified tools** once the check
has passed for that model on that endpoint since the service started; an
updated model returns to **Tool support advertised · checked on first use**
until it is checked again. The page only reads recorded outcomes and never runs
a check. The check and the card work the same way with DeepSeek Account, which
uses this tool loop.

With tools verified and **Tools** enabled in the chat, the model receives the
Kira tools allowed by the context: files and commands according to the project
permissions, history and persistent questions, assistant and Team operations,
Research, Knowledge, project tools, goals, browser and Computer. Every call
still goes through the Kira service's permission, mode and approval checks; a
write requires the expected file hash and commands use explicit arguments, with
no shell interpretation.

With **Tools** disabled, Ollama receives only the browser and Computer tools, if
they are enabled for the chat. The browser's text tools require the check
described above; screenshots, **Kira's Computer** and visual control also
require the model to declare image support. Screenshots are sent in the message
to the model and are not saved in the chat events. A model without vision has
no access to Kira's Computer.

Images attached by the user are not sent to Ollama: the model receives only the
path as text.

### How Kira instructs the model

When tools are available, Kira adds Ollama-specific instructions: perform the
next step with a tool instead of merely announcing it, and keep going after the
results until completion or a concrete blocker. This improves the behavior of
local models but cannot force them: the call remains the model's decision. A
text that says "I ran the command" without a matching receipt is not an
execution.

### Context, window and internal reasoning

Ollama receives a recent, limited window of the history, with an indication of
how much was omitted. A model with verified tools can retrieve the rest through
the history and Knowledge tools; with tools disabled or unsupported, the
archive is still kept but the model cannot consult it on its own. The
**Important chat notes** and the project instructions stay in the initial
context.

In **Details** you find two options that are saved with the chat:

- **Requested context**: the model's default value or **32K**, **128K** and
  **256K**. It is a request, not a guarantee: larger windows use more memory
  and can slow down or prevent the model from loading. After changing it, check
  in **Usage and resources** the context actually loaded and the memory used by
  Ollama.
- **Model internal reasoning**: default, disabled or enabled. Kira neither
  shows nor saves the internal reasoning text; it stays in the in-memory
  exchange of the turn and is removed from the stored messages.

With reasoning enabled and a long history, a local model can take several
minutes to process the prompt before producing the first fragment. Kira waits
up to 600 seconds (ten minutes) without receiving data before aborting the
request; the limit applies to the wait between one update and the next, not to
the total duration of the turn. **Stop** is always available and an abort for
inactivity is reported with its cause.

### Recovery from the "No user query found in messages" error

Some local model templates reject a request whose last message is a tool
result, answering with the error `No user query found in messages`. When Kira
recognizes exactly this error after a tool result, it repeats the request once
as an explicit continuation, keeping the results already obtained in the
context without re-running anything. The recovery is limited to a single
attempt per run and does not apply to other server errors.

### Limits and safeguards

- Three identical consecutive tool requests, with the same result, stop the
  turn with an explicit no-progress reason.
- **Tool round limit** in **Settings → Connections** (`max_tool_rounds`) sets
  the maximum number of tool rounds per request; 0 means unlimited.
- The output sizes of commands and files follow **Maximum output per run**;
  commands follow **Tool timeout**.
- If an interrupted history contains a tool request with no recorded result,
  the next turn receives an `UNKNOWN_OUTCOME` outcome and the instruction to
  inspect the current state: Kira never repeats an uncertain effect.
- Very large catalogs or proxies that compress responses may be rejected (Kira
  requests uncompressed responses and limits the size of the metadata); the
  error is visible and no endpoint is changed.
- An error returned by Ollama reports the server's text together with the HTTP
  code, for example when the model requires more system memory than is
  available.

**Stop** closes the stream to Ollama and cancels the local work started by Kira;
it does not guarantee that the server stops generating immediately.

### Branching, queue and modes

Ollama uses the same queue, the same archive and the same chat goals as the
other providers. **Plan**, **Review** and **Recap** reduce the local
permissions to read only for that request; there is no provider-native mode.
**Continue from here** creates a new branch by copying the readable text
context within declared limits; it does not clone an Ollama process and does
not undo changes already made to files. Guidance sent while work is running is
added to the next request to the model, after the response or tool already in
progress.

Ollama does not provide a public reasoning summary: the **Reasoning summary**
panel says so and does not show the raw internal reasoning.

## Codex

Codex is OpenAI's agent, which runs as a runtime on your computer. With this
provider Kira does not execute the tools: it starts the Codex app server,
passes it the request and the project context, and observes what Codex does.

### Installation and sign-in

Codex must be installed separately: the Kira package includes neither the
runtime nor an account. By default Kira looks for the `codex` command in the
`PATH`. If it is not found (typical when Codex is installed with npm or nvm and
the app is started from the menu or from systemd), enter the absolute path of
the executable in **Settings → Connections → Codex executable**. For an absolute
path, Kira adds its folder to the child process's `PATH`, so that npm wrappers
find their own Node interpreter. Kira does not update or replace the Codex
installation and does not modify its global configuration.

In **Models → Codex** press **Sign in with Codex**: Kira asks Codex to start the
official login with the ChatGPT account and opens the browser. The Codex service
process stays alive until the browser completes the sign-in; Kira receives only
the outcome (succeeded or failed) and neither reads nor stores the tokens. After
signing in, press **Refresh** to load the catalog. The status line shows the
account type and plan without the e-mail address.

### Native tools and receipts

Codex uses its own tools to read and write files and run commands. Kira
receives the start and completion events of every operation and shows them as
receipts in the chat; it does not execute them a second time. A file written by
Codex becomes an artifact only if Kira can resolve it inside the project and
compute its hash; a file produced by a native command may not appear among the
artifacts and must be inspected with **Open folder**. Codex's private reasoning
does not enter the conversation.

### Permissions: from Kira to the Codex sandbox

The project permission chosen in Kira is translated into the Codex sandbox on
every request:

| Kira permission | Codex sandbox | Effect |
| --- | --- | --- |
| **Read only** | `read-only` | No writes, network disabled |
| **Project write** | `workspace-write` | Writes only inside the project root, network disabled, temporary folders excluded |
| **Trusted host** / **Full access** | `danger-full-access` | Commands run as your user, with no file system containment |

The read-only and write sandboxes restrict writes and network according to the
installed runtime; they do not promise that the project data is the only data
readable. With trusted host there is no containment at all.

Model, reasoning level, folder and sandbox are checked before every turn; there
is no fallback model and no service tier chosen by Kira. The child process
environment is limited to the base set expected by Codex.

### Approvals

Ordinary operations authorized by the project permission proceed without
repeated confirmations. An unexpected approval request cannot widen a read-only
or project-write permission. With trusted host, Kira can show you Codex's
requests and accepts only decisions on a single operation (accept, reject,
cancel): it never installs permanent changes to the command or network policy,
and it rejects requests for additional permissions. Codex's structured questions
arrive as persistent Kira **Questions** and never become an approval or an
implicit consent.

### Native planning, guidance and branching

- **Plan** uses Codex's native collaboration mode, when the runtime exposes it;
  ordinary requests explicitly select the default mode, so a previous plan does
  not stay "stuck". The final plan appears in the chat as a formatted response.
  Applying it requires a new request in **Execute**.
- **Send guidance** during a turn uses the native delivery to the active turn.
  Kira records the guidance as a message only after Codex confirms it; the
  confirmation means that the text was delivered, not that the model has
  already applied it. A delivery with an uncertain outcome is not repeated and
  is never redirected to a different turn.
- **Continue from here** creates a native branch of the Codex thread: the new
  branch inherits the provider's internal context, while Kira's local history
  starts over from the new requests (**Open original chat** in the banner shows
  the previous messages). Intermediate points require a turn boundary recorded
  by the provider; for older chats only the end may be available. A native goal
  copied into the branch does not restart on its own.

### Compact context

**Manage chat → Compact context** asks Codex to compact its own thread. It
requires an idle chat and a confirmation; Kira waits for the provider's actual
confirmation (it is not enough for the request to be accepted) and keeps its
own local history intact. No amount of saved tokens is promised. At the end,
the app server used for the operation is shut down; Codex's configuration and
credentials do not change.

### Native Codex goals

Codex can have its own internal goals, distinct from Kira's chat goals and
autonomous goals. If a native goal is still open, Kira may refuse to resume the
thread or to run **Compact context**, to prevent Codex from continuing unmanaged
work on its own. Kira does not pause, complete or rewrite that goal: it must be
resolved in Codex.

### Teams and questions

Team members use independent Codex threads with the same model, reasoning and
project permission; Codex's internal delegation to sub-agents is disabled, so
the list of members you see in Kira is the real one. Structured questions (at
most three per request, six options each) are persistent Kira **Questions**: no
timeout supplies a default answer and an answer sent late does not start a new
turn.

### Interruption

**Stop** asks Codex to interrupt the turn. If the confirmation does not arrive
within 15 seconds, the turn stays recorded as interrupted with an unknown
outcome. Resuming means sending a new explicit turn on the saved thread, not
repeating the interrupted work.

### Account usage

In **Usage** or **Manage usage**, the **Account usage** panel reads the limits
of the account signed in to Codex: for each quota group, the reported windows
(their duration determines the name), the percentage used and remaining, the
reset date and, when present, the credit balance as a provider string, with no
inferred currency. **Refresh** performs a new read; it does not create turns,
does not redeem resets and does not buy credits. A missing value stays **Not
available**: it means neither zero nor unlimited. The Codex account may differ
from the one connected to Direct OpenAI, and Kira does not treat them as the
same account based on e-mail or plan.

### Reasoning summary

Codex provides the public reasoning summary; see the final section.

## Direct OpenAI · ChatGPT

This provider talks directly to the OpenAI API using your ChatGPT plan, through
the official **Sign in with ChatGPT** flow. It does not involve Codex: the tool
loop, permissions, questions and Team are managed by Kira.

### Connecting

In **Models** or in **Settings → Connections → ChatGPT accounts** press
**Continue with ChatGPT**. The browser opens with the official sign-in:
authorize Kira and enable the use of your ChatGPT plan. A valid identity without
that permission cannot run inference. Return to Kira, press **Refresh status**
and choose a model from your account's catalog.

The flow uses a public client with PKCE, a temporary listener on `127.0.0.1`
and verification of the identity token; only the sign-in button opens the
browser. If you abort the sign-in in the browser, press **Cancel sign-in** in
the panel. Tokens are renewed and replaced together; they stay in a private
folder of the Kira profile and are excluded from the database, events, project
context, exports and diagnostics. Kira also registers the current and previous
values with its own redaction filter, so that an accidental echo in a tool
output does not show them in clear text.

### Shared plan and app limits

Usage shares your plan's availability and the specific limits assigned to the
Kira app. They are managed at
[ChatGPT → Settings → Usage](https://chatgpt.com/settings/usage); in Kira,
**Plan usage ↗** and **Copy link** lead to the same page. The integration does
not expose weekly quotas or credits: Kira does not infer them from the response
tokens and does not replace them with the Codex account data.
An app limit error (`subscription_sharing_usage_limit_exceeded`) is shown
prominently and does not mean that the whole plan is exhausted, nor does it
indicate a reset time. Kira never switches to a paid API key.

On first use a notice appears that you can dismiss; the choice is remembered in
the private interface preferences.

### Multiple accounts and the account bound to the chat

You can connect several accounts with **Add account**, choose the **Account for
new chats** in the panel menu and disconnect one with **Disconnect account**.
Every direct chat stays bound to the account it started with, even if you later
choose another account for new chats; model changes and Team continuations keep
the binding. To change the account of an existing conversation, use the
provider selection in the chat (see below).

Changing the active account is blocked while there is direct work, queues or
pending reservations; a sign-in in progress blocks new direct requests. Logging
out attempts remote revocation and deletes the local tokens: if the remote
revocation is not confirmed, Kira reports it and you can always disconnect Kira
from the ChatGPT settings.

### Tools and images

The model calls named functions and Kira executes them: file reading and
writing (with a control hash), commands with trusted host, structured
questions, Kira and Team operations, Research, browser and Computer. Only a
successfully completed response authorizes the execution of the calls it
contains; an incomplete, failed or interrupted response executes nothing. The
results go back to the model with the call identity, and an effect already
completed is never repeated.

Images added with **Attach** or pasted (up to four per request, PNG, JPEG, WebP
or static GIF) are sent as visual content, with a preview before sending; a
plain pasted path stays text. Actual support depends on the model and the
account. The originals stay separate from the active context: attachment limits
may prevent older images from being resent, and Kira reports it.

### Hosted web search and browser

In the chat's web settings (**⚙** icon in the browser bar) choose the **Web
search mode** (**Live access**, **Indexed content** or **Search disabled**) and
enable the browser. The web search hosted by OpenAI depends on the model and
the account's authorizations; a refusal is shown without changing model or
billing. Sources appear as links in the responses and are not invented.

Kira's isolated browser and **Kira's Computer** work with this provider: pages
open in the **Browser** panel next to the chat, Kira runs the browser tools
locally and passes the screenshots to the model as images. Visual control uses
Kira's custom tools, not OpenAI's native "computer" tool, which is not available
on this path and which Kira does not request. Details and consents in
[Browser and Computer](BROWSER_AND_COMPUTER.md).

### Persistent context and compaction

Direct OpenAI has no thread saved on the server: Kira sends the history with
every request and keeps a typed state across turns and restarts, bound to
chat, account and model. It reads the whole original history in order and
compacts it automatically near the working limit of the selected model, with no
fixed cut-off of requests. You can force a compaction with **Compact now** in
**Context and memory**. The catalog's default and maximum capacity remain
distinct and estimates are reported separately from the provider's measures.
How it works in detail: [Context and memory](CONTEXT_AND_MEMORY.md).

### Reasoning summary

Ordinary requests ask OpenAI for the public reasoning summary, without changing
model, level or account. If a model explicitly rejects the option, Kira repeats
the request once without it; a missing summary does not interrupt the work.
Auxiliary compaction requests do not ask for summaries.

### What is not included

Connectors, image, audio and video generation, file uploads to the API, native
turn guidance and native branching are not part of this provider. Portable
branches (**Continue from here**) work.

### Disconnecting

**Disconnect account** in the panel removes the local tokens and requests
remote revocation. Alternatively, revoke the authorization from the ChatGPT
settings. Finish the direct work first and empty or complete the queue.

## DeepSeek Account (experimental)

DeepSeek Account uses the sign-in with your DeepSeek account, with the DeepSeek
Harness flow as the protocol reference: you do not enter an API key, except in
DeeperSeeker mode (see below). Inference happens on `api.deepseek.com`; the
tools are Kira's. It is an experimental integration that follows a rapidly
evolving project.

### Sign-in

In **Models → DeepSeek Account** press **Sign in with DeepSeek**. Kira opens the
system browser with a PKCE flow, a callback on an exact local address and state
checking; the grant is saved privately in the selected Kira profile. Kira does
not read cookies and does not import Harness code. After signing in, press
**Refresh status**, refresh the catalog (which comes from the account's model
list) and choose the model. **Cancel sign-in** aborts a sign-in in progress.

Read the notice in the panel before signing in: the account exchange may create
a managed key on the DeepSeek platform that the local logout might not revoke.
**Disconnect from Kira** deletes the local grant and asks DeepSeek to end the
account session without checking the outcome; it does not open the site's
logout and cannot guarantee the revocation of that key: check it in your
DeepSeek account.

### Tools, images and context

The provider uses the same Kira tool loop described for Ollama, including the
check on first use: files, commands, browser, Kira's Computer, questions, Team,
Research and goals, with the service's permission and approval checks. With
DeepSeek, Kira's tools always stay enabled: a chat's **Tools** option turned off
does not disable them. The model's internal reasoning is discarded and not
shown.

Attached images are sent only if the model selected in the catalog declares
vision; text-only models reject images.

Context compaction is managed by Kira: it estimates pressure from the model's
catalog, calibrates the byte/token ratio with the actual usage reported by
DeepSeek, and before an oversized request produces a summary with a tool-less
call, keeping the most recent tail and the original history intact. The summary
consumes account usage. An explicit context-too-large error allows one
compaction and a new attempt; requests with an uncertain outcome are not
repeated. You can also compact by hand from **Context and memory**.

### Interruption on incomplete JSON

If DeepSeek ends a response with an incomplete tool-call JSON, Kira discards
all the calls of that response, marks the turn as interrupted and records that
execution did not start. It does not retry automatically: check the tools
already completed in the same turn and continue with an explicit request.
Authentication, quota, rate-limit and server errors stop the turn with their
message.

### Use DeeperSeeker mode

In the DeepSeek panel enable **Use DeeperSeeker** to use a DeeperSeeker
instance instead of the account. Enter the **DeeperSeeker address** (for
example `http://127.0.0.1:4000`) and the **DeeperSeeker API key**, then press
**Save DeeperSeeker connection**. The key is saved in a private file of the
profile, with permissions restricted to your user, and is no longer shown nor
included in the status or in exported settings. The address must use HTTPS, or
plain HTTP only on this computer (`127.0.0.1`, `::1` or `localhost`), with no
credentials, query or fragments: the instance receives the key and the whole
chat context. The proxy must already be running and reachable from the Kira
service.

Kira reads the catalog from the proxy and sends requests to its messages
endpoint. Tools, permissions and context remain managed by Kira; image support
and context capabilities are taken from the proxy's catalog when present. To go
back to the account, clear **Use DeeperSeeker** and press **Disable
DeeperSeeker**; the saved key stays in the profile.

## DeepSeek Web (experimental)

DeepSeek Web uses the `chat.deepseek.com` site session instead of the API. It
is a path derived from the web client, hence experimental and subject to
changes of the site.

### Connecting

In the **DeepSeek Web · direct access** panel paste the value of `userToken`
into the **DeepSeek web token (userToken)** field. To get it, open
`chat.deepseek.com` in your browser and look for `userToken` under Application →
Local Storage in the developer tools. Press **Verify and connect token**: Kira
saves it in a private file of the profile, verifies it with `chat.deepseek.com`
and shows the outcome. The token is sent only to that origin.

Then choose the **DeepSeek Web · experimental** provider and one of the two
exposed models: **DeepSeek V4 Flash (web)** or **DeepSeek Reasoner (web)**.
**DeepSeek Reasoner (web)** turns on the site's thinking mode (DeepThink);
**DeepSeek V4 Flash (web)** does not. The reasoning that the site shows appears
in the [Reasoning summary](#reasoning-summary) panel while the model works,
always separate from the answer. The catalog is defined in Kira because the
site does not offer a queryable model list.

This provider does not use the PKCE sign-in of DeepSeek Account, does not use
DeeperSeeker and does not call the account API: the two integrations are
independent and are disconnected separately.

### Tools over a text protocol

The site does not offer native function calling, so Kira mediates the tools
with a text protocol: the model writes a JSON call in a strict format, one per
message, and Kira parses it before executing anything. DeepSeek's DSML
wrappers, a single matching Markdown code block and `<tool_call>` tags are also
accepted; line breaks written directly inside long texts count as such, while
prose around the call is rejected. The notices that the site adds below replies
(such as "This response is AI-generated, for reference only.") are part of
neither the reply nor the call. If the call is malformed or names a tool that is
not registered, Kira asks for a correction that says where the call breaks, up
to two times in a row; a valid call resets the counter. If it is still invalid after two corrections, the turn ends
as interrupted without running any tool from that response, and the remote chat
is kept. Every execution goes through Kira's permissions, modes and approvals.

The browser (screenshots included) and web search, project tools, files,
commands and questions are available. **Kira's Computer** is not available.
Team and autonomous goals are not supported with this provider.

### Images

DeepSeek V4 Flash and DeepSeek Reasoner accept images on this path: Kira
normalizes the user's attachments and the browser screenshots requested by the
model, uploads them to the site's file endpoint, waits for processing and passes
the references in vision mode. The images are sent to DeepSeek under the
connected web account.

### Remote session and resumption

Kira reuses the same remote chat for all the messages of the same Kira chat,
with the same account and model, and saves the parent message identifier when
the site returns it. At the start of the remote session it sends its
instructions, the project context and the tool catalog in full; in later turns
it sends a compact request with the current state, and renews the full start if
the instructions, project memory, mode, permissions, settings or available tools
change. Tool loop continuations send only the new results.

If the site closes a request without producing any reasoning or reply (for
example because it is busy or limits the request rate, also with HTTP 429 or
5xx), Kira sends the same request again after 5, 15 and 30 seconds and shows it
in the activity: no tool ran and the remote chat did not change. The final error
reports only the type and codes of what the site signalled, never its text. If
instead the site ends the response before it is complete, or still does not
answer after the three new attempts, the turn ends as interrupted (the queue
pauses), no tool from that response runs and the remote chat is kept. On the next request, as after the Kira service stops during a
turn, the continuation restarts from the last confirmed message in the same
remote chat: the model receives an excerpt of the interrupted request and of the
tools already completed, with the instruction not to repeat them. If the remote
reference is missing or does not match the account and model, Kira opens a new
remote chat, without guessing which thread to use. It starts from Kira's
instructions, the project context and the current request; earlier messages
stay in Kira's history.

**Stop** cancels the current request right away and the turn is recorded as
cancelled.

### Disconnecting

**Remove web token** deletes the token from the profile. It does not close the
session on the site: for that, sign out of `chat.deepseek.com` in your browser.

## Claude Code · personal use (experimental)

This provider uses the Claude Code installed with Kira's optional extension and
the sign-in you have already done in Claude Code. It is intended only for your
own work on your own computer.

### Installation and sign-in

The extra is not part of the `.deb` package: it requires starting from source.

```sh
uv sync --frozen --extra claude-code
uv run kira-desktop
```

A `uv sync` without `--extra claude-code` removes the support again. The extra
also contains the Claude Code executable, which Kira uses first; if it is
missing, Kira uses `claude` on the `PATH`.

Kira has no sign-in button for Claude Code and does not read, save or forward
its credentials. If the model list reports that Claude Code is not connected,
open a terminal, run `claude` (Claude Code installed separately, which shares
the same sign-in), use `/login`, then press **Refresh** in **Models**. The
catalog and the effort levels are read from Claude Code without sending any
prompt; the status shows only the plan name.

**Do not distribute a Kira build with this provider enabled.** Anthropic's terms
ask those who build products on the Agent SDK to use API key authentication;
Claude Code's personal sign-in is acceptable only for your own use.

### Tools chosen by the permission

Kira starts Claude Code through a private launcher, with no user or project
settings, no external MCP servers and no `CLAUDE.md`: instructions and context
are provided by Kira. The project permission decides which built-in tools are
offered:

| Kira permission | Claude Code built-in tools |
| --- | --- |
| **Read only** | `Read`, `Glob`, `Grep` |
| **Project write** | the above plus `Edit`, `Write`, `NotebookEdit` |
| **Trusted host** / **Full access** | the above plus `Bash`, `WebFetch`, `WebSearch` |

Sub-agents and other built-in tools are not offered. Anything not in the list
is denied, and a check before every tool blocks file paths and patterns that
leave the project, except with **Full access**.

Claude Code's operations appear in the chat as receipts with the same localized
labels as the other providers (**Reading the file**, **Running the command**,
**Searching text**…) with the path, command, pattern, URL or search; an unknown
native tool stays literal as **Claude Code · <Tool>** and the native name is
always in the row's original data. Kira records the operations and the
artifacts of written files, without re-running anything.

Background commands are not available: Claude Code terminates with the Kira
run, so a command must finish within the `Bash` time limit. Claude Code's
automatic per-folder memory is disabled: memory is managed by Kira with
**Project memory**, the chat notes, the cross-chat excerpts and the **Research**
registry, where the model can propose records that you decide whether to accept.

### Kira tools, browser and images

Kira's tools (assistant, history, Knowledge, Research, project tools, goals,
browser, Computer and questions) reach Claude Code through an internal MCP
server called `kira`; if Claude Code reports that server as not connected, the
turn stops. Kira's browser and Computer are offered under the same conditions as
with the other providers: browser enabled in the chat, Kira tools active, chat
not a member of a Team. A successful screenshot or Computer observation goes
back to Claude Code as a PNG image (at most 2 MiB); if the image delivery
fails, the operation reports an error and the screenshot stays in the private
artifacts. Claude Code keeps those images in its own session transcript.

You can attach up to four PNG images to the request: they are sent with the
message of the current turn. Images from previous turns are not resent after a
provider change and the portable history remains text-based.

Team is not supported with this provider.

### Session, interruption and results

The Claude Code session is the chat's native reference and is resumed on every
turn; after a provider change the first turn receives Kira's portable history.
If a resumed session first reports a separate event (for example the notice of
a background command stopped with a previous run), Kira records a notice and
keeps waiting for the actual response. **Stop** sends an interruption and waits
up to 15 seconds for Claude Code's terminal result.

### Usage and resources with Claude Code

For every turn, **Usage and resources** shows the turn's tokens, those of the
last request (that is, the current context occupancy) and the model's context
window reported by Claude Code. The input includes the tokens read from and
written to the prompt cache, with the non-cached share shown separately. The
cost estimate that Claude Code computes for itself is not shown.

The **Account usage** panel shows the plan limits that Claude Code itself
reports during runs: the 5-hour session window and the weekly one, with the
available percentage, the reset time and the time remaining, plus the extra
usage status. They are the same values as Claude Code's usage indicator. Kira
does not query any service on your behalf and does not run inference to refresh
the view: without a recent turn the measure stays the one from the last run
(the date is always shown) and an already reset window is shown as cleared.
Address, organization and plan are not part of the measure and are not shown.

While Claude Code is selected, a line below the chat summarizes the last measure
received in this Kira session (**Claude plan · session X% · week Y%**) with the
**Details** button; it appears after the first turn or after a **Refresh** of
the panel. **Open claude.ai** opens your account's usage page in the browser.

Claude Code does not provide Kira with a public reasoning summary.

## Switching provider or model within the same chat

You can change model and reasoning level from the model chip without creating
another chat; the change applies from the next request. To change provider,
choose the provider in the chip's window: a window lets you check model,
reasoning and, for Direct OpenAI, account, before pressing **Continue in this
chat**. Kira offers the last choices saved for that provider; the first
time you must choose a model. If the saved model is no longer available, it is
not replaced with another one. You can cancel and keep the current
configuration.

**What is kept:** chat, project, title, recorded history, draft (text and
images), chat goal and all previous events. A draft with images remains, but
you will be able to send it only to a model that supports vision.

**What is not transferred:** the provider's internal state, private reasoning,
encrypted native state and provider-specific capabilities. Kira passes the new
provider a portable context within declared limits: with Codex, Ollama,
DeepSeek and Claude Code an excerpt of the conversation and of the tool
outcomes; with Direct OpenAI the whole paginated archive in its persistent
context. Project instructions, important notes and goal are supplied
separately. Previous tool calls are historical data: they are never re-run
during a switch.

**When it is possible:** only in an ordinary, non-archived chat, with work
stopped and the queue empty (a paused queue with pending requests blocks the
switch). Project reservations, native operations in progress, incomplete
cleanups and active Teams or delegations block it too. The chats of Team
members cannot be switched individually. The switch sends no prompt and starts
no model; Kira waits for the service's confirmation before enabling the new
turn. If something fails, the chat stays on the previous configuration and
remains usable.

**Returning to Codex** opens a new native thread with the portable context of
the same Kira chat. The previous Codex thread stays recorded but is not resumed
as if the intermediate turns had happened inside it. For the same reason, a
native branch at an intermediate point is possible only for the requests of the
active Codex generation; at the end of the chat the branch is always available
and portable branches copy the dialogue even across provider boundaries.

**Account change with Direct OpenAI:** the first transition to Direct OpenAI
binds the selected or active account; later transitions keep that binding even
if you changed the account for new chats. To use another account, choose Direct
OpenAI again in the chip's window and select the account. Kira verifies sign-in
and plan permission before confirming.

**Model or reasoning only:** changing model or reasoning level within the same
provider and account is a configuration change: the native reference (Codex
thread, Claude Code session) stays the same. DeepSeek Web keeps its remote chat
when only the reasoning level changes and opens a new one when the model
changes, because each remote chat is bound to its model. Reasoning is kept if
the new model supports it, otherwise it returns to the provider's default. Provider-specific options (Codex service tier, Ollama's
**Requested context**) stay with that provider and come back when you select it
again; the shared tool settings apply to all.

After a provider or account change, the **Always allow the agent’s actions in
this chat** consent returns to per-action confirmation.

## Usage and resources

**Usage** in the tray below the chat and **Manage usage** open the same panel.
The provider selector inside it only chooses which source you are consulting:
it does not change the chat's provider.

### What it shows for each provider

| Provider | Account data | Local measures |
| --- | --- | --- |
| Codex | Quota windows, percentages, reset, credits read from the Codex account (**Refresh**) | Tokens from the provider's last report |
| Direct OpenAI | The chat's account and the **Plan usage ↗** link; no quota exposed | Tokens recorded in the last seven days for this profile |
| Ollama | No account | Tokens, durations and context loaded from the last response; model RAM/VRAM if Ollama reports them |
| DeepSeek Account | No quota exposed | Tokens reported by the responses |
| DeepSeek Web | No quota exposed | None: the site reports no token counts |
| Claude Code | 5-hour and weekly windows reported by Claude Code, extra usage | Turn and last-request tokens, model window |

The summary always distinguishes **last request**, **conversation totals** and
**context capacity**. The capacity is the model's window (from the catalog or
from the provider), not a measure of the tokens left; with Direct OpenAI it may
appear before the first response because it is read from the account's catalog. For Ollama the view separates the size of the installed model, the
context declared by the model, the requested context, the loaded context and
the allocated memory: the device's total memory is not inferred from the
model's allocation.

The local measures of the last seven days count distinct runs and generation
cycles; the additional compaction requests are counted separately and included
in the totals. Cached input is part of the input and is not added twice. The
view states the period, the observation date and the selected account, and
flags when coverage is incomplete. The measures exclude the activity of other
applications.

**Original data** shows the JSON of the usage and resource data as received,
without the internal compaction states. A missing value stays **Not
available**; zero appears only when the provider really reports it.

### What Kira does not invent

No prices, no savings percentage, no balance inferred from tokens, no estimated
reset time. The tokens of a request do not say how much of your plan is left;
the computer's resources do not indicate the consumption of a remote model; a
percentage or a reset date does not guarantee that the next request will be
accepted.

**Open ChatGPT**, **Plan usage ↗** and **Open claude.ai** open the system
browser with the official page; the browser may be signed in to an account
different from the one selected in Kira, so verify the account shown by the
page. On WSL, if the native browser does not start, Kira tries the Windows
default browser.

## Reasoning summary

When the provider supplies it, the **Reasoning summary** panel appears below
the chat, next to the activity line. It shows the provider, a compact preview
and the status (available, partial, truncated); you can expand it, select the
text and consult the summaries of previous runs of the same chat. The text of
an old run is never presented as the current one.

| Provider | Public summary |
| --- | --- |
| Codex | Yes |
| Direct OpenAI · ChatGPT | Yes |
| Ollama | No (the raw internal reasoning stays excluded) |
| DeepSeek Account | No |
| DeepSeek Web | Yes, with DeepSeek Reasoner (web): the reasoning that the site shows |
| Claude Code · personal use | No |

The summary is the public text that the provider decides to share (for DeepSeek
Web, the reasoning that `chat.deepseek.com` shows on its own page); it is not
the private reasoning and does not certify that an operation was performed or a
goal reached: for that, the tool receipts and the recorded outcomes apply.
**Thinking…** keeps indicating processing; a summary is not a final answer.
Summaries do not enter the portable context, branches, history search or the
proof of a goal; in Markdown and JSON exports they appear labeled separately.

Per run, at most 64 parts and 64 KiB of text are kept; omitted parts are
flagged. In **Settings**, **Show reasoning summaries** hides or shows the panel
without interrupting the work and without changing model. If the provider does
not send a summary or does not support the feature, Kira says so and carries
on; past runs are not repeated to generate one.

## References

- OpenAI, Sign in with ChatGPT: [registration and sign-in](https://developers.openai.com/siwc/token-sharing-open-source/sign-in),
  [profiles and sessions](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions),
  [models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference),
  [preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations),
  [errors and recovery](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery),
  [image inputs](https://developers.openai.com/api/docs/guides/images-vision?api-mode=responses).
- OpenAI, [Codex App Server](https://developers.openai.com/codex/app-server/).
- Ollama: [chat](https://docs.ollama.com/api/chat), [tool calling](https://docs.ollama.com/capabilities/tool-calling), [loaded models](https://docs.ollama.com/api/ps).
- DeepSeek Harness: [account sign-in](https://github.com/deepseek-ai/deepseek-harness/tree/master/packages/credentials/deepseek-account-platform), [account Messages provider](https://github.com/deepseek-ai/deepseek-harness/tree/master/packages/llm/llm-deepseek-account), [discussion on the managed key after logout](https://github.com/deepseek-ai/deepseek-harness/discussions/9472).
- [Playwright](https://playwright.dev/python/), the engine of Kira's isolated browser.

Related documents: [User guide](USER_GUIDE.md),
[Browser and Computer](BROWSER_AND_COMPUTER.md),
[Context and memory](CONTEXT_AND_MEMORY.md),
[Permissions and security](PERMISSIONS_AND_SECURITY.md), [Teams](TEAMS.md),
[Autonomous goals](AUTONOMOUS_GOALS.md), [Research](RESEARCH.md),
[Installation](INSTALLATION.md).
