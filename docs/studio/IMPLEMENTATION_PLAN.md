# Kira Studio — B: repository-specific implementation plan

**Architecture and staged plan.** M1 experimental source has now been committed to this branch but remains unqualified; see [M1_IMPLEMENTED](M1_IMPLEMENTED.md). Later milestones are proposals, not shipped capabilities. Baseline: current private `feature/kira-studio` branch from Kira Desktop 0.15.1 `main`. No automatic merging or installation. The branch should remain private and isolated from the independent `feature/project-research` branch and retained release branches. Follow `AGENTS.md` / `docs/OPERATIONS.md`, including backups and idle-state rules.

## Zero: architecture and release discipline

- Do **not** modify source, package, SQLite schema, profile, credentials or existing running work in M0.
- All tests use synthetic samples in an explicitly isolated `KIRA_HOME` profile; never use the installed real account store for exploratory tests.
- Create documentation contracts first. Keep main unchanged; every milestone explicitly qualifies the relevant Ubuntu 24.04 / Ubuntu 26.04 package separately.
- M0 decision gate: verify current `Main.qml` split, tool allowlists and parser/renderer performance offline. No invented performance claims.

## M1: safe opt-in shell and offline poster engine

**Scope:** visible Studio toggle (disabled by default), world list and 2D canvas, agent access gated; declarative poster/geometry render. No arbitrary user code execution.

| Existing integration | Planned isolated change |
| --- | --- |
| `src/kira/ui/qml/Main.qml` | Convert right-side slot into Browser/Studio host. Preserve browser split rules, chat width minimum, details pane, panel-host overlay and narrow-window behavior. |
| `src/kira/ui/qml/BrowserPane.qml` | Keep implementation untouched; only embed as before in shared host. |
| `src/kira/ui/app.py` | Register StudioController and read-only private frame provider with lifecycle-scoped ownership. |
| `src/kira/ui/controller.py` | Own StudioController, project/chat transitions, disconnect/reconnect and enablement UI. |
| `src/kira/ui/qml/StudioPane.qml` (new) | New toolbar, canvas, scene tree, inspector, zoom, revisions and explicit enable button. |
| `src/kira/domain/studio.py` (new) | Closed scene JSON v1, typed finite geometry, action validators, budget constants. |
| `src/kira/studio/{scene,vector,raster,text}.py` | Trusted bundled drawing algorithms; use existing Qt 2D primitives. |
| `src/kira/service/{studio,studio_store}.py` | Project-owned world revisions, asset hash store, stable request IDs, cancellation. |
| `src/kira/service/schema15.py` (new) | Additive database migration **only when M1 implementation is approved**, with consistent backup. |
| `src/kira/service/server.py` | Register narrow `STUDIO_METHODS` dispatcher, lifetime and cleanup. |
| `src/kira/domain/contracts.py` | Bump schema from 14 to 15 only after migration and restore tests. |
| `src/kira/tools/studio_worker.py` | Renderer subprocess driven by data, with bounded memory/time, no user script. |
| `src/kira/ui/translations/*` | Update Italian and English strings per localization checks. |

**Demo A:**
Input `"Scrivi con un pennello blu Ambra mi manchi, su uno sfondo delicato"`. Output exactly spelled phrase, layer-specific edit, reversible brush-style text, editable background, export PNG; optional SVG export with explicitly documented text/font conversion. Reopen after restarting the application with Studio OFF, then enable and inspect saved result. Never claim neural photorealism.

**M1 test set:** scene JSON fuzz/malformed inputs, Unicode text including accents, object hit test, compositing alpha, undo/redo, stable diffs, protected assets, expected-revision races, crash mid-write, frame-size limits, browser still functional, OFF mode zero workers, read-only mode unaffected, background service restart and preserved settings.

## M2: declarative agent tools and creative image editor

Add narrow action gateway `kira.studio` only while user grants **this project/chat** scene editing. Use common provider tool bridge, including `src/kira/backends/client_tools.py`, `src/kira/backends/kira_mcp.py`, and provider-specific direct/Ollama adapters. Shared registered tool constants and bounded catalogs must be updated atomically; preserve provider model/effort and Codex configuration. No synthetic tool success may be presented as confirmed model use.

Add layer/mask/selection/brush APIs, QImage tiled operations, import/edit/export, graph/chart primitives and controlled interaction widgets. Add approval for copying real project files into world and privacy-check on export. Securely reject disallowed extensions, huge image dimensions, fonts or malformed masks. Provide exact local patch path from selection/object ID; no full-scene destructive regeneration.

**Demo B:** manually import a permitted image; crop and adjust selected layer, add blue brush text. Agent changes only mask region and exports before/after. Verify original hash unchanged, third-party metadata not leaked and undo stable.

## M3: interactive research and deterministic simulation

Own finite-state/discrete event engine, trust graphs, timeline, scrubber, source-linked evidence and negative controls. Integrate `src/kira/service/research*.py` by typed owned references instead of altering research assessments automatically. Distinguish observed, inferred, simulated and source-derived facts. Export trace and test manifest.

**Demo C:** local synthetic race/revocation timeline; fork two legal event schedules, run property checks, show one violating and one non-violating schedule if justified by fixture assumptions. Never imply remote proof.

## M4: minimal native 3D viewer

Implement our own scene graph math, primitive mesh generator, view/projection matrix, clipping, depth buffer and shaded low-poly rasterization in separated engine modules. Mouse orbit/pan/zoom, select/layer/annotate, per-object stable IDs and animation timeline. CPU baseline under WSLg, optional GPU only when measured and separately packaged/qualified. If WSLg software renderer is slow, use bounded tile redraw and frame pacing rather than overriding the user's explicit Qt render environment.

**Demo D:** 3D graph of services as low-poly objects linked with protocol annotations; select node, inspect evidence, alter graph and retain camera state.

## M5: optional isolated procedural tools and media export

Separate user-reviewed executable components from baseline declarative code. Consider restricted native libraries, FFmpeg exporter or opt-in offline generative models, one at a time, each with independent worker, dependency budget, provenance and demonstrable containment. Do not treat a subprocess under the same UID as a sandbox. Feature probe missing OS isolation and refuse the extension. No auto-download or hidden network calls.

## Detailed acceptance gates

| Gate | Pass criterion |
| --- | --- |
| OFF | Fresh/new project has no Studio workers/tools, no Studio network and unchanged chat/browser behavior |
| Authorized edit | Only user-enabled current project/chat; no agent-granted self-enable; recheck before commit |
| World durability | Scene revision, assets, viewport and layer IDs survive restart; crash rollback replays no unknown effect |
| Concurrency | Expected revision and operation fingerprint prevent overwrite and duplicate side effects |
| Rendering | Stable scene->preview output within documented tolerances, no invalid buffer access or unbounded allocations |
| Editing | Exact text; localized changes; original imported asset bytes untouched; undo/redo recoverable |
| Research | Source-derived and simulated claims visibly distinguishable; synthetic negative controls work |
| Isolation | Malformed documents cannot request host code, file read, network, arbitrary QML or shell |
| Existing guarantees | Existing full suite + localization + public-tree checks + migration + installed-package tests pass |
| Resource | Timed WSLg/native baseline reports CPU, peak RSS, frames and dimensions; limits chosen from real measurements |

## Engineering work packets for Codex subagents

1. **Contracts owner:** Studio JSON validators, permission/ownership, docs/tests. No UI.
2. **2D engine owner:** geometry/paint/text, benchmark fixtures and deterministic snapshots. No service.
3. **Storage owner:** schema/migration/idempotency/asset integrity. No UI/renderer.
4. **UI owner:** StudioPane and workspace host, preserving BrowserPane; only controller interface agreed in advance.
5. **Simulation owner:** event/state engine and negative-control fixtures, M3 only.
6. **Integrator:** exclusively owns shared `server.py`, `contracts.py`, bridge/allowlists, `Main.qml`, packaging and final promotion. Resolve interfaces before parallel edits; no uncontrolled background coding or blanket full-access.

Order: define contracts and threat model -> offline renderer and tests -> storage/recovery -> UI prototype -> gated provider tools -> image tools -> research -> 3D. One small, reviewable commit per stage, never leap directly into a multi-engine bundle.

## M1 suggested concrete test filenames

`tests/test_studio_contracts.py`, `tests/test_studio_render2d.py`, `tests/test_studio_store.py`, `tests/test_studio_enablement.py`, `tests/test_studio_preview_transport.py`, `tests/test_studio_qml.py`, `tests/test_studio_browser_regression.py`, `tests/test_studio_migration.py`. Add headless Qt tests `QT_QPA_PLATFORM=offscreen`; separate native and WSLg visual verification. No test outcomes are claimed here.

## Build verification once implementation exists

```sh
uv sync --frozen
QT_QPA_PLATFORM=offscreen uv run pytest tests -q
uv run ruff check src tests scripts packaging
uv run mypy src
python3 scripts/check_public_tree.py
python3 scripts/check_translations.py
python3 scripts/build_deb.py
python3 scripts/check_deb.py dist/<qualified-package>.deb --target noble
```

Run corresponding independent `resolute` tests/package on target environment. **These are future instructions, not commands run during this design task.** No installation, merge, release, live provider usage or migration is authorized by the design-only work.
