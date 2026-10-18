# Kira Studio — lightweight engine architecture

**Status: proposal, no implementation.** Based on Kira Desktop 0.15.1. Preserve main, active profiles, credentials and running projects. See [README](README.md).

## Constraint-driven architecture

Build Kira-specific algorithms and scene semantics ourselves; leverage pinned PySide6 Essentials/Qt 6.11.2 for native drawing, font shaping, PNG/JPEG decoding and window/compositor integration. A self-written lightweight engine does **not** mean writing a font shaper, hardware driver, file decoder, or production-grade GPU abstraction from scratch.

Baseline dependencies added: **zero**. Do not add QtWebEngine, a second Chromium, browser JavaScript runtime, Blender, Godot, Unreal, or 3D/physics packages to baseline. Optional offline models and hardware acceleration are capability-probed, installed only by explicit user choice, and have independent limits.

Proposed ownership:

```text
Project owner => Studio Enablement (OFF by default)
  GUI / StudioPane.qml (display/intent only)
     | typed private IPC1 requests / status, bounded preview frames
  asyncio Kira Service (sole durable authority)
     | studio_store: SQLite scene metadata + content-addressed assets
     | studio_ops: schema validation, revisions, reservations, cancellation
     | studio_bridge: run-scoped model gateway; not executable code
     +---- isolated renderer (trusted packaged Kira code)
             | core geometry, 2D paths, raster tiles, animation clock
             | bounded 3D projection + scene graph (future)
             +---- screenshot/PNG/SVG verified output
  Optional: distinct, sandboxed extension worker (never default)
```

Never render raw untrusted QML, Python, Qt bindings, shell, HTML or arbitrary JS inside the privileged GUI/service. Strictly typed scene documents are **data**; the trusted renderer interprets only recognized primitives. Expression language for transforms is a bounded, parsed DSL with no IO, eval, imports, user-defined recursion or loops. Optional user scripts are a separate later security-reviewed capability.

## Integration seams confirmed in source

- `src/kira/ui/qml/Main.qml`: `SplitView` currently contains `chatColumn` then `BrowserPane` (~line 1362). Add a workspace-slot host and separate selector rather than replacing BrowserPane. Chat, details drawer, resize and expansion rules must be regression-tested.
- `src/kira/ui/app.py`: QQmlApplicationEngine initializes native image providers, controller context properties and QML; register StudioController/image provider here. Avoid dynamic untrusted QML loading.
- `src/kira/ui/controller.py` and `src/kira/ui/web_controller.py`: model StudioController after the existing GUI/service split, not after browser ownership or provider credentials.
- `src/kira/service/server.py` dispatches bounded method families. Add independent `STUDIO_METHODS`; do not overload `WEB_METHODS`.
- `src/kira/service/storage.py` owns SQLite migrations and backups. Add schema 15 **only with full migration/rollback testing**, after backups, not in the design-only branch.
- `src/kira/backends/client_tools.py` validates namespaces/limits and `src/kira/backends/kira_mcp.py` has an explicit tool allowlist. Studio gateway must update all validated provider paths together, not by bypassing caps or Codex approvals.
- `src/kira/tools/project_tool_worker.py` executes host-trusted Python and is **not** a safe Studio sandbox. Browser's isolated Chromium is unrelated to local document-code execution.
- `src/kira/ui/graphics.py` sets `QT_QUICK_BACKEND=software` under WSLg by default; do not assume GPU Qt scene graph is available. Direct GPU inference and Studio graphics have independent capability probes.

## Proposed internal modules

```text
src/kira/domain/studio.py                # typed contracts, validation, budgets
src/kira/service/studio.py               # state machine, durable operations
src/kira/service/studio_store.py         # revision storage, asset indices, leases
src/kira/service/schema15.py             # additive migration, later
src/kira/tools/studio_worker.py          # packaged renderer worker, no user code
src/kira/studio/geometry.py              # vector algebra, paths, clipping
src/kira/studio/text.py                  # text layout and stroke effects using Qt
src/kira/studio/raster.py                # QImage tiles and deterministic blending
src/kira/studio/vector.py                # editable paths/layers
src/kira/studio/scene.py                 # object tree, IDs, bounds, transforms
src/kira/studio/animation.py             # playback interpolation and snapshots
src/kira/studio/charts.py                # data/axes/ticks/interaction
src/kira/studio/simulation.py            # discrete event/state simulation
src/kira/studio/mesh3d.py                # own matrix math and primitive meshes
src/kira/studio/render3d.py              # CPU wireframe / triangle-z preview
src/kira/ui/studio_controller.py         # GUI RPC and verified frames
src/kira/ui/qml/StudioPane.qml
src/kira/ui/qml/VisualWorkspaceHost.qml
tests/test_studio_*.py
```

Module list is a proposed boundary, not a command to create all modules at once.

## Declarative scene contract, version 1

```json
{
  "format": "kira.scene/1",
  "world_id": "service-owned",
  "scene_id": "service-owned",
  "revision": 1,
  "canvas": {"width": 1280, "height": 720, "color_space": "sRGB"},
  "objects": [
    {"id": "background", "kind": "rect", "z": 0, "bounds": [0,0,1280,720],
     "fill": {"type": "solid", "rgba": [245,244,255,255]}},
    {"id": "message", "kind": "brush_text", "z": 10, "text": "Ambra mi manchi",
     "font": {"family": "bundled-known-font", "size": 72},
     "brush": {"rgba": [31,99,218,255], "width": 2.5, "seed": 177},
     "transform": [1,0,0,1,110,310]}
  ],
  "timeline": {"duration_ms": 0, "tracks": []}
}
```

The example is illustrative; implementation must use **closed, bounded schemas** with validated object kinds and fields. Never trust user-supplied world/project/revision IDs from a model; bind through the service's active run/session. Stable IDs preserve exact local edits. Imported assets use service-generated IDs and hash verification.

Supported primitive progression: `group`, `path`, `rect`, `ellipse`, `text`, `brush_text`, `image`, `clip`, `mask`, `chart`, `connector`, `camera`, `mesh`, `light`, `timeline`, `label`. Validate parent cycles, finite coordinates, transforms, dimensions, z-order, vertex count, glyph budget and input bytes. Normalized colors: sRGB with explicit alpha; gradients and blends use explicit rules. Raster work is tiled and bounded. For text accuracy, keep an editable Unicode string; convert to paths only as a reversible snapshot and show export limitations where font embedding is not possible.

## Kira-native renderer

2D: own immutable display-list construction, scene traversal, spatial bounds, hit testing, incremental dirty-region calculation, tile cache and procedural brush stamp paths. Qt QPainter/QImage provides reliable anti-aliasing, text and supported source image formats. Qt Quick only paints the trusted result. Avoid painting each stroke through Python/QML bindings on every high-frequency event: batch operations and coalesce pointer samples. Use QQuickItem texture/renderer choice only after measuring WSLg and Linux. No untrusted QML source is ever parsed.

3D: own vectors, matrices, quaternion, camera projection, mesh/triangle representation, normals, transform hierarchy, near-plane clipping, back-face culling and depth testing; CPU wireframe + shaded indexed triangles first. Qt is surface/pixel transport, not a second game engine. Avoid CPU path-tracing and massive meshes as an initial feature. Optional QRhi/native graphics integration only after native/WSLg probes show it beats CPU fallback. Protect against mesh bombs, NaNs, degenerate triangles, overdraw and out-of-bounds buffers.

World evaluation must be deterministic for fixed scene revision, seed and clock. GPU output may differ slightly by device; test with tolerances rather than claiming byte-exact color reproduction across hardware.

## Durable storage and lifecycle

Suggested schema 15 logical records: `studio_worlds` (project, state), `studio_scenes` (stable scene ID/world), `studio_revisions` (append-only revision, parent, content hash, author, run), `studio_assets` (digest/size/mime/location), `studio_asset_refs`, `studio_ops` (request_id, input fingerprint, state, receipt), `studio_grants` (human origin, project/chat scope), `studio_views` (selected scene, pan/zoom/camera), `studio_execution` (worker/lease/result). Asset bytes under private XDG data path in content-addressed files. SQLite transactions control references; temp writes + fsync + atomic rename for assets; reconcile orphaned temps after crash. Hash check on import/read/export. Never delete asset referenced by older revision without explicit compaction policy.

Opt-in enable: user-owned, project-scoped, default false; no model API toggles it. Separate chat permission for agent declarative edits. Disable removes tool registration for new runs, re-checks grants immediately before side effects, cancels workers, retains data. Reconnect reloads revision and view state without resuming runs. Service crash means interrupted; no assumed success or blind retries. GUI is not database writer or executor.

## IPC and agent API proposal

Desktop: `studio.status`, `studio.enable` (human-only), `studio.world.list/create` (human-intent UI), `studio.scene.get`, `studio.scene.apply`, `studio.preview.request`, `studio.revision.list/restore`, `studio.asset.import`, `studio.export`, `studio.operation.cancel`. Calls must carry bounded arguments, current project ownership and expected revisions. Mutations use stable request ID and exact input hash, idempotent receipts, no ambiguous retries.

Single model gateway: `kira.studio` with `action=describe|inspect|create_scene|apply_patch|render|simulate|compare|export_draft`. No tool for enabling Studio or granting scripting/network. Dispatch bound to immutable run's project, chat, provider generation, effective run mode and grant generation. Model tool spec is small; discover detailed schemas progressively. Render frames are verified and read through a separate private GUI preview channel; do not stream raw megabytes as JSON events or automatically add them to provider context.

## Security invariants

Never evaluate scene document as source code. Follow deny-by-default file/network access. Handle imported and remote content as untrusted data; no arbitrary URL images, external fonts, plugins, shader strings or file:// links. Resource limits enforced in a separate renderer process with timeouts, job registry, limits on dimensions, objects, pixels, paths, faces, assets and input text. A worker with same Unix UID is **not** a security boundary by itself; for script extensions require real OS-level isolation and independent audit or keep scripts disabled. Browser, password/passkey and direct account scopes remain separate. No full-access grant is implied by Studio enablement.

Extension capability (later): signed/hash-locked Kira-defined plug-in contracts, execution with OS isolation (namespace/seccomp/landlock according to host capability) and no secret-bearing env, network, or home mount by default; an actual containment test is mandatory. Unavailable isolation means extension unavailable, not silently trusted host.

## Benchmark gates (targets, not observed facts)

- A simple 1280x720 poster panning/brush demo targets interactive 30+ fps with bounded cache; baseline CPU and GPU metrics must be measured.
- Cap baseline preview at ~2 megapixels; use progressive tiles for larger exports.
- Standard scenes: ~1,000 vector nodes and ~10k mesh triangles **targets to verify** on low-end CPU / WSLg; degrade gracefully.
- Baseline worker memory target under 256 MiB for simple poster scene, **not a guaranteed figure**.
- No GPU required to open, inspect, edit or export basic 2D content.
- Integration suite: deterministic images tolerances, color and text regression, malformed scenes, cancellation/owner checks, migration/restore, browser unaffected, provider bridge shared allowlist, cross-project isolation, restore while Studio disabled.
