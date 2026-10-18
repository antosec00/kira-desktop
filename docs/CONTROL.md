# Desktop control and automation boundaries

Kira Desktop is a native AI workspace. Its user service performs project work
through configured providers. It also exposes explicit, fixed native desktop
actions for opening project tools, inspecting diagnostics and capturing the
screen at the user's request.

Desktop 0.10.0 adds [knowledge discovery and project tools](KIRA_IDENTITY.md).
Knowledge references are inert, scoped data. Project Python tools execute only
inside an authorized ordinary run in Execute/trusted-host mode, after registering
and testing their exact immutable revision. The service records ownership before
releasing code to a child worker. The working directory is not a sandbox.
The GUI can inspect and enable/disable tools without running them. This permission
is independent of browser click consent and imports no browser credentials.

## What the application controls

| Route | Responsibility and evidence |
| --- | --- |
| Chat / provider | Starts authorized project runs, observes tools, streams results |
| Team | Schedules owned agent assignments and persists their communication |
| Ferma / Ferma team | Requests cancellation; final state confirms the outcome |
| Riprendi / Riprendi lavoro | Opens the workspace dialog; launching saved folder/terminal/editor choices remains an explicit action |
| Ctrl+K | Searches projects and supported fixed native actions |
| Schermate | Explicit native capture, registered only after a complete PNG/hash |
| Diagnostics / backup | Private whitelisted report or consistent SQLite backup |
| Web (OpenAI direct) | Hosted research evidence, isolated browser and private screenshot previews |

Native actions use fixed executables and argument vectors with cleaned Qt/library
environments. User parameters do not choose an arbitrary executable or shell
fragment. Availability is detected and missing programs are reported. Launches
report requested, not proof that a window became visible. Applications launched
in independent user scopes can outlive Kira's service.

The [workspace contract](V02_IMPLEMENTATION.md#service-apis) specifies native
requests, idempotency and unknown outcomes. These action APIs are explicit local
user operations, not additional model tool vocabulary. They do not increase a
project's permissions.

## CLI and local IPC

The packaged command is `kira-desktop`. For a source checkout, use
`uv run kira-desktop` from its root with the same arguments. For example, after
package installation:

```sh
kira-desktop diagnostics
kira-desktop rpc project.list
kira-desktop actions --project PROJECT_ID
kira-desktop workspace PROJECT_ID --open
```

Replace PROJECT_ID with an actual returned identifier. Inspecting diagnostics or
project metadata does not start inference. Opening a workspace is an explicit
native launch request. The socket is private Unix IPC, not a network control
server. Use one consistent KIRA_HOME for all commands targeting a profile.

`trusted_host` provider work can execute commands as the user. That authority is
separate from this fixed native-action list and is never described as containment.
Built-in file tools and Codex's native sandbox follow their own documented
permission contracts. User messages, tool output and file paths do not confer
extra permissions.

## Input and external tools

Chat and free-form question inputs accept file paths from paste/drop/picker and
private imported PNGs. A Windows-to-WSLg drag must first reach Qt; the application
cannot handle a drag event that the compositor does not deliver. Paste or Allega
are alternatives. See [attachment behavior](ATTACHMENT_INPUT.md).

The built-in desktop action catalog covers the operations listed above. Project
execution follows the selected provider and tool permissions.

For startup, data, shutdown and recovery use [OPERATIONS](OPERATIONS.md).
Observed run events are evidence of actual provider activity; a model's textual
claim or a requested native launch alone is not proof of completed effects.


## Direct provider tools

The `openai_chatgpt` backend uses a service-owned OAuth manager and Responses
HTTP/SSE transport, without Codex App Server. Credential files are private
configuration and must not enter project exports or public reports. Existing
session backend ownership remains intact. See [OpenAI direct](OPENAI_DIRECT.md)
for account binding, permissions, context and recovery boundaries.

The [web/browser contract](WEB_BROWSER.md) adds model-callable public-page
reading and explicitly approved browser interaction to OpenAI direct. Its worker
is owned by a private chat browser actor and survives completed turns. Desktop
0.9.1 runs headed Chromium on a private Xvfb display. The agent sees and operates
pages through CDP; the complete browser UI and its native dialogs are exposed
inside Kira only during manual control. Manual input and view frames are
transient and are never model input or durable conversation events. Browser
preferences never grant arbitrary shell access. Persistent website login is an
explicit per-chat opt-in using manual credential entry in a separate profile.

An agent tool waiting for manual control has a dedicated status and no work
animation. Return to Kira explicitly releases that wait; Stop cancels the model
request. There is no automatic takeover or replay. Manual input belongs to the
published surface identity; tab, size or ownership changes discard stale input.
A failed native view cannot be replaced by a misleading page-only frame.

The optional WSL authentication bridge uses Chrome's `webAuthenticationProxy`
extension API and the Win32 Windows WebAuthn API. It waits for explicit user
takeover or Continue authentication before invoking native authentication;
a model-requested manual handoff is insufficient. Windows owns the secure Hello/PIN
dialog outside the Linux capture; the model cannot see or operate it. Returning
control is disabled while a tracked authentication request is pending. Its
completion is not evidence of successful website login. Keep control declines
automated continuation without closing the user's browser.

No external Chrome profile, cookies or saved credentials are imported. WebAuthn
request/response bodies, PINs and biometrics do not enter model tools or durable
events. Unsupported PRF/extensions and cross-origin ceremonies are rejected.
Linux can use only authenticators available to the isolated browser; headed mode
does not itself provide Windows Hello. Native Linux choosers appear in the manual
surface but are not tracked by the Windows pending-status banner.
[Authentication boundaries](WEB_BROWSER.md#passkeys-and-the-windows-authentication-bridge)
describe the supported route and its qualification limits.

Desktop 0.8.2 adds Kira-owned `computer.observe`/`computer.act` visual tools to
that same browser actor. Coordinates belong to an observed browser viewport;
they cannot move the general desktop pointer or operate other applications.
Click/double-click/type/key/drag use the chat’s selected browser consent policy
and retain project/mode checks. Desktop 0.9.2 adds a persistent, revocable grant
for that chat; it does not authorize host commands or native authentication. A stale observation must be replaced before acting, and
uncertain effects are never replayed automatically. See [Kira Computer](WEB_BROWSER.md#kira-computer-visual-tools).


Desktop 0.10.1 adds run-bound Team continuation from ordinary chats. It starts only
after origin cleanup and never replays uncertain earlier assignments. Mailbox
messages do not schedule execution. The coordinator submits explicit tasks;
multi-member completion requires recorded worker execution. Corrective planning
respects the configured coordinator-round budget and preserves previous outcomes.


Desktop 0.10.2 allows an explicit continuation-round limit. Zero removes only the
coordinator-round ceiling. It does not change project permissions, model/effort,
account, cancellation, no-progress protection or replay rules. Editing the GUI
field does not execute anything; the selected value belongs to the subsequent
explicit resume request and new child Team.


Desktop 0.11.0 adds native nonmodal consultation panels and compact recorded-tool
receipts. Panel navigation, resizing, receipt expansion and settings category
selection are GUI actions; they do not execute tools or change their results.
The saved goal anchor uses a scoped `goal.get` read without invoking the model.
Browser view changes retain manual ownership and the existing explicit return
control. See [workspace presentation](CHAT_LAYOUT.md).

## Live conversation and Team results (0.12.0)

`run.steer` is a durable, run-scoped text delivery operation; direct providers
consume it at paired request/tool boundaries without cancelling the current tool.
One same-project read-only chat may run alongside a Team. Team result delivery
uses a service-origin run with a durable receipt and the original chat/provider
binding. User queue entries take precedence. Stop/restart and uncertain cleanup
never cause automatic replay. See [the canonical contract](LIVE_CONVERSATION.md).

## Full access (0.13.0)

The explicit `full_access` project permission extends Kira file tools to external
user-accessible paths and retains current-user host commands. Prior permissions
are unchanged. Restricted modes remain read-only. Browser consent is independently
revocable and valid live ask/always changes no longer invalidate the guidance
fingerprint. See [Full access](FULL_ACCESS.md) for the canonical boundaries.

## Visual states (0.13.1)

Status colors, symbols, motion and sidebar markers derive from scoped recorded
state. They are presentation only: no state transition, command, permission
change or provider request is triggered. Green tool completion does not certify
the task objective. Existing cancellation and uncertain-effect semantics remain
authoritative; see [Chat layout](CHAT_LAYOUT.md).

## Native context recovery (0.13.2)

Only local output-envelope validation failure in the auxiliary no-tool native
fold may enter the structured summary path. Original actions are not replayed;
normal inference, auth, quota, transport and cancellation failures retain their
existing boundaries. Diagnostics retain fixed shape metadata, never raw rejected
provider content. See [Direct context](DIRECT_CONTEXT.md).


## Autonomous objectives

Explicit `goal.start` authorizes successive service-owned cycles in the same chat.
`goal.get` is read-only; pause/resume and Stop remain explicit controls. Existing
manual objectives never start automatically. See [Autonomous goals](AUTONOMOUS_GOALS.md)
for scheduling, completion evidence, provenance and restart boundaries.


## Public summary display

Desktop 0.14.1 adds the bounded [public provider summary channel](REASONING_SUMMARIES.md).
It preserves private native reasoning and ordinary execution contracts. Schema 13
is unchanged; activate an update only after verified idle state and a consistent
backup. No active response or historical work is replayed to produce a summary.

## Personalized identity controls

[Anima di Kira](KIRA_SOULS.md) adds GUI-owned profile selection and user-level
preferences. They do not change tool permissions, provider/model/effort or arm
autonomous work. A model may propose source-linked experience for review; it
cannot select another identity, accept its own proposals or edit its core.
Profile edits take effect at the next admitted turn. Rebinding requires idle
project work and a paused objective, and retires native provider state while
retaining the current chat transcript. Another identity does not inherit private
experience or old automatic excerpts from a differently identified chat.
