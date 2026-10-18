# Kira Studio — creative, editing, visualization and interaction matrix

**Proposal only.** The Studio must let Kira assemble precise, editable artifacts rather than simply return flattened image files. The small core supports reproducible graphics; photorealistic semantic generation requires explicit optional inference and is not promised by geometry code.

## 1. Canvas, illustration and typography

- Infinite-feeling workspace with bounded pages/artboards, pan/zoom, snap, rulers, guides, grid, coordinates, layers/groups, z-order, locks, hide/show, selection, marquee, align/distribute and multi-select.
- Native tools: pencil, pressure-emulated brush, eraser, Bezier pen, straight/polyline, freehand, shapes, text on path, calligraphy, arrow and connector, crop, transform handles and magnifier.
- Brush engine authored by Kira: deterministic stroke sampling, smoothing, cubic fitting, spacing, jitter with seeded RNG, rotation, flow, caps/joins, wet-look compositing approximation, opacity, pressure profiles from mouse speed or optional tablet pen data. Never call deterministic effects a true fluid paint model.
- Exact Unicode text, font fallback, kerning via Qt, paragraph metrics, text style spans, letter spacing, line heights; decorative stroke layer retains original text for editing, accessible labels and search.
- Fills: solid, linear/radial gradient, image patterns, procedural backgrounds (noise, bands, particles, fractals with finite limits), palette, alpha mask, stroke joins, compositing modes; custom effect graph grows only as benchmarks allow.
- Backgrounds: scene-based gradient/scenery/icon/shape compositions offline, imported photographs with editing, optional model-generated photographs with separate consent/provenance.
- Material and style presets are *recipes*, not separate executable code; model may combine recipes freely.

## 2. Pixel-level image editor

Non-destructive raster editing graph with source image immutable, separate operations and named regions. Import PNG/JPEG/WebP via existing Qt decoders after size/orientation/color validation; preserve a frozen original by digest. Editing includes:

- Rect/ellipse/lasso/polygon/path and color-range selections; add/subtract/intersect/invert; feathered masks, alpha masks, mask painting and reusable selections.
- Crop, rotate, flip, resize, perspective/affine transform (with explicit quality), straighten, per-channel operations, color temperature/tint, exposure, levels/curves, saturation/HSL, white balance, shadows/highlights approximation, grayscale and posterize.
- Convolution kernels, edge detection, sharpen, Gaussian/box blur, denoise baseline, pixelate, morphology, fill bucket, clone/heal with documented simple neighborhood algorithms, local dodge/burn, gradient maps and transparency cleanup.
- Layer blend modes, editable text/vector overlays, adjustable filter strengths, live region previews and history/undo; deterministic local edits can target selected objects or pixel masks without changing unrelated object hashes.
- Import/export safeguards: embedded EXIF location/metadata stripped in standard export; optional metadata preservation is separate; color management limitations shown. Large images via tiles and cache eviction.
- Automated object removal, text-aware inpainting, neural upscaling and semantic masks are **optional ML plugins**, not falsely presented as functions supplied by the small renderer. A model can guide manual masks and local deterministic texture-fill but cannot guarantee object removal quality.

## 3. Charts, dashboards and visual teaching

Own library of line, bars, histogram, scatter, pie/donut, time series, area, heatmap, box-plot, stacked and error-band charts, distributions, network graphs, Sankey-like flows and geographical schematics. Data schemas retain provenance, units, sampling assumptions, uncertainty and nulls. Interactive parameters, tooltips, cross-filters, brushing, zoom, compare, highlight, hover-to-inspect and computed aggregations.

Storyboards: page sequences with backgrounds, captions, drawings, callouts, arrows, code/diagram cards, layered animations, presenter timer, text-to-speech only with explicit optional source. Export PNG/SVG/JSON/PDF later, with truthful editability. Animations via sprites, interpolated transforms, easing curves, path following, particle systems (bounded count), simple 2D kinematic motion and exported frames. External FFmpeg only opt-in for video encode later, not a required runtime.

## 4. Scientific and engineering simulation

A compact *discrete event + numerical* toolkit, not a universal high-fidelity physics engine.

- Finite state machines (guards, events, transitions, assertions), schedulers/queues, timers, interrupts, cooperative actors and deterministic seeded random processes.
- 2D: collisions of simple shapes, particles, basic gravity, projectile/pendulum demonstrations, springs, wave discretizations on small grids, bounded cellular automata; clearly label discretization and timestep limitations.
- 3D: camera navigation, axes, transforms, materials approximating diffuse lighting, primitive geometry (boxes, spheres, cylinders, lines, indexed polygon meshes), object picking, annotated scene graphs, path/camera animation, schematic procedural extrusion, simple orbital or state-driven animations.
- Scientific controls: sliders, breakpoints, variable inspection, compare scenarios, timeline scrub, probes, snapshots, repeat with same seed, units, error bounds where validated, avoid claiming physical accuracy on illustrative algorithms.
- No real rigid-body professional solver, CFD, electromagnetic simulator, volumetric photorealistic rendering or CAD precision in initial engine. Optional integrations are separate measured projects.

## 5. Interactive microapplications made by Kira

A declarative UI component set: labels, buttons, slider, toggle, dropdown, numeric/text input, charts, editable canvas, selection, event triggers, tables, inspected values and keyboard shortcuts. Inputs bind only to whitelisted declarative state transitions. Components can be versioned, recomposed and saved in worlds. A model may generate *new combinations* of these primitives at any time; it may propose a new trusted primitive through code review/testing later.

State graph semantics: typed state, bounded expressions, no dynamic imports, no general script evaluation. For advanced plugins, separate extension runner subject to narrow human permissions and verified OS sandbox. Never use raw generated QML or JS in the desktop privileged context.

Interaction example: click timeline node -> highlighted API request -> read linked (authorized, user-provided) source record -> display observed vs hypothesized properties -> ask Kira to simulate alternative. Such selection should give Kira a typed, bounded object reference, never arbitrary prompt instructions from untrusted visual text.

## 6. World organization and operations

World selector; scene tree; inspector; timeline; assets; styles/templates; favorites; layers; relation graph; minimap; search; compare/fork; history; duplicate; pin; commentary; accessibility tree; keyboard editing; touch/pen where supported; import/export templates. Multi-document workspace can remember independent camera/viewport state. A project can keep as many scenes as storage budgets permit, with quotas, explicit garbage collection and export portability.

Multi-agent: Teams can propose visual changes to independent scene branches (no concurrent edits to shared revision); a coordinator compares patches and commits a merged version after validation. Do not equate concurrent proposals to conflict-free merging. Separate scene mutation authority from agent chat/Team delegation authority.

## 7. Acceptance examples

**Poster:** `"Scrivi Ambra mi manchi con un pennello blu su sfondo rosa chiaro, decorato con tre stelle"` creates a background, editable text/brush object and three vector stars. `"Cambia solo il pennello in blu notte"` modifies one object and retains background/others; text is exact and selectable; export produces output while source revision remains editable.

**Photo:** import owned sample portrait; brighten only masked foreground, blur background and add 2px white outline; originals immutable and re-export repeatable. Do not infer identities from pictures.

**Data:** JSON with timestamps and observed values; Kira charts labels/units and interpolates only if explicitly chosen. Filter alters view, not raw evidence. Clicking a point reveals its source.

**Storyboard:** 5 scenes, each with editable caption/illustration, event-based transition and replayable animation; pause, modify, export frames.

**3D:** primitive-based scene with 25 labeled cubes (services), links, navigation and a running simulated event queue; default CPU fallback handles modest complexity, GPU optional later.
