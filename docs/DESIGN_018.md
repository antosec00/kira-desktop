# Desktop 0.18 — Quiet workspace

The 0.18 interface removes chrome around the conversation without removing a
single control. Every action of 0.17 remains reachable with the same object
names; what changes is where it sits, how much it says and how it looks. This
note records the layout, the new state and the verification. The service, IPC
envelopes, database schema and provider contracts are unchanged.

## Layout

### One top bar

`projectHeader` is the only bar above the conversation. The left side shows the
sidebar toggle (`sidebarToggle`), the project name as a kicker
(`projectHeaderKicker`) and the chat title (`projectHeaderTitle`). The right
side keeps **Ricerca**, **Riprendi**, **Azioni**, **Esporta**, **Dettagli** and
**Progetto** as `HeaderSecondaryButton` instances with an icon and a label, plus
the light/dark switch (`themeToggle`). Below the width threshold the buttons
drop their labels and keep icon, tooltip and accessible name; the kicker yields
before the title. The Ricerca button carries the pending research proposal
count as a badge instead of a parenthesised number.

### Sidebar modes

The sidebar has three modes stored in the private GUI preference
`sidebar_mode`: `full` (default), `rail` and `hidden`. `rail` keeps Cerca,
Progetti, Attività, Team, Conoscenza e strumenti, Modelli, Impostazioni and
Guida iniziale as icon buttons with tooltips and the connection dot; the project
and chat tree returns with the full sidebar. The toggle in the top bar and
**Ctrl+B** cycle the modes; **Impostazioni → Lavoro → Barra laterale** sets one
explicitly. Short windows (`window.compact`) tighten the sidebar spacing so the
whole navigation fits 880×620 without scrolling at the default font size;
larger fonts still scroll.

### Composer chips and model options

The strip of model controls above the chat is gone. The composer
(`composerCard`) carries a row of chips: attachment (`promptAttach`),
**File** (`projectFilesButton`), the run mode (`composerModeChoice`, a pill
`KComboBox`) and the model chip (`modelControlsToggle`, summary in
`modelControlsSummary`). The chip opens `modelPopover`, a non-modal `Popup`
anchored above the composer that hosts the unchanged `backendBox`, `modelBox`,
`effortBox`, `sessionBox`, the tools check box, provider text and refresh. The
conversation height does not change when it opens; Escape or a press outside
closes it. The right group keeps **Coda** (`projectQueueButton`), the chat tools
menu (`chatToolsButton`), **Accoda** and **Invia**. Below 700 px of chat width
the two groups stack and the queue button shows only its count.

### Tool tray

`chatToolBar` shows File, Uso, Contesto, Conoscenza, Browser, Studio, Memoria,
Anima, Team, Domande and the web settings as `ChatToolButton` icons with
tooltips and accessible names. **Etichette** (`toolLabelsToggle`) turns the
labels on for every button and persists the choice in the boolean preference
`tool_labels` (also in **Impostazioni → Lavoro**). The Domande button carries
the pending count as a badge; the Team button keeps its status text whenever a
Team is paused, delegated or active.

### Inspector tabs

Every `KiraPanel` hosted beside the conversation declares an `inspectorKey`.
The panel header shows the inspector strip (`inspectorTabs`): Ricerca, Anima,
Contesto, Memoria, Conoscenza, Uso, File and Riprendi as icon tabs
(`inspectorTab_<key>`) that switch panels in place through
`window.openInspector(key)`. Expand/side-by-side and close remain in the header
as icon buttons with their previous labels as tooltips; Escape still closes.

### Models page

Providers form a segmented row of pill buttons (`ollamaBackendButton`,
`codexBackendButton`, `directBackendButton`, `deepseekBackendButton`,
`deepseekWebBackendButton`, `claudeCodeBackendButton`). One status card
(`providerStatusCard`) shows `providerText` with a dot coloured by the new
`providerState` property — `ok`, `warning` (account to connect or no models),
`error` (catalog unavailable) or `pending` — and the only follow-up action
(**Accedi con Codex**). Models are cards in a two-column grid with the literal
identifier, the tool statement, size when reported and the declared reasoning
efforts; the selected card is outlined with the accent. The raw provider JSON
stays available under **Dati del provider** (`catalogToggle`), folded by
default. No value is invented: missing data stays missing.

### Palette and icons

The Kira preset becomes a warm graphite with an amber accent: dark
`#15181b`/`#e9ecef`/`#e0a06a`, light `#f6f5f2`/`#1f2428`/`#9a5b1d`. Derived
tokens gain `surfaceRaised`, `textSecondary` and `accentSoft`. Neutral and warm
presets, custom colours, fonts, contrast and all other appearance preferences
are unchanged. `KIcon` draws a fixed set of line icons on a `Canvas` from SVG
paths; no icon font or network resource is involved. User bubbles are
right-aligned with a bounded width; assistant turns show the mascot. The
conversation uses the bounded reading column by default (`full_width` defaults
to false); profiles that saved an explicit value keep it.

## State and compatibility

- `sidebar_mode` (`full`, `rail`, `hidden`) and `tool_labels` (boolean) are
  strictly validated GUI preferences in the existing private `ui.json`, written
  with the same atomic, size-bounded and symlink-safe path as the others.
  Workspace exposes `sidebarMode`, `setSidebarMode`, `cycleSidebarMode`,
  `toolLabels` and `setToolLabels`.
- `model_controls_visible` keeps its validation and storage. Its meaning is
  now **Apri le opzioni del modello con la chat**: when true the model popover
  opens by itself whenever a chat is shown; closing the popover never changes
  the preference. `modelControlsVisible`/`setModelControlsVisible` are unchanged.
- The appearance default `full_width` changes from true to false. Palette
  changes affect derived colours only; saved custom colours are untouched.
- None of this calls IPC, alters provider, model, effort, session, permissions,
  runs, database schema 16 or the matching-version handshake.

## Verification

Real Qt fixtures cover the single top bar at 1380×900 and 880×620, compact
header actions, sidebar modes, Ctrl+B, the popover opening without moving the
conversation, chip widths beside the browser pane, tool labels, badges,
inspector tabs, the Models page and the new palette values. Installed smoke
checks open the popover through the chip and verify the preference file. All
screenshots use synthetic projects and catalogs; they are not evidence of
provider execution. Qualification scope is recorded in [STATUS.md](STATUS.md).
