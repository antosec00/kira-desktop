# Building and customizing Kira Desktop

This guide covers development, package builds and interface customization.
[Profiles](PROFILES.md) describes source and user-data ownership;
[STATUS](STATUS.md) records qualification and remaining limits. Verification
reports follow the [evidence policy](evidence/README.md).

## Repository map

| Path | Responsibility |
| --- | --- |
| `src/kira/domain/` | Shared contracts, paths, team definitions and permissions |
| `src/kira/service/` | SQLite, authoritative execution, IPC and recovery |
| `src/kira/backends/` | Codex App Server, Ollama and direct ChatGPT adapters |
| `src/kira/tools/` | Validated local tools, owned Chromium worker and private authentication bridge |
| `src/kira/ui/` | Qt models/controllers, rendering, input, preferences and QML |
| `tests/` | Application unit, integration and real Qt interaction tests |
| `scripts/` | Desktop build, installed-package and explicit live-check tools |
| `packaging/` | Launchers, user service, native libraries/locks and license texts |
| `packaging/resolute/` | Separate Python 3.14 build recipe |
| `docs/` | App guides, contracts, qualification summaries and evidence policy |
| `build/`, `dist/` | Generated staging and package outputs; not user profile storage |

Current [contracts](CONTRACTS.md) include teams and chat functionality. Install
and configure providers separately as described in [Backends](BACKENDS.md).
The [0.4 workbench contract](WORKBENCH.md) covers composer recovery, quick actions,
queued work, explicit steering, modes, archive/branch/export and objectives.

## Development checks

On the Noble target, Ubuntu 24.04 amd64/Python 3.12, run from the root of your
checkout. Read [platform targets](PLATFORM_TARGETS.md) before building packages.

```sh
uv sync --frozen
uv run ruff check src tests scripts packaging
uv run mypy src
python3 scripts/check_translations.py
python3 scripts/check_public_tree.py
QT_QPA_PLATFORM=offscreen uv run pytest tests -q
export KIRA_HOME="$(mktemp -d -t kira-dev.XXXXXXXX)"
uv run kira-desktop
```

A fresh temporary test profile prevents conflicts with the user's running
instance. Keep the same `KIRA_HOME` for its GUI, service and diagnostic commands;
temporary directories are unsuitable for long-term user data. Stop this test
service with `uv run kira-desktop stop-service` before discarding its profile.
Do not copy credentials or personal project content into test fixtures.
Real provider checks may consume account resources and execute authorized work;
they use fresh disposable projects and are reported separately from deterministic
tests. The pinned lock is authoritative; ordinary UI work does not require
upgrading runtime dependencies or the host operating system.

## Native package build and qualification

The current source pins Playwright for the service-owned Chromium browser.
Desktop 0.9.1 uses headed Chromium on an owned private Xvfb display. Native
packages declare `xvfb` and `libxtst6` alongside the browser's existing X11 and
Chromium libraries. Source deployments need these OS packages separately;
`uv sync --frozen` installs Python dependencies only. Chromium is
downloaded explicitly into the selected user profile with
`kira-desktop browser-install`; package scripts do not download it or mutate
browser profiles. Source dependencies are installed by `uv sync --frozen`.
See [Web and browser](WEB_BROWSER.md) for the opt-in real-engine test and resource
boundaries. Requalify each target artifact after changing these dependencies.

Bundle the browser authentication extension and the Windows helper sources with
the Python application. In WSL, the helper uses Windows PowerShell and the Win32
WebAuthn API when available; the extension uses Chrome's documented
`webAuthenticationProxy` API. A helper availability check must not launch a real
authentication ceremony. Keep credentials, browser profiles, authentication
payloads and real user screenshots outside fixtures and package artifacts.

Qualify the two render paths separately: CDP page frames for agent control and
the complete private browser surface for manual control. Include native-dialog
rendering, surface identity changes, stale queued input, cancellation, IME and
small-window layouts. Synthetic WebAuthn tests, helper availability and an
actual user-completed passkey/login flow are distinct evidence. In particular,
headed Linux rendering does not establish Windows Hello support or support for
PRF, other unsupported extensions or cross-origin ceremonies. Windows-owned
secure dialogs are not part of the Linux frame capture.

```sh
python3 scripts/build_deb.py
python3 scripts/check_deb.py dist/kira-desktop_0.15.1_amd64.deb --target noble
python3 scripts/build_deb.py --target resolute
python3 scripts/check_deb.py dist/resolute/kira-desktop_0.15.1_amd64.deb --target resolute
```

Noble requires host `/usr/bin/python3` 3.12 and dpkg-deb. Resolute builds with
Python 3.14 in its separate container recipe. Installed checks use disposable
containers, launch from outside the checkout and exercise package lifecycle/data
preservation. Docker is needed for those container checks/builds, not to run the
Desktop application. Downloading uncached pinned wheels and system packages
requires the appropriate network repositories.

The build uses immutable bundled Python dependencies under `/opt/kira-desktop`,
a system-Python launcher, declared native display dependencies and no privileged
pip. No model weights or Codex credentials/binary are bundled. Target outputs
and ABI limits are in [PLATFORM_TARGETS](PLATFORM_TARGETS.md). Compare artifact
SHA256 and packaged Python/QML/assets with source; a filename/version alone is
not an identity check. Fixed package timestamps use content-keyed QML caches.

## Interface and mascot

The default appearance uses an anthracite palette with blue accents, rounded
panels and the Kira dog mascot on a transparent PNG. Light, dark and system modes,
presets, colors and fonts can be changed in [Appearance](APPEARANCE.md).
The master asset is
`src/kira/ui/assets/kira-mascot.png`, bundled and installed as
`/usr/share/pixmaps/kira-desktop.png`. Interface behavior is documented in
[design notes](DESIGN_033.md). Native window controls
remain owned by the window manager.

QML components define buttons, input, menus, scrollbars and dialogs; Python
models/controllers provide bounded data and commands. Keep external text literal
except the explicit safe assistant Markdown renderer. Maintain selection/copy,
keyboard focus, scaling, small-window layouts and stable scroll position.
Preferences are user-owned ui.json data. Appearance customization applies to
Kira's interface; the desktop environment manages the host's display settings.
Interface text uses the bundled [translation catalogs](LOCALIZATION.md).

Changing a shared control requires checking its real implicit dimensions and
popup contrast. Reproducible package timestamps must not leave an older QML cache
active; preserve explicit cache overrides and restore temporary environment
changes after initial loading. Do not solve updates by clearing unrelated data.

## Source/profile and release discipline

Source code and user data have separate lifecycles. Keep persistent profiles
outside the checkout and distribute only source and intended release artifacts.
Use standard XDG paths or an explicitly selected absolute `KIRA_HOME`, as
described in [operations](OPERATIONS.md). Keep local reports, credentials, chats
and backups outside the published tree.

For any rollout: pause pending queues, finish active work and native chat
operations, create a consistent backup, stop only the
owned idle service, update matching client/service code, relaunch and compare
data/settings. Record tests and limits in STATUS. General frontend fixtures do
not qualify Windows clipboard transfer, native rendering on every desktop or
live model entitlement.

Kira Desktop 0.13.0 expands the run-lane permission constraint in schema 12. Migration
tests must preserve existing records and reject opening the new schema with an
older service. Include
`composer.json` and referenced attachments in a complete private profile backup;
SQLite backup alone does not include GUI drafts. Source checks and application
wheel checks do not qualify a newly built native package: record each artifact's
actual validation in STATUS without carrying forward an older package result.

The application source uses MIT; retained dependency/wheel/license notices are
specified in [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md). Redistributing binary
Qt packages requires the applicable corresponding-source/license obligations.

Browser-consent qualification must cover live revocation, pending approval
identity, provider/account transitions and fork defaults. Synthetic service tests
are separate from installed QML/IPC/browser interaction checks.

Identity/library qualification must cover tools-disabled identity, provider
transport conversion, source ownership and paging, immutable revisions, declared
test expectations, worker ownership before dispatch, cancellation and restart
recovery. The wheel must include bundled knowledge resources and the stdlib-only
project-tool worker. Passing fixtures are not evidence of live model decisions.


Team qualification covers mailbox/assignment boundaries, rejection and bounded
repair of premature completion, round limits, actual worker receipts, deferred
chat continuation, scope/account and cleanup races, deduplication, restart
reconciliation and sender provenance. Preserve the no-progress guard. Synthetic
projects provide deterministic coverage, separately from live provider behavior.


Continuation-limit qualification covers omitted versus explicit zero, strict
bounds, source immutability, actual scheduling beyond a previous one-round limit,
request identity conflicts, deferred recovery, non-sticky old overrides, and
mouse/keyboard use in both resume surfaces. Include exact uncertain-request
retries and GUI restart without automatically starting inference.


The 0.11.0 native workspace uses reusable `KiraPanel` / `KiraPanelHost` QML
components, a `ToolReceipt` component and optional quiet selection on `KButton`.
These resources must ship in the wheel and native packages. Styling uses the
existing appearance palette; the design prototype is not an application webview
or a runtime dependency. Source and installed checks must exercise actual QML
panel navigation, keyboard input, original output controls and minimum-size
layouts. [Workspace contracts](CHAT_LAYOUT.md).

### Status and composer qualification (0.13.1)

Test light/dark/custom palettes, font scaling, reduced motion, hidden/minimized
windows, pooled/offscreen delegates, scope/replay and independent Team/chat states.
Keep controls usable with a narrow browser split and at the minimum window size.
Exercise long drafts/caret visibility, selection, attachments and guidance/queue
actions with the same composer object. Synthetic screenshots belong in ignored
build output; they are not evidence of a live provider turn.

## Personalized identities

Desktop 0.15.0 uses [Anima di Kira](KIRA_SOULS.md) for multiple private user-owned
identities. Configure them inside the application; no global Codex configuration,
model defaults or project source files are modified. New code uses schema 14 and
requires normal idle admission and a consistent profile backup before activation.
The standalone prototype remains a development example, not the installed implementation.
