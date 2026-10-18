# Desktop 0.3.3 — Kira mascot and compact project chat

## Changes

- The Kira dog illustration is the sidebar mascot and application icon, with a
  transparent background for use on the application and desktop surfaces.
- Project panels, composer, buttons and selectors use consistent rounded corners,
  subtle borders and inset spacing in the existing anthracite/blue palette.
- A compact model summary remains visible above the chat. Show opens all provider,
  model, effort, session and discovery controls; Hide collapses that entire area.
  Both states keep the same instantiated controls and selection.
- Model controls start collapsed and remember the user's choice across restarts.
  Show/Hide remains available during active work and does not alter execution.
- The layout automatically uses compact spacing below 720 pixels in height.
  Native window decorations, system buttons, moving and resizing remain under
  the window manager; no frameless-window replacement is introduced.

## State and compatibility

`model_controls_visible` is a strictly validated boolean in the existing private
GUI `ui.json`, default false. Workspace exposes `modelControlsVisible` and
`setModelControlsVisible(bool)`. It uses the same atomic 0600 preference write,
0700 directory, read-size and symlink safeguards as onboarding/density settings.
This preference does not call IPC or modify provider, model, effort, session,
project permissions, runs, database schema or global Codex configuration.
The unchanged service/schema use the existing matching-version handshake for 0.3.3.

## Artwork and installed icon

The bundled asset is `src/kira/ui/assets/kira-mascot.png`, a 1254×1254 PNG with an
alpha channel, SHA-256
`f46917623b21b61c37996a3d439b2538efe1d30cb77bb8ed7197a54c3bf04f8f`.
It was prepared with the **built-in image_gen tool**, background-extraction edit
mode, from the supplied Kira illustration. The original request and provenance
records are retained privately under the
[evidence retention policy](evidence/README.md). The hash above identifies the
bundled asset; its copy is independent of the tool's generated-image cache.

QML loads this fixed local asset with aspect-preserving display scaling; the
application sets its QIcon before creating the window. Both application packages
include the same PNG inside the Python package and at
`/usr/share/pixmaps/kira-desktop.png`. The desktop entry keeps Icon=kira-desktop.
The historical SVG remains source history and is not the new installed icon.
Custom source launchers and desktop entries must refer to the user's own checkout
and icon location; see [build and customization](BUILD_AND_CUSTOMIZATION.md).
Rendering/untrusted Markdown rules are unaffected by a bundled image.

## Verification

Actual Qt fixtures cover 880×620 and 1380×900, active and idle model controls,
keyboard/pointer toggles, recovered chat height, unchanged model/session/effort,
private persistence, invalid stored values and accessible literal labels.
Screenshots use synthetic projects and are not evidence of model execution.
Installed checks verify the PNG/alpha and QIcon, menu icon byte identity,
Show/Hide behavior, saved preference and selection preservation.

A regression in the first full application run identified that replacing the
ComboBox background also removed Qt Basic's intrinsic dimensions. A team popup
shrank to 20 pixels and its scrollbar intercepted clicks. Restoring the pinned
Basic dimensions (140×40) fixes the real hit-target problem; the existing pressed
and contrast assertions remain enforced and a new geometry regression covers it.
The scope of qualification is recorded in [STATUS.md](STATUS.md). Historical
native activation records are retained privately and do not establish deployment
on another user's installation.
