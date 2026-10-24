# Installation, data, backup and upgrade

*[Leggi in italiano](../it/INSTALLAZIONE.md)*

This guide explains how to install Kira Desktop on Ubuntu, from the package or
from source, where your data ends up, how to make a full backup of it and how to
upgrade or roll back to a previous version without losing anything. It is meant
for people who use Kira and for those who administer the installation; the
everyday use of the app is described in the [User guide](USER_GUIDE.md).

## Requirements and supported platforms

Kira Desktop is a 64-bit (amd64) Linux application distributed as two `.deb`
packages, one for each supported Ubuntu release:

| System | System Python | File on the Releases page | Built from the checkout |
| --- | --- | --- | --- |
| Ubuntu 24.04 LTS (noble) | 3.12 | `kira-desktop_ubuntu24.04_amd64.deb` | `dist/kira-desktop_<version>_amd64.deb` |
| Ubuntu 26.04 LTS (resolute) | 3.14 | `kira-desktop_ubuntu26.04_amd64.deb` | `dist/resolute/kira-desktop_<version>_amd64.deb` |

The two packages contain the same application code and the same Qt/PySide6
6.11.2 libraries, but each is bound to the Python interpreter of its own
system: **they are not interchangeable**. The package for Ubuntu 26.04 requires
Python 3.14 and does not install on Ubuntu 24.04; the one for Ubuntu 24.04
requires Python 3.12.

Kira works both on native Ubuntu and inside WSL2 on Windows, provided that a
graphical session is available: in WSL2 you need WSLg (or another configured
display); the steps for Windows are in [On Windows with WSL](#on-windows-with-wsl).
The reference desktop is KDE Plasma on Wayland; other desktop environments work,
but it is on Plasma/Wayland that the interface is checked most carefully.

The AI providers are configured after installation, inside the app, as
described in [Providers](PROVIDERS.md).

## Installing from the package

Ready-made packages are attached to every version on the repository's
[Releases](https://github.com/antosec00/kira-desktop/releases) page, together with a `SHA256SUMS`
file with their checksums. Download the package for your system into an empty
folder, check its checksum and install it with `apt`, which resolves the
required system libraries on its own. For Ubuntu 24.04:

```sh
cd "$(mktemp -d)"
wget https://github.com/antosec00/kira-desktop/releases/latest/download/kira-desktop_ubuntu24.04_amd64.deb \
     https://github.com/antosec00/kira-desktop/releases/latest/download/SHA256SUMS
sha256sum --check --ignore-missing SHA256SUMS
sudo apt install --no-remove ./kira-desktop_ubuntu24.04_amd64.deb
```

For Ubuntu 26.04 replace `ubuntu24.04` with `ubuntu26.04` in the `wget` and
`apt` lines. The `latest/download` links always point to the most recent
published version; earlier versions remain on the Releases page. `sha256sum`
must answer `OK`: if it reports an error, do not install the file; download it
again.

Every version also has `SHA256SUMS.sig`, the Ed25519 signature of `SHA256SUMS`
made with Kira's release key. To check that the checksums are the published
ones, download the signature and the repository's public key
`packaging/release-signing-key.pub.pem` into the same folder and verify the
signature before `sha256sum`:

```sh
wget https://github.com/antosec00/kira-desktop/releases/latest/download/SHA256SUMS.sig \
     https://raw.githubusercontent.com/antosec00/kira-desktop/main/packaging/release-signing-key.pub.pem
openssl pkeyutl -verify -pubin -inkey release-signing-key.pub.pem -rawin -in SHA256SUMS -sigfile SHA256SUMS.sig
sha256sum --check --ignore-missing SHA256SUMS
```

`openssl` must answer `Signature Verified Successfully`; otherwise do not
install anything. [Updating from Kira](#updating-from-kira) makes the same check
with the key built into the app.

The `./` prefix is required for `apt` to recognize a local file; with
`--no-remove`, `apt` stops instead of removing other packages. If `apt` warns
that the download is performed "unsandboxed as root" because the `_apt` user
cannot read the file, the warning is harmless: it only depends on the
permissions of the folder where you downloaded the package (the one created by
`mktemp -d` is readable only by you). If Kira is already installed, follow the
[upgrade procedure](#upgrading) instead.

If you built the package yourself (see
[Building the packages](#building-the-packages)), install the file produced in
the checkout:

```sh
sudo apt install --no-remove ./dist/kira-desktop_<version>_amd64.deb            # Ubuntu 24.04
sudo apt install --no-remove ./dist/resolute/kira-desktop_<version>_amd64.deb   # Ubuntu 26.04
```

After installation start Kira from the application menu or with `kira`.

The package installs the `/usr/bin/kira-desktop` command and its short name
`/usr/bin/kira`, the application code with all its Python dependencies (Qt
included) in `/opt/kira-desktop/`, the **Kira Desktop** entry of the application
menu with its icon, the systemd user service
`/usr/lib/systemd/user/kira-desktop.service` and, in
`/usr/share/doc/kira-desktop/`, the documentation with the same layout as the
repository (`README.md`, `docs/it/`, `docs/en/`, `docs/images/`), the licenses
and the list of dependencies.

The package is deliberately conservative. It does not run `pip` as root and
does not touch the system Python: all the libraries are inside
`/opt/kira-desktop/lib`. It does not download models or browser engines, does
not enable systemd `linger` for your user and does not modify Codex's
configuration or credentials, which remain the provider's property. It has no
installation or removal scripts: installing, upgrading or removing the package
neither stops nor restarts the service and never deletes your data (see
[What remains after uninstalling](#what-remains-after-uninstalling)). For this
very reason, if Kira is already installed, upgrade or remove the package only
after stopping the service as in the [upgrade procedure](#upgrading): a service
left running stays on the previous code and the new interface reports it as a
different version.

## On Windows with WSL

On Windows Kira runs inside WSL2, in an Ubuntu distribution, and shows its
windows through WSLg. WSLg is included in Windows 11 and, on Windows 10 (build
19044 or later), in the version of WSL distributed through the Microsoft Store.

1. Open PowerShell as administrator. If WSL was already installed, first run
   `wsl --update`, which brings it to the latest version. Then install Ubuntu
   24.04 (needed only once; Windows may ask for a restart):

   ```powershell
   wsl --install -d Ubuntu-24.04
   ```

   On first start Ubuntu asks you to create a Linux user. Check with
   `wsl --list --verbose` that the distribution uses version 2 of WSL: WSLg does
   not work with version 1, and `wsl --set-version Ubuntu-24.04 2` converts it.
2. Open the Ubuntu 24.04 terminal from the Start menu and install the package
   for Ubuntu 24.04 as described in
   [Installing from the package](#installing-from-the-package).
3. Start Kira with `kira` from the Ubuntu terminal. With WSLg the **Kira
   Desktop** entry also appears in the Windows Start menu, where you can open it
   like any other application.

If `wsl --list --online` also lists Ubuntu 26.04, you can use that distribution
with the package for Ubuntu 26.04. Typical WSLg warnings and limits are
described in [Troubleshooting](#troubleshooting).

## Running from source with `uv`

If you want to use the latest version of the code or contribute to development,
you can run Kira directly from the checkout. You need
[`uv`](https://docs.astral.sh/uv/), which downloads the suitable Python
interpreter on its own and creates an isolated virtual environment in the
project folder.

```sh
git clone https://github.com/antosec00/kira-desktop.git
cd kira-desktop
uv sync --frozen
uv run kira
```

`uv sync --frozen` installs exactly the versions recorded in `uv.lock`, the
same as the packages. The **Claude Code · personal use** (experimental)
provider requires an additional dependency that is not included in the `.deb`
packages; from source you enable it with the `claude-code` extra:

```sh
uv sync --frozen --extra claude-code
```

`uv` manages only the Python libraries. The system libraries that the `.deb`
package declares as dependencies must be installed separately: Qt's for the
window, Chromium's and those of the isolated browser's virtual display (`xvfb`,
`libxtst6`, `libxdamage1`). On an Ubuntu desktop many are already present; in
WSL or on a minimal installation install them all:

```sh
sudo apt install libglib2.0-0t64 libegl1 libgl1 libopengl0 libfontconfig1 \
  libdbus-1-3 libgssapi-krb5-2 libharfbuzz0b libxkbcommon0 libxkbcommon-x11-0 \
  libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 \
  libxcb-xinerama0 libxcb-xkb1 libxcb-shape0 libxcb-randr0 libxcb-sync1 \
  libxcb-render0 libxcb-shm0 libx11-xcb1 libxcb1 libx11-6 libxext6 \
  libwayland-client0 libwayland-cursor0 libwayland-egl1 xdg-utils libnss3 \
  libnspr4 libatk1.0-0t64 libatk-bridge2.0-0t64 libatspi2.0-0t64 libcups2t64 \
  libdrm2 libgbm1 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
  libasound2t64 libcairo2 libpango-1.0-0 xvfb libxtst6
```

The `.deb` package installs them on its own through `apt`; from source you have
to take care of it yourself, otherwise the interface or the isolated browser
does not open.

All the commands in this guide also apply from source: replace `kira-desktop`
with `uv run kira-desktop` (or `uv run kira`), run from the root of the
checkout. A source checkout never uses the package's systemd unit: when needed,
it starts its own background service. Without `KIRA_HOME`, however, the
checkout and the package use the same profile and therefore a single service:
if the package's service is already running, the checkout connects to it or
reports a different version. To try the checkout's code next to an installation
in use, use a separate profile with `KIRA_HOME`.

Remember that from source the interface and the service load the checkout's
code: do not modify the files while Kira is in use; prepare the changes
separately and activate them with the [upgrade procedure](#upgrading).

## First launch and useful commands

Start Kira from the application menu or with `kira` (the full name
`kira-desktop` is equivalent). The interface looks for the Kira service (the
process that owns the database and talks to the providers): if it is not
running, it starts it, through systemd in the package installation or as a
background process from source or when you use a separate profile with
`KIRA_HOME`. The service keeps working even when you close the window, as long
as your user session stays open.

The folder from which you launch the command has no effect: it neither selects
nor creates a project. Choose the project and the chat from the interface. After
a restart of the system or of WSL, start the app again: the service restarts
and the interrupted work is shown with its recorded state, without repeating
operations with an uncertain outcome.

These commands work from any folder. `kira` is the short name of
`kira-desktop` and takes the same arguments, for example `kira doctor`.

| Command | What it does |
| --- | --- |
| `kira-desktop` | Opens the interface and, if needed, starts the service |
| `kira-desktop --version` | Shows the installed version |
| `kira-desktop diagnostics` | Prints a local report on the state of the installation, with private data filtered out; `--export` saves it as a JSON file in `diagnostics/` inside the data folder |
| `kira-desktop doctor` | Queries the service and reports the real state of the resources and of each provider (Ollama, Codex, Direct OpenAI, DeepSeek Account, DeepSeek Web, Claude Code); an unavailable provider does not hide the others |
| `kira-desktop service` | Runs the service in the foreground in the terminal, useful to see the logs while you try something |
| `kira-desktop stop-service` | Stops the running service, but only if it is idle, even when it belongs to another version of Kira; if no service is running it starts none and says so |
| `kira-desktop browser-install` | Downloads the Chromium engine of the isolated browser for the selected profile |
| `kira-desktop rpc database.backup` | Creates a consistent copy of the database and prints its path |

Other commands (`rpc project.list`, `rpc project.export`, `actions --project ID`,
`workspace ID --open`) are described in
[Permissions and security](PERMISSIONS_AND_SECURITY.md).

The commands that talk to the service (`doctor`, `diagnostics`, `stop-service`,
`rpc`, `actions`, `workspace`) must be run with the same environment as the
interface: same user, same `KIRA_HOME` or same XDG variables. Otherwise they
address a profile different from the one you are using. Except `stop-service`,
they start the service if it is not running. On failure they print
`{"error": {"code", "message"}}` on standard error and exit with a non-zero
status; invalid arguments are rejected before any service is started.

## Where the data lives

Kira keeps three things clearly separate: the application code (the package or
the checkout), the files of your projects (in the folders you choose) and the
app's data (database, attachments, preferences). Moving or cloning the source
does not move the data; reinstalling the package does not touch it.

### Default paths

Without a `KIRA_HOME` set, Kira follows the XDG variables of your environment
and, if they are missing, the standard paths under your home folder:

| Content | With the XDG variable set | Default path |
| --- | --- | --- |
| Database `kira.sqlite3`, attachments, artifacts, captures, browser | `$XDG_DATA_HOME/kira/` | `$HOME/.local/share/kira/` |
| Interface preferences | `$XDG_CONFIG_HOME/kira/ui.json` | `$HOME/.config/kira/ui.json` |
| Chat drafts and reusable history | `$XDG_CONFIG_HOME/kira/composer.json` | `$HOME/.config/kira/composer.json` |
| Provider sign-ins managed by Kira (Direct OpenAI, DeepSeek Account, DeepSeek Web) | `$XDG_CONFIG_HOME/kira/` (`openai-chatgpt/`, `deepseek-account/`, `deepseek-web/`) | `$HOME/.config/kira/` (same folders) |
| Drafts and pending identities of Soul, Research and project tools | `$XDG_STATE_HOME/kira/` (`souls-ui/`, `research-ui/`, `knowledge-ui/`, each with `composer.json`) | `$HOME/.local/state/kira/` (same folders) |
| State and log of the service started in the background | `$XDG_STATE_HOME/kira/` | `$HOME/.local/state/kira/` |
| Communication socket between interface and service | `$XDG_RUNTIME_DIR/kira/service.sock` | `runtime/service.sock` inside the state folder |

Inside the data folder, next to the database, there are `artifacts/` (files
produced by runs), `attachments/` (attachments and pasted files),
`image-attachments/` (immutable copies of attached images), `captures/`
(screen captures), `diagnostics/` (exported reports), `exports/` (chat and
project exports), `updates/` (packages downloaded by updating from Kira) and
the three folders of the isolated browser `browser-engine/`,
`browser-profiles/` and `browser-temporary/`. The XDG
variables, if you set them, must contain absolute paths; a variable set to an
empty value counts as unset.

### Separate profiles with `KIRA_HOME`

A **profile** is a complete set of Kira data. To have several independent
profiles (for example one for work and one for testing), set `KIRA_HOME` to an
absolute path outside the checkout. When it is set and not empty, it takes
precedence over all the XDG variables and groups everything into four
subfolders: `config/` (`ui.json`, `composer.json` and the provider sign-ins
managed by Kira), `data/` (database, attachments, artifacts, browser), `state/`
(service logs and state, drafts of Soul, Research and project tools) and
`runtime/` (socket and other temporary files of the service).

```sh
export KIRA_HOME="$HOME/kira-work"
kira-desktop
```

Use the same value of `KIRA_HOME` for the interface, the service and every
command that acts on that profile. Keep the path reasonably short: the Unix
socket in `runtime/` has a length limit. Never start two services on the same
profile.

### Temporary profile for testing

To try something without touching your data, open a throwaway profile in a
temporary folder:

```sh
export KIRA_HOME="$(mktemp -d -t kira-test.XXXXXXXX)"
uv run kira
```

The value stays set in that terminal. To see the logs live, run
`uv run kira-desktop service` in one terminal and the interface in a second
terminal with the same `KIRA_HOME`. Before deleting the folder, close the
window and then stop the service with `uv run kira-desktop stop-service`: with
the window open the interface would restart it right away. Temporary folders
are not suited to data you want to keep.

### User systemd service and environment variables

In the package installation, without `KIRA_HOME`, the interface starts the
service with `systemctl --user start kira-desktop.service`. Only the installed
code does this: a source checkout never starts the packaged unit. The service
therefore runs under the systemd user manager, with that manager's environment
and not with that of the terminal from which you launched `kira-desktop`. If
you customize the XDG variables only in the shell, the interface and the
service would use different paths: in that case make the variables known to
systemd as well (for example with `systemctl --user set-environment`) or, more
simply, use `KIRA_HOME`. With `KIRA_HOME` set, or if starting through systemd
fails, Kira starts the service directly in the background, with the same
environment as the interface; its log ends up in `service.log` in the state
folder.

### What remains after uninstalling

`sudo apt remove kira-desktop` deletes only the files installed by the package,
nothing of yours: the database, the attachments, the preferences, the drafts,
the browser profiles, the provider sign-ins managed by Kira and of course the
project folders remain. If you really want to delete everything, before
uninstalling disconnect the connected accounts in the app, so that Kira also
requests remote revocation where it exists (see [Providers](PROVIDERS.md)); then
close the window and stop the service with `kira-desktop stop-service`, because
the command no longer exists after the removal. Finally remove by hand the
folders listed above. Codex and Claude Code, instead, keep their own sign-ins in
their own configuration, which Kira does not touch.

Mind one detail: the file references saved in the database may contain
absolute paths. If you move a profile to another folder or another machine,
Kira does not rewrite those references: check that the referenced files remain
reachable.

## Isolated browser

For web search and for the chat's **Browser** Kira does not use the system
browser: it downloads its own Chromium engine, isolated from the rest of the
computer and started on a virtual display (Xvfb). The engine is neither
included in the package nor downloaded automatically on first launch: you
install it, once per profile.

```sh
kira-desktop browser-install
```

The command downloads the Chromium version expected by Kira into
`browser-engine/` in the data folder of the selected profile (it respects
`KIRA_HOME` and the XDG variables, so run it with the same environment as the
app). It does not modify the system browser and does not delete engine versions
downloaded earlier. From source you also need the system libraries listed in
[Running from source with `uv`](#running-from-source-with-uv).

Browsing data lives in two distinct folders:

- `browser-profiles/` contains the retained profiles, which you enable for a
  chat with **Keep the browser profile for this chat**: one per chat, provider
  and account. They keep the logins to sites: treat them as confidential data
  and include them in the backup.
- `browser-temporary/` contains the throwaway profiles, deleted when the
  browser that used them has been closed and cleaned up.

Screen captures end up in `captures/`, with immutable copies among the
artifacts. When you close the last Kira window, the service closes idle
browsers right away, including those under manual control, and the others as
soon as the current turn ends: temporary profiles are deleted, retained ones
stay. How the browser, manual control and passkey sign-in work is described in
[Browser and Computer](BROWSER_AND_COMPUTER.md).

## Full backup and restore

### Consistent database backup

The `kira.sqlite3` database is in continuous use by the service and part of
the committed data may still be in the WAL file next to it. Copying only the
file while Kira is running can produce an incomplete backup. Use the consistent
copy instead, which goes through SQLite's backup API:

- from the app, with **Settings → Local data → Back up database**;
- from the terminal, with `kira-desktop rpc database.backup`.

In both cases the copy is written next to the database, with the name
`kira-backup-<identifier>.sqlite3`, and the path is shown to you. Then move
it to a safe place outside the data folder and outside the checkout.

### Why the database alone is not enough

The database backup contains chats, projects, queue, goals, Teams, questions,
tool receipts, browser consent and chat settings. It does not contain, however:

- the attachments in `attachments/` and the images in `image-attachments/`;
- the artifacts in `artifacts/` and the captures in `captures/`;
- the preferences in `ui.json` and the unsent drafts with the history in
  `composer.json`;
- the provider sign-ins managed by Kira, in the configuration folder;
- the drafts of Soul, Research and project tools, in the state folder;
- the persistent browser profiles in `browser-profiles/`;
- the project folders, which are yours and must be saved separately.

For a full backup of the profile:

1. Finish the work in progress, close the window and stop the service with
   `kira-desktop stop-service`, as in the [upgrade procedure](#upgrading): with
   the window open the interface would restart the service.
2. Copy in full the profile's configuration, data and state folders (with
   `KIRA_HOME`: `config/`, `data/` and `state/`). The temporary files in
   `runtime/` are not needed.
3. Save the project folders separately.

Keep the backups outside the source repository. They contain conversations,
absolute paths, site logins and the tokens of the provider sign-ins: keep them
private.

### Restore

Always test the restore on an isolated profile before touching the active one:

```sh
export KIRA_HOME="$HOME/kira-restore"
mkdir -p "$KIRA_HOME"
# copy config/, data/ and state/ from the backup here
kira-desktop
```

Check that projects, chats, attachments and preferences are the expected ones.
Only then, with the service stopped, replace the active profile with the copy.
Do not merge two divergent profiles by overwriting individual database files:
one or the other, in full.

## Upgrading

Kira installed from the package or started from a source checkout updates
itself when you ask it to, as described in
[Updating from Kira](#updating-from-kira). The
[manual procedure](#manual-procedure) applies to every installation and is the
one to follow when Kira cannot update itself. Both are meant not to lose the
work in progress: a run interrupted halfway by a code change is not repeated
automatically when you reopen.

When a newer version opens an existing database, it upgrades it on its own
after creating a consistent safety copy next to the database
(`kira.sqlite3.pre-*.bak`): conversations, projects, permissions and provider
references stay intact. The reverse does not hold: **an older version does not
open a newer database**. This is why the backup must be made before upgrading,
not after.

Updating the code does not change accounts, models, project permissions or the
browser consent: those stay in the profile.

### Updating from Kira

Kira checks for updates 8 seconds after it starts and then every 24 hours while
it stays open. In **Settings → Local data → Updates** the
**Check for updates at launch and every day** switch turns the automatic
checks on or off and **Check for updates** runs one right away. When a newer
version exists, **Update to <version>** appears in the sidebar and opens the
**Kira update** window with what is new. A check installs nothing: the update
starts only when you press **Update now**.

What is checked and how the update works depends on the installation:

- **Package** (code in `/opt/kira-desktop/`): Kira asks GitHub for the latest
  published release and compares it with the installed version. To update, it
  downloads the package for your Ubuntu version (24.04 or 26.04) into the
  `updates/` folder of the profile's data, with 0600 permissions, verifies the
  Ed25519 signature `SHA256SUMS.sig` of the `SHA256SUMS` file with the public
  key built into Kira, then the SHA-256 checksum of the package. A release
  without a signature, with an invalid signature or with a different checksum is
  refused and nothing is installed. The downloaded package stays in `updates/`:
  you can delete it after the update.
- **Source checkout** (a git clone with Kira's `pyproject.toml`): Kira runs
  `git fetch` and shows the subjects of the new commits on the remote branch the
  checkout tracks. It updates with `git merge --ff-only` and `uv sync --frozen`,
  keeping the `claude-code` extra if it is installed; if the dependency sync
  fails, it goes back to the previous commit with `git reset --keep`.
  Uncommitted changes to tracked files, a local branch that has diverged from
  the remote one or a checkout that tracks no remote branch block the update: in
  those cases update with git following the manual procedure.
- **Other installations**: Kira reports new versions and points to the
  [Releases](https://github.com/antosec00/kira-desktop/releases) page; update
  with the manual procedure.

**Update now** runs these steps and shows them in the window:

1. **Back up database**: saves the drafts and creates a consistent copy, as
   the button of the same name does (see
   [Consistent database backup](#consistent-database-backup)).
2. **Download and signature check** for the package, **Checking the sources**
   for the checkout.
3. **Stopping the service once it is idle**: Kira asks the service to stop and does not
   restart it while the update is in progress. If there is active work (runs,
   Teams, automatic continuations, context maintenance, branches or
   compactions, a browser under manual control) the service refuses, nothing
   changes and you can try again when it has finished.
4. **Installation**: for the package
   `pkexec apt-get install -y --no-remove <package>`, with the system password
   prompt; for the checkout the branch fast-forward and the sync described
   above.
5. **Restarting Kira** on the new version, with the same profile.

If `pkexec` is missing, or the password prompt is dismissed or cannot appear
(as usually in WSL), Kira shows the command
`sudo apt install --no-remove <package path>` with a button to copy it: run it
in a terminal, then press **Restart Kira**. **Cancel** gives up for now and
starts the service of the installed version again. If the installation fails
the previous version stays, its service starts again and **Try again** repeats
the update.

**Skip this version** (package only) hides that version from the automatic
checks; **Check for updates** still shows it. **Later** closes the window
without changing anything. Steps 1-3 of the manual procedure (finishing the
work, pausing the queue, closing the browser) are still useful here, because
they keep the service from refusing to stop.

The checks contact only `api.github.com` (a checkout, its git remote) and the
downloads only the GitHub addresses that serve the release files; apart from the
User-Agent `Kira-Desktop/<version>` no data about you is sent.

### Manual procedure

This procedure applies to both the package and the source.

1. **Finish the work.** Let the active requests, the Teams and the autonomous
   goals finish (or pause them with `/goal pause`).
2. **Pause the queue** of every project with **Queue → Pause queue**, so that
   no pending request starts while you upgrade.
3. **Close the browser.** Complete or cancel any sign-in in progress, press
   **Return to Kira** if you had taken control, then **Close browser** from its
   panel. The service refuses to stop while a browser is under manual control.
4. **Back up the database** with **Settings → Local data → Back up database**.
   If you also want a full copy of the profile, make it after step 6, with the
   service stopped, as described in
   [Full backup and restore](#full-backup-and-restore).
5. **Close the window** normally, so that the unsent drafts are saved. If a
   draft saving error appears, copy the text elsewhere before closing.
6. **Stop the service**:

   ```sh
   kira-desktop stop-service
   ```

   The command refuses to stop if there is still active work (runs, Teams,
   automatic continuations, context maintenance, branches or compactions, a
   browser under manual control) and tells you why. In that case go back to the
   previous steps; do not kill the process and do not delete the socket or the
   database to force it. If a provider or browser cleanup could not be
   confirmed, the service stops anyway and lists the affected projects
   (`pending_cleanup_projects`); the next start checks them again, and a cleanup
   without a recorded process is released after the computer restarts. The
   command also stops a service of another version of Kira; if no service is
   running, it says so without starting one.
7. **Update the code**: download and install the new package as in
   [Installing from the package](#installing-from-the-package), or update the
   checkout and repeat `uv sync --frozen`, with `--extra claude-code` if you
   use Claude Code: without the extra the command removes its support.
8. **Restart with the same profile** (same `KIRA_HOME` or same XDG variables)
   and check the selected project and chat, provider, model, reasoning effort,
   preferences and drafts.
9. **Resume the queue** with **Queue → Resume queue** after checking the
   interrupted work. The queue stays paused until you do it.

### Rolling back to a previous version

To roll back you need two things: the package (or the checkout) of the previous
version and a backup of the profile made with that version, before the
upgrade. The upgraded database cannot be "taken back". If that backup is
missing, the automatic safety copy `kira.sqlite3.pre-*.bak` contains the
database as it was before the upgrade, but not the other files of the profile.

1. Follow steps 1-6 of the [manual procedure](#manual-procedure) with the
   current version.
2. Set aside, intact, the current profile: it contains the work done after the
   upgrade, which the rollback does not keep.
3. Restore the compatible backup, including the files it references
   (attachments, artifacts, browser profiles, `composer.json`), preferably on
   an isolated profile to verify it.
4. Install the previous version and start it on the same profile.

## Building the packages

The packages are built from the checkout, without root privileges and without
touching the system Python. The Python dependencies are installed into a
working folder from the lock with hash verification, the application is
packaged as a wheel and everything ends up in `/opt/kira-desktop` inside the
`.deb`.

### Package for Ubuntu 24.04

Requirements: Ubuntu 24.04 with the system `python3` (3.12), `uv` and
`dpkg-deb` (`dpkg` package, already present). The script refuses to run with a
different interpreter, because the compiled libraries must match the system
Python of whoever will install the package.

```sh
python3 scripts/build_deb.py
```

The result is `dist/kira-desktop_<version>_amd64.deb` with `dist/SHA256SUMS`
next to it. The working folder is `build/deb/`.

### Package for Ubuntu 26.04

The package for Ubuntu 26.04 is built in a Docker container, so you do not need
Ubuntu 26.04 or Python 3.14 on the building machine:

```sh
python3 scripts/build_deb.py --target resolute
```

The script uses the Ubuntu and `uv` images pinned by digest in
`packaging/resolute/source.json`. The checkout is mounted read-only; the only
writable folders are `build/targets/resolute/` and `dist/resolute/`, whose files
belong to your user. Nothing is installed on the building computer outside
Docker, where the downloaded images and the builder image
`kira-app-builder:resolute-0.1` remain. The result is
`dist/resolute/kira-desktop_<version>_amd64.deb`, with `SHA256SUMS` and a JSON
file that records the image, the interpreter, the Qt version and the hashes of
the locks used.

Keep the build and output folders of the two packages separate: do not rename a
package to install it on the other system. The packages on the Releases page are
built and checked with these same scripts, and published under the stable names
of the table at the top together with their `SHA256SUMS` (see
[Development](DEVELOPMENT.md#publishing-a-version)).

### Checking a package

Before distributing a package you can test it in a throwaway Ubuntu container,
again with Docker:

```sh
python3 scripts/check_deb.py --target noble
python3 scripts/check_deb.py --target resolute
```

The script installs the package in the container, starts the interface and the
service as an unprivileged user outside the checkout and tests data saving,
backup and export, clipboard, offscreen and Xvfb rendering, and finally
upgrade, removal and reinstallation of the package with data preservation. The
reports end up under `build/`. The container has neither a Plasma/Wayland
desktop nor configured providers: the final test on a real system is up to you.
Code structure, tests and customization are in [Development](DEVELOPMENT.md).

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| Wrong projects or empty history | You are opening another profile: check `KIRA_HOME` and the XDG variables of the interface and of the service. Do not create a new database over the old one |
| The interface reports a version different from the service | Finish the work, close the window, stop the idle service with `stop-service` (it also works with a service of another version) and restart with the same code for both |
| EGL/Zink warnings in WSLg | They are harmless: Kira uses software rendering for the interface. If you set a renderer by hand, your choice prevails |
| Provider unavailable | Check the configured executable or endpoint and the account status at the provider. A model listed in the catalog does not guarantee that your account can use it |
| Dragging a file from Windows does not work | Use paste or **Attach**: in WSLg the drag event does not always reach Kira |
| A native action is unavailable | Native actions use fixed applications (Dolphin, Konsole, Kate, Firefox, Spectacle, Plasma's system settings and system monitor, Discover, Déjà Dup): install the required one, or use the available actions. An accepted launch request does not mean that the window opened: check |
| The service does not start | Read the logs (below) or start `kira-desktop service` in the foreground to see the error in the terminal: `diagnostics` and `doctor` need a running service. Do not delete the socket or the database and do not repeat an uncertain operation as a "repair" |
| Queued requests do not start after a restart | It is normal: the queue stays paused. Check the queue and the outcome of the interrupted operations, then press **Queue → Resume queue** |
| Draft not saved | Copy the text out of the app right away, then read the error reported on `composer.json` before closing |
| The browser shows a control wait | Press **Return to Kira** after the manual work, or **Stop** to cancel the model's request. Neither happens on its own |
| Passkey sign-in pending | Complete or cancel the Windows window: while the sign-in is in progress, **Return to Kira** and **Done** stay disabled. On native Linux the authentication pickers appear directly in the browser |
| The browser view does not open | Keep the manual work and read the error shown in the panel. From source, check that you have installed the system libraries listed in [Running from source with `uv`](#running-from-source-with-uv) |
| The browser does not start and reports a sandbox error | On Ubuntu 24.04 and later AppArmor can deny unprivileged user namespaces to programs without a profile (`kernel.apparmor_restrict_unprivileged_userns = 1`), and Chromium's sandbox needs them. Kira never disables the sandbox: ask whoever administers the computer to allow user namespaces for the Chromium executable in `browser-engine/` of Kira's data folder with an AppArmor profile, then open the browser again |
| Passkey method unavailable | It depends on the authenticators reachable from the isolated browser and, in WSL, on the bridge with Windows. Without that bridge, Windows Hello is not available from Linux; signing in to a site still requires user verification |
| Native provider operation (new thread, compaction) uncertain | Look at the result reported by the provider: an accepted request does not prove that the operation took place |

### Logs and diagnostics

When the interface starts the service through systemd (installed package,
without `KIRA_HOME`) the service writes to the systemd journal:

```sh
journalctl --user -u kira-desktop.service
```

With `KIRA_HOME`, from source, or when systemd is not available, the service
started in the background writes to `service.log` in the profile's state folder
(`$HOME/.local/state/kira/service.log` by default; if it exceeds 2 MB at the
next start, it is moved to `service.previous.log`). A source checkout never
goes through systemd: the service it starts always writes to `service.log`,
even when the package is installed. With `kira-desktop service` in the
foreground the logs appear directly in the terminal.

`kira-desktop doctor` asks the service for the real state of the resources and
of each provider. `kira-desktop diagnostics` produces a local report in which
private data (personal paths, identifiers, chat contents) is filtered out;
`kira-desktop diagnostics --export` saves it as JSON in `diagnostics/` in the
data folder. The filter is cautious but not infallible: reread the report
before sharing it with anyone.

Finally, remember that closing the window is not quitting: the service keeps
going and the queue keeps working, while idle browsers are closed. Shutting
down WSL, turning off the computer or a service crash instead leave the ongoing
activities as interrupted, to be resumed after checking their outcome; the
[User guide](USER_GUIDE.md) describes what happens when you reopen.
