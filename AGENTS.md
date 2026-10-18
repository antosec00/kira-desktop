# Kira Desktop engineering

This repository contains Kira Desktop. Start at `docs/README.md`, then read
`docs/CONTRACTS.md` and `docs/PROFILES.md`.
Current contracts and explicit user instructions govern implemented features.
User-facing updates and `docs/USER_GUIDE_IT.md` are Italian; implementation and
technical documentation are English. Preserve the configured builder model and
reasoning effort. Never modify global Codex configuration or credentials.

Start documentation at `docs/README.md`. Keep the Italian guide consistent with
`OPERATIONS.md`, `CONTROL.md`, `BUILD_AND_CUSTOMIZATION.md` and canonical
contracts. Treat every tracked file and commit as public. Keep machine-specific
operations notes, raw provider reports, screenshots of personal sessions and
original historical records in a private archive outside the checkout. Preserve
their original bytes and hashes there; publish only reviewed summaries.
Update `docs/STATUS.md` for current qualification; synthetic tests are not live
provider evidence. Use relative checkout paths, standard XDG defaults and
explicit user-supplied paths in public instructions.

Stack: Python, PySide6/QML, asyncio service, SQLite and private Unix IPC. Noble
uses Ubuntu 24.04 amd64/Python 3.12; Resolute uses Ubuntu 26.04 amd64/Python 3.14.
Read `docs/PLATFORM_TARGETS.md` before packaging. Commands:

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

Application source and user data are separate. Respect explicit KIRA_HOME
settings. `docs/OPERATIONS.md` describes standard XDG defaults and explicit
profile overrides, not a maintainer's active installation. Do not discover or
modify unrelated personal directories or private archives. Keep documentation,
fixtures and comments focused on this application and its supported workflows;
omit unrelated projects and maintainer development history.
Generated verification reports belong under ignored `build/`;
never force-add them without a separate public-data review. Back up privately
before removing historical records or rewriting repository history.

Mark application UI text for translation and update the bundled catalogs.
Preserve literal user/provider/tool content and identifiers when localizing.
See `docs/LOCALIZATION.md` for catalog ownership and language-extension checks.

Publication uses one reviewed source snapshot of the current release on `main`.
The user authorized `release/0.8.5` as an exact retained copy of that release,
and `feature/project-research` for independent development on the private GitHub
repository. Preserve both branches when publishing a release. The research
checkout has its own Python environment, explicit test profile, cache and
scratch projects. Promote qualified research into the installed release only
when explicitly authorized; source synchronization alone does not authorize it.
Keep older private development history, raw verification records and recovery
material outside the published repository. Do not publish unrelated branches
or release-history tags.

Before replacing a published snapshot, preserve all local/remote refs and linked
worktree states privately, verify exact trees and use explicit expected remote
revisions. Reapply authorized research-only changes onto a reviewed release
snapshot when explicitly refreshing its baseline; retain original commits
privately. Never merge rewritten publication history into the independent
historical repository. Branches share repository visibility, so review retained
release and experimental history before making the repository public.

Preserve running user work during consolidation. Git metadata and documentation
can be aligned without reloading code; installing different application code,
changing dependencies or migrating the active profile requires verified idle
state and a consistent backup. Mock providers and isolated profiles are the
default for research tests; `KIRA_HOME` does not isolate Codex credentials or
shared-account quotas. Keep roadmap proposals distinct from released behavior.
