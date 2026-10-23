# Contributing to Kira Desktop

*[Leggi in italiano](CONTRIBUTING.md)*

Thank you for helping Kira grow. This page explains how the repository is
organized, how to set up a development environment and what a change needs
before it can be merged. Engineering rules that apply to every change are in
[AGENTS.md](AGENTS.md); feature behavior is described in the
[documentation](docs/en/README.md).

## Ground rules

- **Evidence over narration.** Service events establish what happened; a
  model's description never does. Code and docs must keep that distinction.
- **The user's data is private.** No credentials, personal paths, provider
  identifiers or chat content in fixtures, docs, screenshots or commits.
  `python3 scripts/check_public_tree.py` enforces part of this; review the rest.
- **Permissions are explicit.** Nothing may widen a project's permission, bypass
  the browser consent policy or execute a tool the user did not enable.
- **Languages.** The interface and the documentation are written in Italian;
  the documentation is translated into English in `docs/en/`. UI strings use
  `qsTr`/`tr()` and need an entry in the English catalogs.

## Development setup

Ubuntu 24.04 (Python 3.12) or 26.04 (Python 3.14), natively or on WSL2/WSLg,
with the system libraries listed in
[Installation → Running from source with `uv`](docs/en/INSTALLATION.md#running-from-source-with-uv).

```sh
git clone https://github.com/antosec00/kira-desktop.git
cd kira-desktop
uv sync --frozen --extra claude-code   # the extra is optional
export KIRA_HOME="$HOME/kira-dev-profile"   # never develop against your real profile
uv run kira-desktop
```

`KIRA_HOME` keeps the development profile (database, drafts, preferences,
runtime socket) apart from an installation in use. It does not isolate Codex
credentials or the quotas of a shared account, though: the `scripts/live_*.py`
scripts use the real account. The GUI and the service must run the same
version; before switching checkouts close the window and stop the service with
`uv run kira-desktop stop-service` (with the window open the interface would
restart it).

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
against a real provider, do it outside the suite with the `scripts/live_*.py`
scripts in a disposable profile and describe the outcome in the pull request.

Useful conventions:

- UI tests pin `objectName`s and geometry on purpose. If you add a `ComboBox`,
  update the pinned counts in `tests/test_ui_contracts.py` and
  `tests/test_ui_workspace.py`.
- Offscreen renders are the quickest way to review a layout change; see
  [Development](docs/en/DEVELOPMENT.md).
- Keep comments and names in the style of the surrounding code.

## Branches and commits

- `main` is the only branch of the public repository and holds the latest
  published release. Open pull requests against `main`: the maintainer
  integrates them and publishes them with the next version.
- Published versions are the `v<version>` releases on the
  [Releases](https://github.com/antosec00/kira-desktop/releases) page.

Commit messages follow the conventional form used in the history:
`type(scope): summary`, for example `fix(ollama): report stream inactivity as a
timeout` or `docs(guide): describe sidebar modes`. Types: `feat`, `fix`,
`docs`, `test`, `refactor`, `security`, `ci`, `i18n`, `style`, `chore`.

A pull request should:

1. describe the behavior change and the guide it touches;
2. list the checks you ran (and any live check, with its scope);
3. update the documentation in `docs/it/` for user-visible changes and its
   translation in `docs/en/`.

## Reporting problems

Use the issue templates. For anything security-related, follow
[SECURITY.en.md](SECURITY.en.md) instead of opening a public issue.

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.en.md).
