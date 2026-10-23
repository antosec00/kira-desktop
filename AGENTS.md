# Kira Desktop engineering

This repository contains Kira Desktop. Start at `docs/it/README.md` (the
documentation is written in Italian and translated in `docs/en/`), then read
`docs/it/CONTRATTI.md` and `docs/it/ARCHITETTURA.md`. The documented contracts
and explicit user instructions govern implemented features.

Languages: the application interface and the documentation are Italian; every
document in `docs/it/` has its English translation in `docs/en/` with the
matching name (see the table in `docs/README.md`). Code, comments, commit
messages and identifiers are English. Preserve the configured builder model and
reasoning effort. Never modify global Codex configuration or credentials.

Treat every tracked file and commit as public. Keep machine-specific notes, raw
provider reports, screenshots of personal sessions and development records in a
private archive outside the checkout; publish only reviewed summaries. Use
relative checkout paths, standard XDG defaults and explicit user-supplied paths
in public instructions. Documentation describes Kira as it is now: no version
history, no development or verification narrative, no internal schema numbers.
Keep roadmap proposals and unreleased experiments out of the public docs.

Stack: Python, PySide6/QML, asyncio service, SQLite and private Unix IPC. Noble
uses Ubuntu 24.04 amd64/Python 3.12; Resolute uses Ubuntu 26.04 amd64/Python 3.14.
Read `docs/it/INSTALLAZIONE.md` before packaging. Commands:

```sh
uv sync --frozen
QT_QPA_PLATFORM=offscreen uv run pytest tests
uv run ruff check src tests scripts packaging
uv run mypy src
python3 scripts/check_public_tree.py
python3 scripts/check_translations.py
uv run kira-desktop
python3 scripts/build_deb.py
python3 scripts/build_deb.py --target resolute
```

Use matching `scripts/check_deb.py --target noble|resolute` for each package.
Keep ABI outputs separate. Reproducible timestamps require content-keyed QML
caches; preserve explicit overrides and restore temporary cache environment
following the first load. Never clear unrelated caches or user data on upgrade.

Use parallel subagents for independent building or review work where helpful.
Assign explicit file ownership and interfaces before editing; the coordinator
owns integration and serializes shared contracts, dependencies and packaging.
No credentials or user conversation content belong in fixtures. Durable service
events establish execution; narration does not. Preserve running user work and
update only after verified idle state with a consistent backup.

Application source and user data are separate. Respect explicit `KIRA_HOME`
settings. `docs/it/INSTALLAZIONE.md` describes standard XDG defaults and
explicit profile overrides, not a maintainer's active installation. Do not
discover or modify unrelated personal directories or private archives. Keep
documentation, fixtures and comments focused on this application and its
supported workflows. Generated verification reports belong under ignored
`build/`; never force-add them without a separate public-data review. Back up
privately before removing historical records or rewriting repository history.

Mark application UI text for translation and update the bundled catalogs.
Preserve literal user/provider/tool content and identifiers when localizing.
See `docs/it/SVILUPPO.md` for catalog ownership and language-extension checks.

Branches: `main` holds the latest published release and is the only branch of
the public repository; the maintainer develops on `dev/<version>` and keeps
`release/<version>` copies of published releases outside it. Mock providers and
isolated profiles are the default for tests; `KIRA_HOME` does not isolate Codex
credentials or shared-account quotas. Installing different application code,
changing dependencies or migrating the active profile requires verified idle
state and a consistent backup.
