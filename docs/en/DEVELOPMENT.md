# Developer guide

*[Leggi in italiano](../it/SVILUPPO.md)*

This guide is a map of the repository for people who already know Python, Qt
and agent harnesses: where everything lives, how to work on the code, which
checks must pass and how to add a provider, a language or a preference. The
engineering rules that apply to every change are in
[AGENTS.md](../../AGENTS.md); the behaviour of the features is described in the
rest of this documentation, starting from [Architecture](ARCHITECTURE.md) and
[Contracts](CONTRACTS.md).

## Mental model

Kira is made of three parts that cooperate inside a single user session:

| Part | Folder | Responsibility |
| --- | --- | --- |
| **Interface** (Qt Quick / PySide6) | `src/kira/ui/` | Intentions and presentation: QML in `ui/qml/`, controllers exposed to QML as context properties (`Workspace`, research, web, workbench, attachments…), GUI-only preferences in `config/ui.json`. |
| **Service** (asyncio, SQLite in WAL mode, private IPC over a Unix socket) | `src/kira/service/` | Execution and durability: projects, sessions, runs and their sequenced events, tool execution under the project permission, the browser/Computer actor, Teams, the Research record, questions, attachments. It is the only process that writes to SQLite. |
| **Backends** (provider adapters) | `src/kira/backends/` | One adapter per provider (`ollama`, `codex`, `openai_chatgpt`, `deepseek_account`, `deepseek_web`, `claude_code`). It receives a `RunContext`, sends events back with `ctx.emit` and, depending on the provider, has the service run the base tools through `ctx.execute_tool` and Kira's client tools through `ctx.client_tool_call`, or observes the provider's native tools and offers Kira's through MCP. |

`src/kira/domain/` holds the pure definitions shared by the three parts: tool
specifications, contracts, provider sets per capability (`providers.py`),
questions, web and Computer namespaces, paths. The contracts between the parts
(IPC methods, event envelopes, permissions) are in [Contracts](CONTRACTS.md);
[Architecture](ARCHITECTURE.md) explains the process boundaries and the
recovery model.

## Repository map

```
src/kira/
  cli.py            kira-desktop and kira entry point (GUI, diagnostics, doctor, stop-service, rpc…)
  client.py         IPC client of the terminal commands and service startup (the GUI uses ui/ipc.py)
  backends/         provider adapters, client_tools.py (Kira tool bridge),
                    kira_mcp.py (MCP relay for Codex), deepseek_pow_solver.wasm
  domain/           contracts, provider sets (providers.py), tool specifications,
                    questions, web/computer namespaces
  service/          server.py (IPC dispatch), assistant.py, teams.py, web.py (browser actor),
                    research.py, knowledge.py, questions.py, attachment_inputs.py,
                    provider_routing.py, tool_policy.py, storage.py
  tools/            file/command tools, Chromium and Computer workers, browser
                    authentication bridge, project tool worker
  resources/        knowledge resources bundled with the package
  ui/               app.py (engine), controller.py (Workspace), models.py (transcript),
                    *_controller.py, preferences.py, qml/, translations/, assets/
tests/              synthetic pytest suite (pytest-qt, offscreen Qt, mock providers)
scripts/            check_translations.py, check_public_tree.py, build_deb.py, check_deb.py,
                    installed_smoke.py, render_doc_screenshots.py, live_*.py,
                    deepseek_web_smoke.py, gui_evidence.py
packaging/          Debian packaging for Ubuntu 24.04 (noble) and 26.04 (resolute):
                    launcher, user unit, .desktop, licence texts, requirements lock
protocol-schema/    generated Codex App Server protocol JSON schemas, used by the tests
docs/
  it/               Italian documentation
  en/               English translation of the documentation (this folder)
  images/           screenshots produced from fixture data
```

## Working environment

Ubuntu 24.04 (Python 3.12) or Ubuntu 26.04 (Python 3.14), native or on
WSL2/WSLg. The isolated browser also needs the `xvfb` and `libxtst6` system
packages.

```sh
git clone https://github.com/antosec00/kira-desktop.git
cd kira-desktop
uv sync --frozen --extra claude-code        # the extra is optional
export KIRA_HOME="$HOME/kira-dev-profile"   # never develop on the real profile
uv run kira-desktop                         # starts the GUI and, if needed, the service
uv run kira-desktop stop-service            # refuses if there is active work
```

`KIRA_HOME` keeps the development profile (database, drafts, preferences,
runtime socket) separate from a package installation. Use the same `KIRA_HOME`
for the GUI, the service and the diagnostic commands: a temporary folder is
fine for a trial, not for data you want to keep. **GUI and service must have
the same version**: a mismatch is rejected at the IPC handshake, so stop the
service with `stop-service` before switching checkout. The `uv.lock` lock is
the authority on dependencies: ordinary work on the interface requires
updating neither the dependencies nor the operating system.

Never copy credentials or personal content into test fixtures.
[Installation](INSTALLATION.md) describes profiles, backups and upgrades.

## Checks

```sh
QT_QPA_PLATFORM=offscreen uv run pytest tests     # whole synthetic suite (~20 minutes)
uv run ruff check src tests scripts packaging
uv run mypy src
python3 scripts/check_translations.py             # English entries complete, consistent and used
python3 scripts/check_public_tree.py              # no personal paths or unreviewed binaries
```

All five must pass before a pull request; CI runs them and also checks that
`uv.lock` and `packaging/requirements.lock.txt` are current (`uv lock --check`
and the comparison with `uv export`). `check_public_tree.py` rejects paths such
as `/home/<name>` and `C:\Users\<name>` in tracked files and compares the hash
of every reviewed binary (for example the DeepSeek Web WebAssembly module) with
the expected value.

### How the tests are built

The tests are synthetic: they use fixture projects, mock providers and
offscreen rendering, and never contact a real provider. A behaviour change
comes with a test that would fail without it.

- **Interface tests.** They create a real `QQmlEngine` with a synthetic
  `Workspace` (see the fixtures in `tests/test_ui_*.py`): they walk
  `childItems()`, click with `QTest.mouseClick`, evaluate QML expressions with
  `QQmlExpression` and verify that the Qt log contains no binding loops,
  `ReferenceError` or `TypeError`.
- **Pinned `objectName`s and geometries.** They are pinned on purpose. The
  `ComboBox` counters in `tests/test_ui_contracts.py` (39 combos, 156
  measurements) and in `tests/test_ui_workspace.py` (45) change every time you
  add a `ComboBox`: update them in the same change.
- **Offscreen rendering.** To look at a layout, create the engine with the
  synthetic workspace, resize the window and save `window.grabWindow()`; the UI
  test fixtures show how. The screenshots in `docs/images/` are produced this
  way, from fixture data only, by `scripts/render_doc_screenshots.py`:
  regenerate them when the interface changes and update their checksums in
  `scripts/check_public_tree.py`.
- **Service tests.** They start a real `Service` on a temporary profile with a
  mock provider (`backend_factory=`) and call the IPC methods directly.
- **Backend tests.** They use mock SDKs or HTTP transports; nothing reaches a
  provider.

### Live verification scripts

The `scripts/live_*.py` scripts (Ollama, Codex, assistant, Teams, continuity,
direct context, questions, workbench…) exercise a **real** provider. They are
not part of the suite: they consume your account's resources, may perform
authorized work and must be run only with a throwaway profile under `build/`
(for example `KIRA_HOME=$PWD/build/live-state`). They write their reports to
`build/`, which is ignored by Git: **do not commit the reports**, they may
contain account identifiers and personal paths.

`scripts/installed_smoke.py` instead runs inside a throwaway container with the
package installed, without a checkout and with synthetic data; `check_deb.py`
uses it.

## Adding a provider

1. **Adapter.** Implement `src/kira/backends/<name>.py` with `discover()`,
   `run(ctx)` and `close()`. Emit `message.delta` / `message.completed`,
   `tool.started` / `tool.completed`, `usage` and `session.updated` through
   `ctx.emit`. `claude_code.py` is a compact example of an adapter that
   observes the provider's native tools and offers Kira's through an internal
   MCP server; `ollama.py` shows the tool loop managed by Kira.
2. **Registration in the service.** Add it to the `_backend()` factory in
   `service/server.py`, to the account check in `prepare_provider()` if it has
   an account, and to `service/provider_routing.py` (`BACKENDS` and the allowed
   settings in `_OWN_SETTINGS`): the providers accepted by `session.create` and
   by the `backend.*` methods derive from it. Then decide the shared
   capabilities in the sets of `domain/providers.py`: `IN_PROCESS_BACKENDS`
   (HTTP inference inside the service, with no provider process),
   `WEB_BACKENDS` (Kira's browser and Computer) and `TEAM_BACKENDS` (Teams and
   delegation from the chat). The other capabilities have their own allowlists
   in `service/assistant.py` (assistant tools), `conversation_controls.py`
   (guidance), `project_tools.py`, `research.py`, `knowledge.py`,
   `autonomous_goals.py`, `reasoning_summaries.py`, `attachment_inputs.py`
   (images), `tool_policy.py` and in `cli.py` (`doctor`). Leaving the provider
   out of a set is a legitimate choice, not an oversight, but it must be
   consistent with the capability table in [Providers](PROVIDERS.md).
3. **Interface.** Expose it in `ui/controller.py` (`setBackend`, labels,
   capabilities), in the shared labels of `qml/KFormat.qml` (`providerLabel`),
   in the models page and in the composer popover in `qml/Main.qml`, in the
   provider switch dialog (`ProviderSwitchDialog.qml` and
   `ui/provider_switch.py`) and in the translation catalogs.
4. **Documentation.** Describe connection, tools, context and limits in
   `docs/it/PROVIDER.md` and update the translation in `docs/en/`. If the
   provider is an experimental integration, say so with that word, once.

## Adding interface text or a preference

- Mark strings with `qsTr()` (QML) or `tr()` (Python) and add the English
  translation in `src/kira/ui/translations/en/*.json`.
  `check_translations.py` fails on missing entries, on entries the source does
  not use and on conflicts between catalogs.
- Interface preferences live in `ui/preferences.py` (`DEFAULTS` plus the
  validation in `valid_preference`) and are exposed as properties and slots of
  `Workspace`; they never call the IPC. They are saved atomically in `ui.json`.
- User, provider and tool text stays literal: it does not go through the
  catalogs and is never formatted a second time.

## Adding a language

All language data lives in `src/kira/ui/translations/`:

- `languages.json`: registry of the language codes and the native names shown
  in the selector.
- `it/qt.json`: Italian labels of the standard Qt buttons; the rest of the
  Italian text is in the source, because Italian is the source language.
- `en/core.json`, `en/qml.json`, `en/python.json` and the other `en/*.json`
  files: English translations grouped by ownership. Each file is a flat JSON
  object mapping the source text to the translation.

The catalogs are package data, not downloaded content or executable templates.
The loader validates codes, shape, size limits per entry, per file and in
total, and conflicting duplicates. An unsupported preference falls back to
Italian; a missing entry falls back to the source text.

At startup the GUI compiles the validated messages with `lrelease`, the only
executable used, taken from the pinned PySide6 distribution, and loads native
Qt catalogs. The generated QM files live in a private content-addressed cache
whose key includes the source messages, the QML contexts and the Qt version;
reuse verifies integrity and publication is atomic. No binary catalog is
committed.

To add a language:

1. Add the code and native name to `languages.json`, keeping Italian.
2. Create the language folder using the complete keys of the English catalogs
   as the message inventory and translate the JSON files. Keep the Python
   `{name}` and Qt `%1` placeholders and the extensions in file filters intact.
   Translate the standard Qt button labels too.
3. Run `python3 scripts/check_translations.py`: it checks coverage of the
   marked messages, conflicting duplicates, placeholders and entries the source
   does not use. Labels generated from static tables also need catalog entries.
4. Run the localization and interface tests and verify layouts at the
   supported sizes, font fallback, text direction where needed, persistence of
   the choice, literal user data and every dialog in the new language.

A registered catalog appears in the selector without touching any logic; the
review of translation and layout remains the responsibility of whoever adds the
language.

## Building the packages

```sh
python3 scripts/build_deb.py                      # Ubuntu 24.04 (noble), host Python 3.12
python3 scripts/check_deb.py --target noble
python3 scripts/build_deb.py --target resolute    # Ubuntu 26.04, Python 3.14 in a container
python3 scripts/check_deb.py --target resolute
```

The noble build requires `/usr/bin/python3` 3.12 and `dpkg-deb` on the host;
resolute uses the separate recipe in `packaging/resolute/` (Dockerfile, build
requirements lock). The `check_deb.py` checks run in throwaway containers,
launch the application from outside the checkout and exercise the package
lifecycle and data preservation. Docker is needed for these checks and for the
resolute build, not for using Kira. Outputs go to `dist/`
(`kira-desktop_<version>_amd64.deb` and `SHA256SUMS`) and `dist/resolute/`.

What the package contains:

- the **immutable** Python dependencies under `/opt/kira-desktop/lib/`,
  installed from the `packaging/requirements.lock.txt` lock without privileged
  `pip`;
- a launcher that uses the system Python and prepends that folder to the import
  path, installed as `/usr/bin/kira-desktop` and under the short name
  `/usr/bin/kira`;
- the systemd user unit, the `.desktop` file and the mascot in
  `/usr/share/pixmaps/kira-desktop.png`;
- in `/usr/share/doc/kira-desktop/`: Kira's licence (`copyright`), the
  third-party licences in `licenses/` (Qt and the DeepSeek Web WebAssembly
  module), `dependencies.json` with the name, version and licence of every
  bundled dependency, the requirements lock and the documentation (README,
  `docs/` and the community documents) with the repository layout, so relative
  links keep working;
- the declared native dependencies, including `xvfb` and `libxtst6` for the
  isolated browser.

The package does **not** contain model weights, Codex binaries or credentials,
Chromium (the user downloads it with `kira-desktop browser-install`) or the
Claude Code extra. Keep the two targets separate: their ABI outputs are not
interchangeable. After changing a dependency, rebuild and recheck both
artifacts; to identify a package compare its SHA256 and its Python/QML/asset
content with the source, not just the file name.

The package's reproducible timestamps rely on the content-keyed QML cache: a
new package must never reactivate an old cache, explicit cache overrides must
be respected and temporary environment variables must be restored after the
first load. An upgrade is not fixed by clearing unrelated caches or data.

## Publishing a version

1. On the `dev/<version>` line update the version in `pyproject.toml` and in
   `src/kira/__init__.py`, then run `uv lock` (it only changes the project
   line) and update the version placeholder in
   `.github/ISSUE_TEMPLATE/bug_report.yml`.
2. Run all the [checks](#checks), regenerate the screenshots if the interface
   changed, build and check both packages as described above, then move `main`
   to the same commit.
3. In **Actions → Release packages → Run workflow**, choose `main`. The
   `.github/workflows/release.yml` workflow builds the two packages with the
   same scripts and checks them with `check_deb.py` in containers; you can also
   run it from `dev/<version>` to try the build and checks without publishing
   anything. Only on `main` does a second job, the only one with write access,
   attach them to a **draft** release `v<version>` under stable names
   (`kira-desktop_ubuntu24.04_amd64.deb`, `kira-desktop_ubuntu26.04_amd64.deb`)
   together with their `SHA256SUMS`. The draft's notes start from
   `.github/release-notes.md` and link the documentation of the built commit,
   so the links already work in the draft; if the draft already exists, the
   workflow only refreshes its commit and files and leaves the notes
   untouched.
4. Review the draft, complete the notes and publish it: publishing creates the
   tag. The workflow refuses to replace the files of an already published
   version, so any later fix requires a new version.
5. Keep an exact copy of the published version as `release/<version>`.

The stable names keep the `releases/latest/download/...` links used in the
README and in [Installation](INSTALLATION.md#installing-from-the-package) valid.

## Customizing the interface

The mascot is `src/kira/ui/assets/kira-mascot.png` (PNG with transparency),
included in the wheel and installed as `/usr/share/pixmaps/kira-desktop.png`.
The theme, presets, colours and fonts the user can change are described in
[Appearance and language](APPEARANCE_AND_LANGUAGE.md); window decorations
remain the window manager's.

The shared QML components define buttons, the composer, menus, scroll bars and
dialogs; the Python models and controllers provide bounded data and commands.
The work areas use `KiraPanel` and `KiraPanelHost` for panels, `ToolReceipt`
for tool receipts, `KButton` (with optional discreet selection), `KIcon`,
`KComboBox`, `KScrollBar` and `KToolTip`. The style reads the appearance
palette in `ui/appearance.py`; these files must stay in the wheel and in the
native packages.

When you touch a shared control, preserve:

- text selection and copying, including in receipts and code blocks;
- keyboard focus and the navigation order;
- font scaling and high resolution;
- the layouts in small windows and with the browser docked alongside;
- a stable scroll position during streaming;
- literal external text: only the assistant's Markdown renderer interprets
  formatting.

Check the control's real implicit sizes and the contrast of popups in the
light, dark and custom themes. The content-keyed QML cache guarantees that the
change is loaded even in a package with fixed timestamps.

## Source/profile discipline

Source code and user data have separate lifecycles. Persistent profiles live
outside the checkout, in the standard XDG paths or in an explicitly chosen
absolute `KIRA_HOME`. Credentials, chats, backups, local reports, screenshots
of personal sessions and paths with real user names do not enter the
repository: `check_public_tree.py` intercepts some of them, the rest is the
responsibility of review. `KIRA_HOME` does not isolate Codex credentials or the
quotas of a shared account: that is why tests use mock providers and isolated
profiles by default.

## Branches and commits

- `main` holds the latest published version, a single reviewed snapshot per
  release, and is the only branch of the public repository: open pull requests
  against `main`.
- The maintainer develops the next version on a `dev/<version>` branch and keeps
  exact copies of the published versions as `release/<version>`; these branches
  are not published.

Commit messages follow the form `type(scope): summary`, for example
`fix(ollama): report stream inactivity as a timeout` or
`docs(guide): describe sidebar modes`. Types: `feat`, `fix`, `docs`, `test`,
`refactor`, `security`, `ci`, `i18n`, `style`, `chore`. Code, comments and
commits are in English; the source documentation is in `docs/it/`.

## What a pull request must contain

1. The description of the behaviour change and of the document it touches.
2. The list of checks run; if you did a verification with a real provider, say
   so and state its scope, without attaching the report.
3. A test that would fail without the change.
4. The documentation update in `docs/it/` for every change visible to the user
   or relevant to developers, and of the corresponding translation in
   `docs/en/`.
5. The catalog entries for every new interface string and, if you added a
   `ComboBox`, the updated counters in the tests.

For security issues follow [SECURITY.md](../../SECURITY.en.md) instead of
opening a public issue; the community rules are in the
[Code of conduct](../../CODE_OF_CONDUCT.en.md).
