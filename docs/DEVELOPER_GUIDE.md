# Developer guide

A map of the repository for people who already know their way around Python,
Qt and agent harnesses. It points to the authoritative documents instead of
repeating them; the engineering rules every change must follow are in
[AGENTS.md](../AGENTS.md).

## Mental model

Kira is three cooperating pieces in one user session:

| Piece | Lives in | Owns |
| --- | --- | --- |
| **GUI** (Qt Quick / PySide6) | `src/kira/ui/` | Intent and presentation: QML in `ui/qml/`, controllers exposed to QML as context properties (`Workspace`, research, web, workbench, attachments…), GUI-only preferences in `config/ui.json`. |
| **Service** (asyncio, SQLite WAL, private Unix IPC) | `src/kira/service/` | Execution and durability: projects, sessions, runs and their sequenced events, tool execution with the project permission, the browser/Computer actor, teams, research registry, questions, attachments. The only SQLite writer. |
| **Backends** (provider adapters) | `src/kira/backends/` | One adapter per provider (`ollama`, `codex`, `openai_chatgpt`, `deepseek_account`, `deepseek_web`, `claude_code`). They receive a `RunContext`, stream events back through `ctx.emit` and either execute Kira's tools through `ctx.client_tool_call` or observe a provider's native tools. |

The contracts between them — IPC methods, event envelopes, permissions,
database schema — are canonical in [CONTRACTS.md](CONTRACTS.md);
[ARCHITECTURE.md](ARCHITECTURE.md) explains the process boundaries and the
recovery model. `src/kira/domain/` holds shared pure definitions (tool specs,
contracts, questions, web and computer namespaces) used by both sides.

## Repository layout

```
src/kira/
  cli.py            kira-desktop entry point (start GUI, diagnostics, stop-service)
  client.py         GUI-side IPC client and service bootstrap
  backends/         provider adapters + client_tools.py (Kira tool bridge for providers)
  domain/           contracts, tool specifications, questions, web/computer namespaces
  service/          server.py (dispatch), runs, assistant, teams, web.py (browser actor),
                    research.py, knowledge.py, questions.py, attachments, storage
  tools/            file/command tools, browser engine and auth bridge
  ui/               app.py (engine), controller.py (Workspace), models.py (transcript),
                    *_controller.py, qml/, translations/en/*.json, assets/
tests/              synthetic pytest suite (pytest-qt, offscreen Qt, fake providers)
scripts/            check_translations.py, check_public_tree.py, build_deb.py, check_deb.py,
                    installed_smoke.py, upgrade_smoke.py
packaging/          Debian packaging for Ubuntu 24.04 (noble) and 26.04 (resolute)
protocol-schema/    JSON schema of the IPC/event envelopes
docs/               contracts, guides, status; USER_GUIDE_IT.md is the Italian user guide
```

## Working on the code

```sh
uv sync --frozen --extra claude-code
export KIRA_HOME="$HOME/kira-dev-profile"     # isolated profile for development
uv run kira-desktop                            # GUI starts or connects to the service
uv run kira-desktop stop-service               # refuses while work is active
```

GUI and service must have the same version; a mismatch is refused at the IPC
handshake. [OPERATIONS.md](OPERATIONS.md) covers profiles, backups and upgrades;
[PROFILES.md](PROFILES.md) explains source/data separation.

### Checks

```sh
QT_QPA_PLATFORM=offscreen uv run pytest tests     # whole synthetic suite (~20 min)
uv run ruff check src tests scripts packaging
uv run mypy src
python3 scripts/check_translations.py             # every qsTr()/tr() string has an English entry
python3 scripts/check_public_tree.py              # no personal paths, no unreviewed binaries
```

### Tests, in practice

- UI tests create a real `QQmlEngine` with a synthetic `Workspace` (see
  `tests/test_ui_*.py` fixtures): they walk `childItems()`, click through
  `QTest.mouseClick`, evaluate QML expressions with `QQmlExpression`, and assert
  that no binding loop, `ReferenceError` or `TypeError` appears in the Qt log.
- Service tests start a real `Service` on a temporary profile with a fake
  provider (`backend_factory=`) and dispatch IPC methods directly.
- Backend tests use fake SDKs or HTTP transports; nothing contacts a provider.
- Geometry and `objectName`s are pinned deliberately. The ComboBox counts in
  `tests/test_ui_contracts.py` (39, 156 measurements) and
  `tests/test_ui_workspace.py` (45) change whenever a ComboBox is added.
- To look at a layout, render it offscreen: create the engine with a synthetic
  workspace, resize the window, `window.grabWindow().save(...)`; the UI test
  fixtures show the setup. Screenshots in `docs/images/` are produced this way
  from fixture data only.

### Adding a provider

1. Implement the adapter in `src/kira/backends/<name>.py`: `discover()`,
   `run(ctx)`, `close()`; emit `message.delta`/`message.completed`,
   `tool.started`/`tool.completed`, `usage`, `session.updated`.
2. Register it in `service/provider_routing.py`, the backend allow-lists in
   `service/assistant.py`, `project_tools.py`, `research.py`, `knowledge.py`,
   `autonomous_goals.py`, `reasoning_summaries.py`, `web.py` (`WEB_BACKENDS`)
   and `attachment_inputs.py` (image capability), plus `tool_policy.py`.
3. Expose it in the GUI: `controller.py` (`setBackend`, labels, capabilities),
   the Models page and composer popover in `qml/Main.qml`, the provider switch
   dialog, translations.
4. Document it in [BACKENDS.md](BACKENDS.md) and the Italian guide, and record
   its qualification in [STATUS.md](STATUS.md). `claude_code.py` is a compact
   example of an adapter that observes native tools and serves Kira's tools
   through an in-process MCP server.

### Adding UI text or a preference

- Mark strings with `qsTr()` (QML) or `tr()` (Python) and add the English
  translation to `src/kira/ui/translations/en/*.json`; the check script fails
  on missing entries and on catalog conflicts.
- GUI preferences live in `ui/preferences.py` (`DEFAULTS` + `valid_preference`)
  and are exposed by `Workspace` properties/slots; they never call IPC.

## Releases and publication

`main` carries one reviewed snapshot per release; development happens on
`dev/<version>` branches with conventional commits. Before publishing:
update `CHANGELOG.md`, `docs/STATUS.md`, the version in `pyproject.toml`,
`src/kira/__init__.py` and `uv.lock`; build and qualify the packages
([PLATFORM_TARGETS.md](PLATFORM_TARGETS.md), [BUILD_AND_CUSTOMIZATION.md](BUILD_AND_CUSTOMIZATION.md));
run `scripts/check_public_tree.py`. Raw provider reports, personal screenshots
and migration records stay in a private archive ([evidence/README.md](evidence/README.md)).
