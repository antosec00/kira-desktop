# Kira Studio qualification triage — 18 Oct 2026

**Private experimental branch:** `feature/kira-studio`, version `0.16.0.dev1`, database schema `16`.  
**Target:** WSL2 / Ubuntu / offscreen Qt tests.  
**Status:** NOT QUALIFIED — fixes committed, awaiting targeted user-side verification.  
**Source of actual observations:** user-provided WSL pytest terminal output; no installed live-provider call was executed by the assistant.

## Observed runs before the fixes in this document

- `QT_QPA_PLATFORM=offscreen uv run pytest tests/test_studio_*.py -q`: **38 passed** in 18.81s.
- `QT_QPA_PLATFORM=offscreen uv run pytest tests -q`: **3485 passed, 13 skipped, 9 failed, 2 errors** in 1272.67s (21m12s).
- Full suite failures include 5 visual geometry regressions, 2 outdated test assumptions, one incomplete historical schema fixture, one provider catalogue-boundary assertion and one synthetic browser manual-login timeout. Two additional teardown errors lacked exact original Qt warning details.

## Failures and remediation on this branch

| Failure | Root cause or leading explanation | Committed action | Follow-up |
| --- | --- | --- | --- |
| `test_direct_context_store.py::test_schema6_migration_preserves_original_rows_and_creates_private_backup` | The test simulated schema 6 by dropping later tables but accidentally retained schema-15/16 Studio tables. Migration then attempted to recreate `studio_worlds` | Drop all newer Studio tables, in dependent-first order, within the test fixture | Rerun exact test; verify preserved legacy rows |
| `test_service_workspace.py::test_diagnostics_export_whitelists_values_and_excludes_content` | Assertion hardcoded schema 14 while feature branch legitimately uses schema 16 | Assert `SCHEMA_VERSION` | Rerun exact test; confirm no canary leak |
| `test_soul_mcp_bridge.py::test_shared_validator_still_rejects_duplicate_and_overbound_definitions` | Shared tool bound increased by exactly one when `kira.studio` was registered. Old "full" test fixture was no longer full | Register Studio in the synthetic complete catalogue and preserve overbound/duplicate rejection assertions | Rerun exact test |
| `test_ui_appearance.py::test_live_palette_reaches_dialogs_controls_and_literal_output` (light, dark) | Long `0.16.0.dev1` string overflowed the version label by ~3.1 px at 1380 width | Disable decorative letter spacing for extended version identifiers | Rerun both themes |
| `test_ui_design.py::test_model_panel_toggle_gains_chat_space_without_changing_session` (3 variants) | Same version overflow; plus new Studio toolbar label consumed another row at narrow width, reducing chat body height | Compact Studio and Knowledge controls to accessible icon-only buttons on narrow chat widths; preserve labels/tooltips elsewhere | Rerun 4 variants with native-size geometry |
| `test_service_web.py::test_manual_login_pauses_and_waits_for_user_confirmation` | **Not yet established.** Test timed out waiting for approval; Studio is off by default in this test. May be an intermittent scheduling/path issue or a real regression | Preserve 5s bound; on timeout report ordered service events, browser actions and number of synthetic provider requests | Run isolated test and inspect diagnostic if still failing; do not treat as fixed |
| `test_ui_live_conversation.py::test_related_team_run_survives_project_refresh_and_gui_reopen` (2 teardown errors) | Qt fixture's assert ran before QML cleanup, producing hundreds of unrelated null-context `TypeError` messages. The original `ReferenceError`/binding-loop record was not shown. | Always finalize QML window/engine before assertion; report up to 10 original Qt warnings | Run 2 variants and examine actual warnings if still failing; do not treat as fixed |

## Targeted verification (instead of 21 minutes immediately)

Run in the disposable worktree and with no active GUI/session using this command:

```bash
QT_QPA_PLATFORM=offscreen uv run pytest \
 tests/test_direct_context_store.py::test_schema6_migration_preserves_original_rows_and_creates_private_backup \
 tests/test_service_workspace.py::test_diagnostics_export_whitelists_values_and_excludes_content \
 tests/test_soul_mcp_bridge.py::test_shared_validator_still_rejects_duplicate_and_overbound_definitions \
 tests/test_ui_appearance.py::test_live_palette_reaches_dialogs_controls_and_literal_output \
 tests/test_ui_design.py::test_model_panel_toggle_gains_chat_space_without_changing_session \
 tests/test_service_web.py::test_manual_login_pauses_and_waits_for_user_confirmation \
 tests/test_ui_live_conversation.py::test_related_team_run_survives_project_refresh_and_gui_reopen \
 -q --tb=short
```

This reruns 12 parametrized cases (9 originally failed + 2 teardown errors + 1 formerly passing UI variant). Also run `uv run ruff check src tests scripts packaging`, `uv run mypy src`, `python3 scripts/check_translations.py` and `python3 scripts/check_public_tree.py`. Only after focused tests pass should a new full-suite run be requested.

## Qualification boundaries

No GUI installation, provider login, active user-profile migration, branch merge, package release, external browser session, real account token or live research run is authorized by this work. The stable `main` branch is unchanged. The remaining failures, if any, must be addressed with new evidence, not suppressed via skipped tests or relaxed assertions.
