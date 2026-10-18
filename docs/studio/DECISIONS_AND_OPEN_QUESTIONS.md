# Kira Studio — decisions, alternatives, investigations

**Status:** proposed decisions, pending implementation/benchmark review.

## Primary decisions

| Concern | Preferred choice | Reason and fallback |
| --- | --- | --- |
| Heavyweight game engine | **None in baseline** | Native Kira scene DSL, Qt already packaged; add targeted libraries only if quantified wins |
| Right-hand UI | **Browser / Studio tabs in same split slot** | Reuse Main.qml layout without sacrificing independent browser state |
| Studio activation | **Explicit per-project OFF by default** | Stored worlds persist even when Studio disabled; no model enable tool |
| Renderer 2D | **Our algorithms + Qt QPainter/QImage** | Fonts, antialias, image codecs proven toolkit; own editable scene, brush and effects |
| Renderer 3D | **Own small CPU raster/scene graph initially** | Lightweight and software-compatible, but limit triangles/resolution and benchmark |
| Image fidelity | **Deterministic non-destructive editor baseline** | Model generation/inpainting/upscale optional; no unfounded quality promises |
| Runtime language | **Declarative scene with closed typed schema** | Prevent generated QML/Python/JS from executing in privileged process |
| Persistent data | **SQLite service authority + content hash assets** | Atomic revisions, provenance, backup, rollback |
| Scripts/plugins | **Not enabled in baseline** | Need real OS containment; same-UID process not a sandbox |
| Network | **OFF within Studio by default** | Explicit imports; browser and research host permissions remain separate |
| Extension implementation | **Kira-owned algorithms and narrow components** | Avoid vendor lock-in; don't rewrite trusted system primitives without reason |
| License | **Retain repository MIT terms; audit additions** | No copied snippets/assets or unreviewed redistributed fonts/licenses |

## Realistic technical risks

1. **Qt Quick software mode on WSLg.** The current application explicitly selects a software render path by default; GPU acceleration claims are premature. Benchmark QPainter offscreen, QQuickPaintedItem (may cause expensive texture updates) and a native frame provider on both WSLg and Linux.
2. **3D in Python.** A pure-Python z-buffer triangle rasterizer can be slow at high resolution or mesh count. Keep CPU software scenes deliberately small; consider optional compiled Kira-owned extension only after profiling, with reproducible cross-ABI package builds.
3. **Vector vs pixel artwork.** Editable text/paths and destructive retouching differ; use non-destructive layers to reconcile them. Font licensing and glyph shaping need Qt and font installation policy.
4. **Generative content.** Procedural gradients/textures/graphs are feasible with code. Arbitrary photorealism, perfect object removal, semantic masks and 3D photogrammetry require trained models or external assets, compute, and permission. Never imply self-written brush code achieves them.
5. **Many operations.** Agent-generated scene patches need bounded transactions, stable object IDs, idempotency and conflict handling; the existing app has exactly these patterns in other subsystems, but Studio itself is not built.
6. **Security containment.** A renderer parsing untrusted data may have bugs; budget caps and separate process reduce failures but do not provide sandbox isolation alone. Custom program execution should remain disabled without proven kernel controls.
7. **Cross-version backups.** Adding DB schema 15 makes older installed clients reject it. No active profile migration until explicitly scheduled with backed-up idle state; separate `KIRA_HOME` test.
8. **Browser mutual presentation.** Switching tabs should not implicitly close Chromium/terminate jobs, duplicate visual workers, grant Studio permissions to browser or interfere with manual login ownership.
9. **Advanced 3D.** CPU fallback alone will not compete with modern dedicated engines in high-polygon scenes. Optimize for Kira's diagrams, low-poly simulations and research clarity rather than photorealistic games.
10. **Large documents.** Memory and storage quotas, tile LRU, import size, revisions cap, purge and backups must be explicit and user observable.

## Experiments before code architecture freeze

- Verify baseline package binaries/installed PySide6 modules on Noble and Resolute; no assumption that optional Qt modules are present.
- Draw exact brush-text scene at 1280x720 with pure Python scene graph + QPainter. Measure average and worst latency, RSS, CPU and text shaping behavior.
- Prototype 256x256 tile dirty rectangles vs whole-canvas repaint with layered text and images; compare 10/100/1000 objects.
- Render CPU low-poly 3D 100/1,000/10,000 triangles at 640x360 and 1280x720; measure input latency and downsampling fallback.
- Evaluate devicePixelRatio scaling, color sRGB conversion, font fallbacks, save SVG editable text vs raster output.
- Force crash mid-asset commit and concurrent patch conflict; demonstrate atomic rollback and no leaked temporary files.
- Attempt import of malicious SVG/JSON/resource references and excessive meshes, recursive groups, infinite float values, huge masks; reject deterministically.
- Exercise opening Browser then Studio, manual browser takeover, tiny widths, chat switches, service restart and Studio disabled state.
- Verify provider-tool catalog limits on OpenAI direct, Codex MCP and Ollama independently with fixture-only test accounts.
- Prove plugin isolation using real host test before enabling any script, not only by unit-test mocking.

## Preliminary measurable targets, not observations

- Startup overhead while Studio OFF: **zero workers** and negligible UI side effects.
- Simple 2D composition: target 30 fps interactive panning at 1280x720 on modest CPU; thumbnail generation with bounded time and memory.
- Typical editing: target 200 ms response for a single editable object property change in a small scene; asynchronous previews as needed.
- Image import: preserve a verified immutable source; tile large images, no full-copy per brush stamp.
- 3D: start at low-poly 640x360 and only raise resolution/mesh budget based on profiling; camera orbit should not block Qt event loop.
- Default network bytes generated by Studio: zero.
- Reproducibility: fixed seed and scene revision match raster within defined platform tolerances; structural scene JSON hash must be exact.

## Questions reserved for staged product decisions

1. Is the enablement project-only, or project plus a per-chat creative edit grant? Recommendation: both.
2. Is the user-facing default Studio start a blank canvas or template library? Recommendation: blank canvas with two visible templates.
3. How large may local private world files become? Recommend a configurable quota with no silent deletion.
4. Which exports must remain editable? Recommend versioned Kira JSON bundle first, then SVG and PNG; PDF/video later.
5. Does the user prefer a specific GPU optional route in future? Benchmark both native Linux and WSLg first.
6. Should a world support multiple simultaneously displayed canvases? Design storage for many, ship one active viewport.
7. When may Team agents apply shared edits? Start with independently proposed patches, review/merge under service ownership.
8. How should imported private source code and images be linked from Studio? Opt-in explicit per-file grants, source hashes and redaction.
9. Can users opt in to external image generation? Implement only as an independently approved future provider capability.
10. What constitutes a \"valid\" vulnerability simulation? Only actual source/trace-backed evidence plus negative controls; visualization is never independent proof.

## Immediate next gate

Approve **M1 design and security review** before coding. Keep the first patch limited to a no-op Studio enablement shell and standalone offline poster renderer, with no modification to existing browser internals, provider credentials or live profile. After tests and measured performance, scope subsequent editing and research features.
