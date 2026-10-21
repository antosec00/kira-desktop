## What changes

<!-- Behavior change in one or two sentences, and the contract or guide it touches. -->

## Why

<!-- The problem, issue number or design note. -->

## Checks

- [ ] `QT_QPA_PLATFORM=offscreen uv run pytest tests`
- [ ] `uv run ruff check src tests scripts packaging` and `uv run mypy src`
- [ ] `python3 scripts/check_translations.py` (new UI strings have English catalog entries)
- [ ] `python3 scripts/check_public_tree.py` (no personal paths, credentials or unreviewed binaries)
- [ ] Tests added or updated for the behavior change
- [ ] Documentation updated in `docs/it/` (Italian) and its translation in `docs/en/`

## Live checks (if any)

<!-- Which provider, which account scope, what was observed. Synthetic tests are not live evidence. -->
