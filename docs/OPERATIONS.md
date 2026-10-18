# Desktop operations and recovery

This runbook applies to any supported installation or source checkout.
[STATUS](STATUS.md) records qualification and remaining limits;
[Profiles](PROFILES.md) describes source and user-data ownership.

Kira Desktop **0.13.0** uses schema 12. Upgrading schema 11 expands only the
run-lane permission constraint, retaining every table row and all existing project
permissions. Historical Teams are not armed for automatic execution. Follow the idle-state, backup and shutdown procedure below before
changing application code. Versions before 0.13.0 cannot open schema 12; rollback
requires its code and a compatible profile backup.

## Start the application

After installing the package matching your platform, use the application menu
or run these commands from any directory:

```sh
kira-desktop
kira-desktop --version
kira-desktop diagnostics
```

For development, open a terminal in the root of your checkout, wherever you
cloned it, and use:

```sh
uv sync --frozen
uv run kira-desktop
```

Replace `kira-desktop` with `uv run kira-desktop` in the remaining commands when
using a checkout. A graphical session is required for the GUI, including WSLg
or another configured display when running in WSL.

The working directory does not implicitly select/create a project or start an
agent. Select the project/chat in the interface. After a system or WSL restart,
launch the application again; the GUI connects to or starts its service. Recover
interrupted work from its recorded state rather than automatically replaying
operations.

## Data paths and separate profiles

Without a nonempty `KIRA_HOME`, paths are resolved from the current user's XDG
environment. These are standard defaults, not paths to a maintainer's machine:

| Data | With the XDG variable set | Default when unset |
| --- | --- | --- |
| SQLite and managed files | `$XDG_DATA_HOME/kira/` | `$HOME/.local/share/kira/` |
| GUI configuration | `$XDG_CONFIG_HOME/kira/ui.json` | `$HOME/.config/kira/ui.json` |
| Unsent chat drafts and reusable message history | `$XDG_CONFIG_HOME/kira/composer.json` | `$HOME/.config/kira/composer.json` |
| State / detached-service log | `$XDG_STATE_HOME/kira/` | `$HOME/.local/state/kira/` |
| IPC socket | `$XDG_RUNTIME_DIR/kira/service.sock` | `runtime/service.sock` beneath the resolved state directory |

Set XDG directories to absolute paths. `KIRA_HOME`, when nonempty, takes
precedence over all of these variables and contains `config/`, `data/`, `state/`
and `runtime/` children. Choose an absolute location outside the checkout for a
persistent profile, and use the same environment for its GUI, service,
diagnostics, backup and shutdown commands. Keep the runtime path short enough
for a Unix socket.

For a disposable development instance, run from the checkout root:

```sh
export KIRA_HOME="$(mktemp -d -t kira-dev.XXXXXXXX)"
uv run kira-desktop
```

The exported path remains selected in that terminal. For foreground debugging,
run `uv run kira-desktop service`, then connect the GUI in a second terminal
with the same `KIRA_HOME` value. Stop the test service before discarding its
profile. Temporary directories are not suitable for long-term user data.

When using the installed systemd service with customized XDG variables, its
environment must match the client's. A shell-only override does not update the
systemd user manager's environment. `KIRA_HOME` selects a detached service
directly and avoids that mismatch for an isolated instance.

Project directories are separate from application data. Codex accounts remain
in the provider's own storage; Kira does not migrate their credentials.

The per-chat browser action consent is stored in session settings and is included
in database/profile backups. It can be revoked while a turn is active without
closing the browser. Changing this preference is separate from updating running
application code, which still requires the idle procedure below.

## Package installation and upgrades

Use the package for the host's Python ABI. These examples assume packages built
in the current checkout; if you downloaded a package, use its actual location:

```sh
# Ubuntu 24.04 / Python 3.12
sudo apt install --no-remove ./dist/kira-desktop_0.13.2_amd64.deb
# Or Ubuntu 26.04 / Python 3.14
sudo apt install --no-remove ./dist/resolute/kira-desktop_0.13.2_amd64.deb
```

Launch with `kira-desktop`. Package installation does not move data or convert a
custom source launcher; use the intended client and profile consistently.

Before changing running code, pause each project's pending queue, finish active
runs/teams and provider chat operations, and create a database backup with the
current client. Confirm that unsent drafts have saved successfully, then close
GUI windows normally so pending draft saves finish. Run:

```sh
kira-desktop stop-service
```

This refuses active work. Verify the old process exited and its ownership lock
is released before updating or relocating files. Back up the stopped profile's
config, data and state directories, including drafts, attachments, artifacts and
persistent browser profiles. Runtime sockets and locks are not durable backup
content. Relaunch matching client and service code with the same intended profile.
Verify the selected project/chat, provider, model, reasoning effort, preferences
and retained drafts after reopening. Queued work stays paused until explicitly
resumed. Do not unlink a live socket or erase the database to bypass version
negotiation.

Packages bundle locked dependencies and Qt; Apt supplies matching Python and
system display libraries. They do not run pip as root, download models, enable
linger or modify global Codex configuration. Maintainer scripts never terminate
active work. Removal preserves project directories and private user data.

### Application version rollback

Keep the prior code/package and a consistent matching profile/database backup.
A newer schema cannot be forced into an older binary. Stop the idle service,
retain the newer data separately, restore a compatible backup and its referenced
files, then start the older client. A SQLite-only backup is not a complete
attachment/project backup. Do not merge divergent profile copies by overwriting
individual database files.

## Backup and preservation

### Isolated browser setup

The current source's direct browser requires an explicit
`kira-desktop browser-install` (or `uv run kira-desktop browser-install` from the
checkout) with the intended KIRA_HOME/XDG profile. This downloads only Kira's
pinned Chromium engine beneath the resolved data directory's `browser-engine/`.
It does not change the system browser or remove older engine revisions.

Desktop 0.9.1 runs that Chromium with its native UI on a private Xvfb display.
The full browser surface is shown inside Kira only under manual control; agent
observations remain page-only. Native packages declare `xvfb` and `libxtst6` in
addition to Chromium's existing system libraries. Install those runtime packages
separately for a source deployment; `uv sync` does not install OS libraries.

In WSL, the optional authentication bridge is detected through Windows interop,
Windows PowerShell and a supported Windows WebAuthn API. Supported requests wait
for explicit user takeover or Continue authentication; a model-requested manual
handoff alone does not authorize the native operation. Windows owns the
secure Hello/PIN dialog outside the Linux image; Kira does not open an external
Windows browser or copy its profiles, cookies or credentials. On Linux without
that bridge, authentication depends on the authenticators available to isolated
Chromium. PRF, unsupported extensions/options and cross-origin requests are not
silently downgraded. See [authentication limits](WEB_BROWSER.md#passkeys-and-the-windows-authentication-bridge).

The Web dialog enables optional persistent profiles per chat/account/provider
generation under `browser-profiles/`. These contain website login state: keep
them private and include them in a stopped-profile backup. Temporary profiles
under `browser-temporary/` are removed only after verified worker cleanup.
Screenshot artifacts and their immutable image copies remain in the selected
data directory. SQLite-only backup excludes these files and browser profiles.
Browser workers belong to chat browser actors and remain open across completed
turns. Before an upgrade, finish or cancel any authentication prompt, then return
manual browser control or close the browser in its pane and finish active model
work. An authentication request tracked by Kira disables returning control until
it ends. Service shutdown refuses active manual
browser control and cleans independently owned browser workers before exit.
Closing the GUI stops view subscriptions; it does not implicitly delete browser
profiles. The ordinary idle/backup/shutdown rules apply. See [Web and browser](WEB_BROWSER.md).

### Consistent data backup

```sh
kira-desktop rpc database.backup
```

This uses SQLite's backup API and returns a private backup path. Copying only a
live `kira.sqlite3` can omit committed WAL data. For a complete application-state
copy, stop the idle service and back up the resolved config/data/state
directories plus referenced files; runtime sockets and locks are not durable
content. Store backups outside the source repository and back up project
directories separately. Restore first to an isolated profile and verify the
expected records and files before replacing active state.

Kira Desktop 0.13.0 uses schema 12. Opening an older supported database creates a
private consistent backup before migration; existing conversations
and provider references remain intact. Earlier-schema binaries cannot open the
upgraded database. Retain the matching old profile and code if rollback is required.

Project tool source revisions, contracts and execution receipts live in the
selected profile database. Keep that database with the complete profile backup.
The original project scripts remain project files and need a separate project
backup. Pending tool processes participate in the existing run/reservation idle
checks. An interrupted invocation is recorded and never replayed during startup.

Desktop 0.8.2 retains schema 8 but adds computer-tool identities and image results
to direct conversation context. Database-schema compatibility alone does not
qualify an older adapter to continue that newer tool history. Test rollback in
an isolated copy, and preserve all work recorded after the original backup.

Composer image attachments are immutable private PNG snapshots under
`image-attachments/` in the resolved data directory. Other path-based clipboard
inputs can use `attachments/`; copying path text from an existing file does not copy
that file. Database backup alone excludes both image directories, captures and artifact
bodies. Stored file references may contain absolute paths: copying a profile
to a different location or machine does not rewrite them. Preserve the referenced
files and verify their accessibility when planning a migration.

## Preferences, chats and providers

GUI preferences in ui.json include density, onboarding, font size, reduced motion,
model controls, details, close confirmation, local opener and the default
permission for new projects. They do not change existing permissions or provider
configuration. Last-selected chat is saved. Chat composer drafts and the rolling
history of the last 200 service-acknowledged messages use `composer.json`, a
separate GUI-owned private file. Writes are atomic, debounced by 300 ms and
flushed on orderly close. This file is not included in a SQLite-only backup.
It has a 2 MiB aggregate limit, at most 100 drafts and 262,144 UTF-8 bytes per
text entry. The oldest reusable history entries may be trimmed to fit; drafts
are preserved. A draft that still cannot fit produces an unsaved-draft error;
another draft is not silently removed. An invalid existing file is preserved and is not
automatically overwritten. Preserve a private copy before manual recovery.

The same file retains up to 128 pending action identities for queue submission,
steering, branching and compaction. Each stores a request UUID and argument
fingerprint before dispatch, without another copy of the prompt. Explicit retries
reuse the identity across GUI restart or chat/project changes. An unknown effect
is not automatically resubmitted. After inspecting chat, queue and activity,
**Consenti nuove richieste separate… / Allow new separate requests…** lets the
user clear the local retry guard after confirmation. This sends no request;
a later new action can duplicate an earlier effect whose outcome was uncertain.

Each project contains ordinary named chats. Context excerpts use only that
project's other ordinary chats, within documented bounds; the project can opt
out; archived chats are excluded from new shared excerpts. Archive/restore changes
visibility, not retained history. Full chat Markdown exports include the readable
transcript and retained activity; artifact paths remain references to separate
files. Team state and persistent questions live in the service database. Closing
a question popup does not delete it. An answer saved is distinct from delivery.

Pending queues, chat objectives, branch identities and native context operations
are service-owned SQLite records. Closing the GUI leaves the queue running while
the service remains alive. Service restart pauses future queue dispatch; choose
**Riprendi coda / Resume queue** after reviewing interrupted work. Failure or
cancellation also pauses dispatch. Queue pause does not cancel the active turn.
Uncertain steering, branching or compaction is never replayed automatically.
Provider child ownership is recorded before native thread operations, and cleanup
is verified before releasing their project reservation. Unverified cleanup keeps
the project blocked; a stale PID alone is never authority to terminate a process.
An active objective supplies context to later explicit turns; it does not start
work on launch. See [chat controls](WORKBENCH.md).

Configure Codex's installed executable and official login in the app. Use an
absolute executable path if the desktop/systemd PATH cannot find it. Configure
Ollama's explicit endpoint and installed models separately; no weights are
fetched automatically. [Backend protocol and limits](BACKENDS.md).

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Wrong projects / empty history | Check the client and KIRA_HOME/XDG profile selected; do not create a replacement store over old data |
| Version mismatch | Finish work, close the GUI, stop the old idle service, then start matching code |
| WSLg EGL/Zink warnings | Default software UI rendering is retained; explicit renderer overrides take precedence |
| Backend unavailable | Check configured executable/endpoint and official account state; catalog presence does not prove entitlement |
| No Windows drop event | Use paste or Allega; WSLg must deliver the event before Kira can process it |
| Native action unavailable | Install/configure the host tool separately or use available actions; a requested launch is not rendered-window evidence |
| Service startup failure | Read diagnostics and logs; never delete a live socket/database or replay an uncertain task as a repair |
| Pending requests do not start after restart | Inspect the queue and previous outcome, then explicitly resume dispatch |
| Draft not saved | Preserve the visible text outside the app and inspect the reported composer-file error before closing |
| Browser tool shows an ownership wait | Use Return to Kira after manual work or Stop to cancel the model request; neither takeover nor retry is automatic |
| Authentication pending | Complete or cancel the Windows prompt; the bridge's tracked authentication must end before Return to Kira or Done becomes available. Native Linux choosers are shown in the full manual surface without this pending-status tracking |
| Native browser view unavailable | Preserve manual work and inspect the visible surface error; missing `xvfb`/`libxtst6` or failed capture is not repaired by displaying page-only pixels |
| Passkey method unavailable | Check the authenticator and bridge requirements; headed mode alone does not make Windows Hello available on Linux, and a site login needs its own user verification |
| Native chat operation unavailable or uncertain | Check the provider result; do not assume a new thread, successful compaction or model consumption from an accepted request alone |

Systemd logs: `journalctl --user -u kira-desktop.service`; detached startup writes
`service.log` beneath the resolved state directory. `kira-desktop doctor`
observes provider and resource state; `kira-desktop diagnostics` is a
privacy-filtered local report. Review exported reports before sharing them:
filtering does not guarantee removal of every private detail.

Closing the GUI is not logout, WSL shutdown, service shutdown or cancellation.
When external effects are uncertain, inspect project files and recorded results
before explicitly continuing. Source/UI and installed-package tests remain
separate from authenticated model execution; see [STATUS](STATUS.md).

## Assistant and Team verification

Chat-native delegation is service-owned. Pending requests start only after the
origin turn succeeds and its provider cleanup is confirmed. No pending handoff
or uncertain operation is automatically replayed after restart. Inspect the
origin chat's Team card and full Team view, then use explicit continuation when
appropriate. Cancellation of a pending handoff does not fabricate a team run.

The Codex connection uses an ephemeral private MCP relay attached to that turn;
its descriptor is removed on normal completion/cancellation. It is separate from
global provider configuration and authentication. Do not copy a descriptor into
a persistent configuration or attempt to reuse one after the run. Existing
threads retain their provider reference.

For an authorized real-provider acceptance test in a disposable profile:

```sh
uv run python scripts/live_assistant_check.py --model YOUR_MODEL --effort YOUR_EFFORT --dry-run
uv run python scripts/live_assistant_check.py --model YOUR_MODEL --effort YOUR_EFFORT
```

The second command consumes provider resources. It delegates a synthetic file
read to a two-member team, verifies actual mailbox evidence and unchanged file
bytes, then checks continuity in the origin chat. Reports stay under ignored
`build/`; they must not be added to the public repository without review.


## Direct ChatGPT account lifecycle

The `openai_chatgpt` backend uses a service-owned OAuth manager and Responses
HTTP/SSE transport, without Codex App Server. Credential files are private
configuration and must not enter project exports or public reports. Existing
session backend ownership remains intact. See [OpenAI direct](OPENAI_DIRECT.md)
for account binding, permissions, context and recovery boundaries.


## Team correction update

The 0.10.1 Team correction adds no migration and does not rewrite historical Team
conclusions. Restart does not resume stopped Teams automatically. Continue
explicitly from the original chat or Team view after reviewing recorded outcomes.
New continuation tool history needs compatible adapters; schema equality alone
does not qualify rollback. Preserve a matching full-profile backup.


The 0.10.2 continuation limit adds no database migration. Existing Teams keep
their saved limits. GUI chat continuation receipts may include parent Team and
selected limit metadata; retain composer/pending-intent files in the full profile
backup. Unknown operations are not replayed automatically after restart.


### Native workspace update (0.11.0)

The interface update retains schema 10 and requires no migration. Upgrading an
active installation still requires verified idle state, normal GUI/service
shutdown and a consistent complete-profile backup. Existing chat drafts,
provider/model/effort, browser consent and appearance preferences are preserved.
Panel consultation and goal metadata reads do not start provider work. Validate
new application resources from an independently installed artifact; synthetic
UI tests do not establish live provider behavior.

### Visual refinement update (0.13.1)

This patch keeps schema 12 and changes the UI only. A source-backed installation
can load QML/Python from its checkout, so prepare changes separately while it is
in use. A prepared source archive or wheel is not an activation. Honor an explicit
request to keep the running application unchanged: do not copy into active source,
install, close or restart it. Later activation still requires verified idle state
and a consistent backup preserving drafts, attachments and configuration.

### Compaction reader update (0.13.2)

Schema 12 and original conversation records remain intact. A previously
interrupted turn requires a new explicit request; installing the update never
replays its actions. Prepare the change separately, then use fresh idle checks,
a consistent complete backup and normal shutdown for later activation. No account,
model, reasoning effort or billing route is changed by this recovery.


## Autonomous-goal upgrade

Desktop 0.14.0 migrates schema 12 to 13 additively after a consistent backup.
Existing saved goals remain manual. Before installation, wait for active requests
and Teams to stop and pause any armed autonomous objective. Service restart
retains goal records and checkpoints but requires explicit resume. Never replay
an uncertain cycle or activate an old goal as part of package verification.


## Public summary display

Desktop 0.14.1 adds the bounded [public provider summary channel](REASONING_SUMMARIES.md).
It preserves private native reasoning and ordinary execution contracts. Schema 13
is unchanged; activate an update only after verified idle state and a consistent
backup. No active response or historical work is replayed to produce a summary.

## Desktop 0.15.0 identity upgrade

Schema 14 adds user-owned identities, versioned experience, operation receipts
and run snapshots. Migration preserves previous tables, chats, goals and project
settings; no default personalized identity is selected for historical chats.
Use the same verified-idle, consistent-backup and graceful shutdown procedure
as other updates. Back up the complete selected KIRA_HOME/XDG profile, including
UI drafts, attachments and browser state; copying a live database alone is insufficient.
Older binaries reject schema 14. Rollback requires the retained compatible backup,
with a fresh backup of any newer user work before restoration.
