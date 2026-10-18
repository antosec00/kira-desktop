# Kira Studio M1 — committed experimental implementation

**Historical M1 snapshot:** The follow-on AI provider gateway is described
in [M2_PROVIDER_BRIDGE.md](M2_PROVIDER_BRIDGE.md). The version and schema
numbers below describe M1, not the newest development branch.

**Development status:** source implementation committed on the private
`feature/kira-studio` branch, **not yet run or qualified on an Ubuntu installation**.
Application package identity: **0.16.0.dev0**; additive profile schema **15**.
Do not install/launch against the user's active 0.15.1 profile. Main and its
published releases remain unchanged. These facts describe source availability,
**not** a passing native GUI, package, performance or provider qualification.

## What now exists

- A **Studio** button next to the existing Browser control in the chat bar.
- Independent Browser and Studio right-side panes; switching presentation does
  not close the isolated browser actor or delete a Studio world.
- Studio OFF by default for **each project**. Explicit user activation and
  revocation in the Studio pane; no model Studio tool or autonomous Studio worker
  is registered in M1.
- One editable, persistent poster scene per project (M1 single-scene scope).
  User may create exact Unicode caption, change its color, modify background,
  move text and restore any of the 20 latest listed revisions.
- Append-only scene revisions stored in SQLite tables; expected-revision
  concurrency checks, durable idempotent operation receipts and user-supplied
  request IDs. Existing chats/Teams/browser data are not revised.
- Closed `kira.scene/1` format; only validated background, brush-text and star
  primitives. Arbitrary Python/JS/QML/shaders/URLs are **not executable scene
  primitives**.
- Kira-owned geometry, decorative star paths, editable Unicode text layout
  and brush-like outlines using the **existing Qt QPainter/QImage** dependency.
- A short-lived headless renderer process using the installed package's
  isolated Python root; derived preview PNG and explicit copy to private
  `studio/exports`. No new heavy libraries, model download or Studio network.
- Basic tests committed for format validation, source immutability,
  enable/disable, idempotency, scene history, migration, headless PNG and QML
  presence. **Not executed here**: the connected GitHub tool reads/writes source
  but does not execute project tests, and the local container lacks PySide6.

## User demo after isolated validation

1. Finish/save all active Kira work. Do **not** launch the experimental branch
   with the live user profile, credentials or existing active SQLite DB.
2. Use a separate worktree, isolated virtualenv, and explicit disposable path.
   Example (developer machine):
   ```bash
   git fetch origin feature/kira-studio
   git worktree add ../kira-studio-m1 origin/feature/kira-studio
   cd ../kira-studio-m1
   uv sync --frozen
   export KIRA_HOME="$PWD/build/studio-test-profile"
   QT_QPA_PLATFORM=offscreen uv run pytest tests/test_studio_*.py -q
   uv run ruff check src tests scripts packaging
   uv run mypy src
   python3 scripts/check_translations.py
   python3 scripts/check_public_tree.py
   ```
3. Only after tests pass, launch `uv run kira-desktop` in a real supported
   graphical session **with the disposable KIRA_HOME** and a disposable project.
   Start Studio from the chat toolbar. Confirm the OFF state. Enable by clicking.
4. Create poster **Ambra mi manchi**. Confirm a blue outlined text over a lilac
   background with decorative stars. Change only the blue, change only the
   background, move text left/right/up/down and restore revision 1.
5. Export PNG. It should appear under private profile
   `data/studio/exports` and the UI should display its exact saved path.
6. Stop and restart the app using the **same disposable profile**. Confirm
   restored scene state and history; verify a new project is Studio OFF.
7. Open Browser and then Studio; confirm neither tab/profile is destroyed
   and manual browser ownership is not escalated.
8. Run the full test suite and separately check both Noble (Ubuntu 24.04) and
   Resolute (Ubuntu 26.04) builds; measure Qt/offscreen/WSLg performance before
   considering the branch deployable.

## Current limitations (must not be misrepresented)

- This is a manual proof-of-concept poster editor, **not yet** prompt-driven
  Studio creation: the model has no `kira.studio` agent gateway in this M1
  increment. An independent per-chat agent edit grant belongs to M2.
- The brush aesthetic is a Qt vector text outline/fill treatment, not
  freehand wet-paint physics. Additional brushes, masks, photo imports,
  inpainting, retouching, charts, research visualizers and 3D are later stages.
- There is one fixed 960x540 poster canvas and one scene per project.
  Revisions preserve editable JSON, not previous preview PNG images.
- Resource caps are bounded by document/image sizes and a 12-second renderer
  timeout. No benchmark or real process-isolation audit has been performed.
  The worker is for trusted packaged rendering, **not** safe arbitrary code.
- Export goes to Kira's private data directory; there is not yet a Save As dialog.
- Source revision and derived PNG are separate objects. Exported PNG is raster,
  while the original scene is editable. Do not claim SVG/source export exists.
- Disabling Studio stops new work and terminates its active renderer, but the
  scene and revisions remain; no background rendering when OFF.
- A schema-15 profile **cannot be reopened by an older schema-14 Kira service**.
  Roll back only using a full matching profile backup and with explicit owner
  approval. No historical data migration has been run on the live user store.

## Actual implemented files

Engine and contracts:
`src/kira/domain/studio.py`,
`src/kira/studio/paint2d.py`, `src/kira/tools/studio_worker.py`.

Service:
`src/kira/service/studio.py`,
`src/kira/service/schema15.py`,
`src/kira/service/server.py`,
`src/kira/service/storage.py`,
`src/kira/domain/contracts.py`.

UI and localization:
`src/kira/ui/studio_controller.py`,
`src/kira/ui/controller.py`,
`src/kira/ui/qml/Main.qml`,
`src/kira/ui/qml/StudioPane.qml`,
`src/kira/ui/translations/en/qml.json`.

Regression sources:
`tests/test_studio_contracts.py`,
`tests/test_studio_service.py`,
`tests/test_studio_render2d.py`,
`tests/test_studio_ui.py`
and historical migration test updates.

## Next development decision

Qualify M1 end-to-end in disposable profiles. Only after passing real rendering,
UI lifecycle, schema upgrade/rollback, and full suite should work begin on:

1. A run-owned, explicitly authorized **Kira Studio agent gateway**, with an
   independent human-controlled per-chat edit grant and separate tool allowlist.
2. Object/layer inspector, image import, reversible masks and advanced paint.
3. Discrete-event security research timeline with source provenance.
4. Small CPU-first 3D scene graph, with optional GPU path only after benchmarks.

A branch containing code does not establish a working installed release.
