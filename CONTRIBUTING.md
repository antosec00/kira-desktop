# Contributing to Kira Desktop

Thank you for helping Kira grow. This page explains how the repository is
organised, how to set up a development environment and what a change needs
before it can be merged. Engineering rules that apply to every change are in
[AGENTS.md](AGENTS.md); feature behavior is governed by the documented contracts
in [docs/](docs/README.md).

## Ground rules

- **Durable evidence over narration.** Service events establish what happened;
  a model's description never does. Code and docs must keep that distinction.
- **The user's data is private.** No credentials, personal paths, provider
  identifiers or chat content in fixtures, docs, screenshots or commits.
  `python3 scripts/check_public_tree.py` enforces part of this; review the rest.
- **Permissions are explicit.** Nothing may widen a project's permission, bypass
  the browser consent policy or execute a tool the user did not enable.
- **Languages.** User-facing text and `docs/USER_GUIDE_IT.md` are Italian;
  implementation and technical documentation are English. UI strings use
  `qsTr`/`tr()` and need an entry in the English catalogs.

## Development setup

Ubuntu 24.04 (Python 3.12) or 26.04 (Python 3.14), natively or on WSL2/WSLg.

```sh
git clone https://github.com/antosec00/kira-desktop-app.git
cd kira-desktop-app
uv sync --frozen --extra claude-code   # the extra is optional
export KIRA_HOME="$HOME/kira-dev-profile"   # never develop against your real profile
uv run kira-desktop
```

`KIRA_HOME` keeps the development profile (database, drafts, preferences,
runtime socket) apart from an installed application. The GUI and the service
must run the same version; stop a running service with `uv run kira-desktop
stop-service` before switching checkouts.

## Checks before a pull request

```sh
QT_QPA_PLATFORM=offscreen uv run pytest tests        # ~20 minutes, offscreen Qt
uv run ruff check src tests scripts packaging
uv run mypy src
python3 scripts/check_translations.py
python3 scripts/check_public_tree.py
```

Tests are synthetic: they use fixture projects, fake providers and offscreen
rendering, and they never contact a real provider. A change in behavior comes
with a test that would fail without it. When you need to prove something
against a real provider, do it outside the suite and record the outcome in
[docs/STATUS.md](docs/STATUS.md) as a live check, separate from synthetic coverage.

Useful conventions:

- UI tests pin `objectName`s and geometry on purpose. If you add a `ComboBox`,
  update the pinned counts in `tests/test_ui_contracts.py` and
  `tests/test_ui_workspace.py`.
- Offscreen renders are the quickest way to review a layout change; see
  [docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md).
- Keep comments and names in the style of the surrounding code.

## Branches and commits

- `main` — one reviewed snapshot per release. It is updated only by the maintainer.
- `dev/<version>` — the active development line (currently `dev/0.18`).
  Open pull requests against it.
- `release/<version>` — retained exact copies of published releases.

Commit messages follow the conventional form used in the history:
`type(scope): summary`, for example `fix(ollama): report stream inactivity as a
timeout` or `docs(guide): describe sidebar modes`. Types: `feat`, `fix`,
`docs`, `test`, `refactor`, `security`, `ci`, `i18n`, `style`, `chore`.

A pull request should:

1. describe the behavior change and the contract or guide it touches;
2. list the checks you ran (and any live check, with its scope);
3. update `docs/USER_GUIDE_IT.md` for user-visible changes and the relevant
   English contract for technical ones;
4. update `CHANGELOG.md` under *Unreleased*.

## Reporting problems

Use the issue templates. For anything security-related, follow
[SECURITY.md](SECURITY.md) instead of opening a public issue.

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).
