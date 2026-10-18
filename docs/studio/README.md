# Kira Studio — design dossier (proposal, not implemented)

**Status:** M1 and a guarded provider gateway are committed as experimental source, **not yet qualified or installed**. Advanced M2 image editing and M3–M5 remain proposals. **Baseline:** Kira Desktop 0.15.1, GitHub private branch `feature/kira-studio`, forked from `main` on 2026-10-18.
**Language convention:** engineering specifications in English; user-facing labels and future guide in Italian.
**Safety:** no changes to the installed application, service database, package, provider credentials, or existing branches are part of this dossier.

## M1 experimental source

The first opt-in poster prototype and its tests are recorded in
[M1_IMPLEMENTED.md](M1_IMPLEMENTED.md). The separate provider gateway is
specified in [M2_PROVIDER_BRIDGE.md](M2_PROVIDER_BRIDGE.md).
Advanced image editing, research visualizers and 3D remain future work.

## Vision

Kira Studio is an **opt-in, persistent visual world** hosted in the right-hand workspace of Kira Desktop. When enabled by the human owner, a model may author, inspect, revise, simulate, explain, and export **structured, executable-by-Kira scenes**. A world is not a chat reply or a disposable screenshot: it is a versioned collection of canvases, layers, scene objects, relationships, timelines, user interactions, input data, and evidence.

The shared product principle is **build our own small engine and algorithms for Kira-specific needs**, using the **Qt/PySide6 runtime already bundled** for presentation and hardware abstraction. Avoid adding a heavyweight game engine or embedding another browser for the baseline. Never equate inventing a scene format with inventing a new GPU API.

The user controls enablement and operations; **the agent cannot arm the feature itself**. Studio is disabled by default, and the main chat/browser/Team flows must work unchanged.

## Design navigation

1. [ENGINE_ARCHITECTURE.md](ENGINE_ARCHITECTURE.md) — the lightweight 2D/3D engine, scene schema, storage, IPC, renderer, security, provider integration.
2. [CREATIVE_AND_INTERACTION.md](CREATIVE_AND_INTERACTION.md) — comprehensive creation/editing/animation/simulation/interactivity capability map.
3. [SECURITY_RESEARCH_LAB.md](SECURITY_RESEARCH_LAB.md) — authorized vulnerability research, protocol/race visualizers, evidence, reproducible tests.
4. [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) — **B: concrete repository implementation plan**, staged deliveries, affected files, tests, acceptance gates.
5. [DECISIONS_AND_OPEN_QUESTIONS.md](DECISIONS_AND_OPEN_QUESTIONS.md) — platform trade-offs, resource limits, realistic constraints and validation experiments.

Do not treat proposals in these files as shipped features; `docs/STATUS.md` is the authoritative implemented qualification record.

## User experience contract

- Left: projects, chats, Teams, and existing tools unchanged. Center: conversation unchanged.
- Right workspace: **Browser | Studio**, with one visual workspace shown at a time; switching tabs does not delete either state. Browser permissions, manual login and its isolated profile stay separate.
- **Studio OFF by default per project.** Explicit UI toggle in project settings/Studio launcher; never auto-enable from a chat message, tool response, provider instruction, branch checkout, or project import.
- Project worlds and revisions survive closing the pane/window and restarting Kira **while disabled**; disabled means no Studio workers, agent Studio tools, scripted activity, or outbound Studio network traffic.
- **Enabling does not imply permission to execute arbitrary code.** Three independent controls: (1) project Studio enabled; (2) this chat may request Studio declarative edits; (3) script execution / external generation only with separate, narrower human approval. Disabling revokes new calls immediately and stops or safely cancels active operations without corrupting a revision. Restart does not auto-resume interrupted execution.
- Model-generated scene changes are shown as transparent activity with revisions, author/run, asset provenance, and undo. Manual edits are equally first-class. A scene can be forked, compared, restored, exported, or shared only by an explicit user action.
- Start at a friendly blank canvas with templates. No requirement for the user to learn DSL, CAD, 3D modeling or Python.

## High-value acceptance demos

| Scenario | Required behavior | Critical limitation |
| --- | --- | --- |
| "Con un pennello blu scrivi Ambra mi manchi, su uno sfondo delicato" | Exact spelled text; a blue brush-like editable stroke layer and a background; resize, recolor, undo, revise, export PNG/SVG where possible; persists on reopen | Decorative text is **deterministic vector/brush output**. Arbitrary photorealistic background generation requires an explicitly chosen model/service, not the tiny graphics core |
| "Cambia solo il blu e la posizione delle lettere" | Stable object IDs, edit selected layers only, unchanged asset hashes elsewhere | No destructive flatten/re-render as the default |
| "Crea una dashboard JSON" | Data import after user consent, local parse, custom charts, filters, labels, cross-highlight; export | Values must retain source and units; no synthetic statistics disguised as observations |
| "Mostrami una race revoke/sign/commit" | Interactive timeline, variable ordering, assumed/observed event labels, replay, outcome comparisons | A simulation is not proof of server timing or production exploitability |
| "Rappresenta questa architettura in 3D" | Navigable nodes, camera, transforms, selection, annotations and revisions | Begin with small schematic meshes; photorealism and AAA game performance are not baseline promises |
| "Ingrandisci la foto e correggi solo la parte selezionata" | Non-destructive imports, masks, crop/transform, tonal/color corrections and brush edits; export | Automatic semantic inpainting/segmentation needs separate ML inference, user masks or a provider |

## Capability coverage and release stages

| Area | Custom/lightweight direction | MVP (M0–M2) | Research and advanced |
| --- | --- | --- | --- |
| Drawing | Qt-backed 2D vector paths, pressure-emulated brush, text, layers | Editable poster / diagrams | Filters, selections, advanced masks |
| Images | QImage raster pipeline, tile caches, reversible edit graph | Import, compose, crop, export | Retouch, region operations, optional ML |
| Visualization | Own chart/graph/timeline primitives and labeling | Graphs and event timelines | Large graph LOD, linked dashboards |
| 2D motion | Keyframes/interpolation, deterministic clock | Playback / pause / snapshots | Storyboarding, path motion, physics-lite |
| 3D | Own scene graph, linear algebra, primitive meshes, camera, CPU preview | Later M4 | GPU backend only after measured need |
| Simulation | Finite-state/discrete-event engine | M3 | Constraint solvers, calibrated domains |
| Research | Owned evidence + visual protocol/trust/state views | M2–M3 | Branching experiments, source-aware PoC narratives |
| Extensibility | Signed/hashed, reviewed scene components with versioned contracts | Declarative primitives | Isolated program extensions |
| Collaboration | Explicit export, import and local scene ownership | Local worlds | Team handoffs only after scope review |

## Definition of "persistent world"

A **world** is a named project-owned virtual space with one or more **scenes**. A scene is immutable at a given revision; updates create a new revision. Each object has stable ID, type, geometry, style, transforms, relationships and optional bound input. Scenes may contain multiple canvases, cameras and animation timelines. Asset bytes are stored once by content hash and referenced, not embedded repeatedly. A world remembers view state, selected items and provenance independently of chat compaction. Closing the pane never means delete.

Cross-project sharing is explicit export/import with new ownership and provenance. A provider cannot invent or select another project ID. Personal model-generated files and uploaded research evidence stay outside the public source repository.

## Success criteria

1. An ordinary Kira session with Studio OFF has unchanged runtime behavior and no Studio worker/process.
2. The exact-phrase poster demo is possible with deterministic, editable layers and survives restart.
3. Scene update and restore cannot silently overwrite a concurrent manual edit (expected revision + stable request ID).
4. Agent tools operate only on a user-enabled, run-bound, project-owned world, and never change browser policy.
5. A visual race demo reliably distinguishes hypotheses, source evidence, and measured results.
6. Resource ceilings, cancellation, crash recovery, offline operation and headless render regression tests pass on the supported Ubuntu builds; WSLg software graphics have explicit acceptance testing.
7. No extra generative model download, Qt WebEngine, Blender, Three.js, Godot, Unreal or FFmpeg is required for the core baseline.

**Important:** this dossier is a blueprint, not evidence that any implementation, benchmark, containment or provider integration has already passed.
