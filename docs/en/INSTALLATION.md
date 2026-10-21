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

| System | System Python | Package |
| --- | --- | --- |
| Ubuntu 24.04 LTS (noble) | 3.12 | `dist/kira-desktop_<version>_amd64.deb` |
| Ubuntu 26.04 (resolute) | 3.14 | `dist/resolute/kira-desktop_<version>_amd64.deb` |

The two packages contain the same application code and the same Qt/PySide6
6.11.2 libraries, but each is bound to the Python interpreter of its own
system: **they are not interchangeable**. The package for Ubuntu 26.04 requires
Python 3.14 and does not install on Ubuntu 24.04; the one for Ubuntu 24.04
requires Python 3.12.

Kira works both on native Ubuntu and inside WSL2 on Windows, provided that a
graphical session is available: in WSL2 you need WSLg (or another configured
display). The reference desktop is KDE Plasma on Wayland; other desktop
environments work, but it is on Plasma/Wayland that the interface is checked
most carefully.

The AI providers are configured after installation, inside the app, as
described in [Providers](PROVIDERS.md).

## Installing from the package

Download the package suited to your system (or build it, see
[Building the packages](#building-the-packages)) and install it with `apt`,
which resolves the required system libraries on its own:

```sh
# Ubuntu 24.04 / Python 3.12
sudo apt install --no-remove ./dist/kira-desktop_<version>_amd64.deb

# Ubuntu 26.04 / Python 3.14
sudo apt install --no-remove ./dist/resolute/kira-desktop_<version>_amd64.deb
```

The `./dist/...` path is that of a package built in the checkout; if you
downloaded it, point to the folder where it is. The `./` prefix is required for
`apt` to recognize a local file.

The package installs the `/usr/bin/kira-desktop` command, the application code
with all its Python dependencies (Qt included) in `/opt/kira-desktop/`, the
**Kira Desktop** entry of the application menu with its icon, the systemd user
service `/usr/lib/systemd/user/kira-desktop.service` and, in
`/usr/share/doc/kira-desktop/`, the license, the list of dependencies and the
documentation.

The package is deliberately conservative. It does not run `pip` as root and
does not touch the system Python: all the libraries are inside
`/opt/kira-desktop/lib`. It does not download models or browser engines, does
not enable systemd `linger` for your user and does not modify Codex's
configuration or credentials, which remain the provider's property. It has no
installation or removal scripts: installing, upgrading or removing the package
never interrupts work in progress and never deletes your data (see
[What remains after uninstalling](#what-remains-after-uninstalling)).

## Running from source with `uv`

If you want to use the latest version of the code or contribute to development,
you can run Kira directly from the checkout. You need
[`uv`](https://docs.astral.sh/uv/), which downloads the suitable Python
interpreter on its own and creates an isolated virtual environment in the
project folder.

```sh
git clone https://github.com/antosec00/kira-desktop-app.git
cd kira-desktop-app
uv sync --frozen
uv run kira-desktop
```

`uv sync --frozen` installs exactly the versions recorded in `uv.lock`, the
same as the packages. The **Claude Code · personal use** (experimental)
provider requires an additional dependency that is not included in the `.deb`
packages; from source you enable it with the `claude-code` extra:

```sh
uv sync --frozen --extra claude-code
```

`uv` manages only the Python libraries. The system libraries that Kira's
isolated browser uses for its virtual display must be installed separately:

```sh
sudo apt install xvfb libxtst6
```

The `.deb` package declares them as dependencies and `apt` installs them on its
own; from source you have to take care of it yourself, otherwise the isolated
browser does not open.

All the commands in this guide also apply from source: replace `kira-desktop`
with `uv run kira-desktop`, run from the root of the checkout. Remember that
from source the interface and the service load the checkout's code: do not
modify the files while Kira is in use; prepare the changes separately and
activate them with the [upgrade procedure](#upgrading).

## First launch and useful commands

Start Kira from the application menu or with `kira-desktop`. The interface
looks for the Kira service (the process that owns the database and talks to the
providers): if it is not running, it starts it, through systemd in the package
installation or as a background process from source or when you use a custom
profile. The service keeps working even when you close the window, as long as
your user session stays open.

The folder from which you launch the command has no effect: it neither selects
nor creates a project. Choose the project and the chat from the interface. After
a restart of the system or of WSL, start the app again: the service restarts
and the interrupted work is shown with its recorded state, without repeating
operations with an uncertain outcome.

These commands work from any folder:

| Command | What it does |
| --- | --- |
| `kira-desktop` | Opens the interface and, if needed, starts the service |
| `kira-desktop --version` | Shows the installed version |
| `kira-desktop diagnostics` | Prints a local report on the state of the installation, with private data filtered out; `--export` saves it as a JSON file in `diagnostics/` inside the data folder |
| `kira-desktop doctor` | Queries the service and reports the real state of providers and resources |
| `kira-desktop service` | Runs the service in the foreground in the terminal, useful to see the logs while you try something |
| `kira-desktop stop-service` | Stops the service, but only if it is idle |
| `kira-desktop browser-install` | Downloads the Chromium engine of the isolated browser for the selected profile |
| `kira-desktop rpc database.backup` | Creates a consistent copy of the database and prints its path |

Other commands (`rpc project.list`, `actions --project ID`, `workspace ID --open`)
are described in [Permissions and security](PERMISSIONS_AND_SECURITY.md).

The commands that talk to the service (`doctor`, `diagnostics`, `stop-service`,
`rpc`) must be run with the same environment as the interface: same user, same
`KIRA_HOME` or same XDG variables. Otherwise they address a profile different
from the one you are using.

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
| State and log of the service started in the background | `$XDG_STATE_HOME/kira/` | `$HOME/.local/state/kira/` |
| Communication socket between interface and service | `$XDG_RUNTIME_DIR/kira/service.sock` | `runtime/service.sock` inside the state folder |

Inside the data folder, next to the database, there are `artifacts/` (files
produced by runs), `attachments/` (attachments and pasted files),
`image-attachments/` (immutable copies of attached images), `captures/`
(screen captures), `diagnostics/` (exported reports), `exports/` (chat and
project exports) and the three folders of the isolated browser
`browser-engine/`, `browser-profiles/` and `browser-temporary/`. The XDG
variables, if you set them, must contain absolute paths.

### Separate profiles with `KIRA_HOME`

A **profile** is a complete set of Kira data. To have several independent
profiles (for example one for work and one for testing), set `KIRA_HOME` to an
absolute path outside the checkout. When it is set and not empty, it takes
precedence over all the XDG variables and groups everything into four
subfolders: `config/` (`ui.json` and `composer.json`), `data/` (database,
attachments, artifacts, browser), `state/` (service logs and state) and
`runtime/` (socket and locks).

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
uv run kira-desktop
```

The value stays set in that terminal. To see the logs live, run
`uv run kira-desktop service` in one terminal and the interface in a second
terminal with the same `KIRA_HOME`. Before deleting the folder, stop the
service with `uv run kira-desktop stop-service`. Temporary folders are not
suited to data you want to keep.

### User systemd service and environment variables

In the package installation, without `KIRA_HOME`, the interface starts the
service with `systemctl --user start kira-desktop.service`. The service
therefore runs under the systemd user manager, with that manager's environment
and not with that of the terminal from which you launched `kira-desktop`. If
you customize the XDG variables only in the shell, the interface and the
service would use different paths: in that case make the variables known to
systemd as well (for example with `systemctl --user set-environment`) or, more
simply, use `KIRA_HOME`. With `KIRA_HOME` set, Kira does not go through systemd
and starts the service directly, with the same environment as the interface;
its log ends up in `service.log` in the state folder.

### What remains after uninstalling

`sudo apt remove kira-desktop` deletes only the files installed by the package,
nothing of yours: the database, the attachments, the preferences, the drafts,
the browser profiles and of course the project folders remain. If you really
want to delete everything, stop the service first and then remove by hand the
folders listed above. The logins to Codex and the other providers are kept by
the providers themselves, not by Kira.

Mind one detail: the file references saved in the database may contain
absolute paths. If you move a profile to another folder or another machine,
Kira does not rewrite those references: check that the referenced files remain
reachable.

## Isolated browser

Kira does not use the system browser. For web search and for the chat's
**Browser** it downloads its own Chromium engine, isolated from the rest of the
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
downloaded earlier. From source you also need the `xvfb` and `libxtst6`
libraries, as mentioned above.

Browsing data lives in two distinct folders:

- `browser-profiles/` contains the persistent profiles that you can enable from
  the **Browser** panel for a chat, an account or a provider. They keep the
  logins to sites: treat them as confidential data and include them in the
  backup.
- `browser-temporary/` contains the throwaway profiles, deleted when the
  browser that used them has been closed and cleaned up.

Screen captures end up in `captures/`, with immutable copies among the
artifacts. Closing the Kira window neither closes the browser nor deletes its
profiles. How the browser, manual control and passkey sign-in work is described
in [Browser and Computer](BROWSER_AND_COMPUTER.md).

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
- the persistent browser profiles in `browser-profiles/`;
- the project folders, which are yours and must be saved separately.

For a full backup of the profile:

1. Finish the work in progress and stop the service with
   `kira-desktop stop-service` (see the [upgrade procedure](#upgrading), which
   is the same).
2. Copy in full the profile's configuration, data and state folders (with
   `KIRA_HOME`: `config/`, `data/` and `state/`). The socket and lock files in
   `runtime/` are not needed.
3. Save the project folders separately.

Keep the backups outside the source repository. They contain conversations,
absolute paths and site logins: keep them private.

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

There is a single procedure, valid for both the package and the source, and its
purpose is not to lose the work in progress. A run interrupted halfway by a code
change is not repeated automatically when you reopen.

1. **Finish the work.** Let the active requests, the Teams and the autonomous
   goals finish (or pause them with `/obiettivo pausa`).
2. **Pause the queue** of every project with **Queue → Pause queue**, so that
   no pending request starts while you upgrade.
3. **Close the browser.** Complete or cancel any sign-in in progress, press
   **Return to Kira** if you had taken control, then **Close browser** from its
   panel. The service refuses to stop while a browser is under manual control.
4. **Make the backup** with **Settings → Local data → Back up database** and,
   if you want a full copy, follow the previous section.
5. **Close the window** normally, so that the unsent drafts are saved. If a
   draft saving error appears, copy the text elsewhere before closing.
6. **Stop the service**:

   ```sh
   kira-desktop stop-service
   ```

   The command refuses to stop if there is still active work (runs, Teams, tool
   processes, browser under manual control) and tells you why. In that case go
   back to the previous steps; do not kill the process and do not delete the
   socket or the database to force it.
7. **Update the code**: install the new package with `sudo apt install
   --no-remove ./...deb`, or update the checkout and repeat `uv sync --frozen`.
8. **Restart with the same profile** (same `KIRA_HOME` or same XDG variables)
   and check the selected project and chat, provider, model, reasoning effort,
   preferences and drafts.
9. **Resume the queue** with **Queue → Resume queue** after checking the
   interrupted work. The queue stays paused until you do it.

When a newer version opens an existing database, it upgrades it on its own
after creating a consistent safety copy: conversations, projects, permissions
and provider references stay intact. The reverse does not hold: **an older
version does not open a newer database**. This is why the backup must be made
before upgrading, not after.

Updating the code does not change accounts, models, project permissions or the
browser consent: those stay in the profile.

### Rolling back to a previous version

To roll back you need two things: the package (or the checkout) of the previous
version and a backup of the profile made with that version, before the
upgrade. The upgraded database cannot be "taken back".

1. Follow steps 1-6 of the upgrade procedure with the current version.
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
belong to your user. Nothing is installed or modified on the building computer.
The result is `dist/resolute/kira-desktop_<version>_amd64.deb`, with
`SHA256SUMS` and a JSON file that records the image, the interpreter, the Qt
version and the hashes of the locks used.

Keep the build and output folders of the two packages separate: do not rename a
package to install it on the other system.

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

## Migrating an existing profile

If you have used Kira from source with a "consolidated" profile (a folder with
the `config/`, `data/`, `state/`, `runtime/` structure of `KIRA_HOME`) and you
want to move it or hand it over to another copy of the code, two scripts in the
checkout guide you in a controlled way. They are tools for expert users: read
their `--help` first.

```sh
uv run python scripts/separate_profile.py --help
uv run python scripts/activate_standalone.py --help
```

`separate_profile.py` copies a stopped profile to a new destination and leaves
a compatibility link in the original place, so that absolute references to the
artifacts keep working. It requires `--source`, `--destination` and `--archive`
(where to keep the original copy, which remains as restore material, not as a
second active profile) and acts only with `--apply`. It does not rewrite
database rows, saved paths or project files, and refuses to proceed if the
database still has pending WAL data: the service must have been stopped
cleanly.

`activate_standalone.py` performs the whole switch to a standalone checkout: it
wants `--source-root` (the checkout, with its virtual environment already
created), `--source-profile` (the current consolidated profile), `--profile`
(the destination) and `--launcher` (the launch command to update), all as
explicit paths. Without `--apply` it only checks that the service is idle and
reads the metadata of the selected session; with `--apply` it performs the
migration, but never stops a busy service.

Both scripts refuse destinations that already exist or are unsafe. Before using
them, finish the work, make a consistent backup and keep a copy of the original
profile and launch command; afterwards, check project, chat, model and
reasoning effort in the interface. From that moment the old profile is an
archive: restoring it would delete the work done afterwards.

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| Wrong projects or empty history | You are opening another profile: check `KIRA_HOME` and the XDG variables of the interface and of the service. Do not create a new database over the old one |
| The interface reports a version different from the service | Finish the work, close the window, stop the idle service with `stop-service` and restart with the same code for both |
| EGL/Zink warnings in WSLg | They are harmless: Kira uses software rendering for the interface. If you set a renderer by hand, your choice prevails |
| Provider unavailable | Check the configured executable or endpoint and the account status at the provider. A model listed in the catalog does not guarantee that your account can use it |
| Dragging a file from Windows does not work | Use paste or **Attach**: in WSLg the drag event does not always reach Kira |
| A native action is unavailable | Install or configure the required tool on the system separately, or use the available actions. An accepted launch request does not mean that the window opened: check |
| The service does not start | Read `kira-desktop diagnostics` and the logs (below). Do not delete the socket or the database and do not repeat an uncertain operation as a "repair" |
| Queued requests do not start after a restart | It is normal: the queue stays paused. Check the queue and the outcome of the interrupted operations, then press **Queue → Resume queue** |
| Draft not saved | Copy the text out of the app right away, then read the error reported on `composer.json` before closing |
| The browser shows a control wait | Press **Return to Kira** after the manual work, or **Stop** to cancel the model's request. Neither happens on its own |
| Passkey sign-in pending | Complete or cancel the Windows window: while the sign-in is in progress, **Return to Kira** and **Done** stay disabled. On native Linux the authentication pickers appear directly in the browser |
| The browser view does not open | Keep the manual work and read the error shown in the panel. From source, check that you have installed `xvfb` and `libxtst6` |
| Passkey method unavailable | It depends on the authenticators reachable from the isolated browser and, in WSL, on the bridge with Windows. Without that bridge, Windows Hello is not available from Linux; signing in to a site still requires user verification |
| Native provider operation (new thread, compaction) uncertain | Look at the result reported by the provider: an accepted request does not prove that the operation took place |

### Logs and diagnostics

In the package installation the service writes to the systemd journal:

```sh
journalctl --user -u kira-desktop.service
```

With `KIRA_HOME`, from source, or when systemd is not available, the service
started in the background writes to `service.log` in the profile's state folder
(`$HOME/.local/state/kira/service.log` by default; when it exceeds 2 MB it is
rotated to `service.previous.log`). With `kira-desktop service` in the
foreground the logs appear directly in the terminal.

`kira-desktop doctor` asks the service for the real state of providers and
resources. `kira-desktop diagnostics` produces a local report in which private
data (personal paths, identifiers, chat contents) is filtered out;
`kira-desktop diagnostics --export` saves it as JSON in `diagnostics/` in the
data folder. The filter is cautious but not infallible: reread the report
before sharing it with anyone.

Finally, remember that closing the window is not quitting: the service keeps
going, the browser stays open, the queue keeps working. Shutting down WSL,
turning off the computer or a service crash instead leave the ongoing
activities as interrupted, to be resumed after checking their outcome; the
[User guide](USER_GUIDE.md) describes what happens when you reopen.
