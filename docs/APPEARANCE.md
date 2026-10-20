# Appearance preferences (Desktop 0.3.9)

Appearance is GUI-owned, saved atomically in the existing private `config/ui.json`.
It never changes provider settings, model/effort, project permissions, messages or
database schema. Changes apply immediately, including while a run is active.
Existing profiles keep their chat size and reduced-animation preference. The
initial palette is Kira dark; since 0.18 the Kira preset is a warm graphite with
an amber accent and the conversation uses a bounded reading column unless full
width is enabled ([DESIGN_018.md](DESIGN_018.md)).

## Controls and storage

The strict `appearance` object stores mode (`system`, `light`, `dark`), independent
font sizes, contrast and presentation options, plus `shared`, `light` and `dark`
style profiles. Each style contains a preset, optional background/foreground/accent
overrides, and font families/styles for interface, conversation and code.

- Presets: Kira, neutral and warm, each with light and dark palettes.
- Custom colors: six-digit RGB hex or an empty override to use the preset.
- Fonts: installed local families; system font, interface inheritance and system
  monospace are explicit choices. No font download occurs.
- Sizes: interface 12–18 px, conversation 12–20 px, code 10–20 px. Conversation size
  remains compatible with the existing `chat_font_size` preference.
- Separate styles: edit the currently displayed light or dark profile. Enabling
  separation copies the shared style; disabling it keeps the visible style as the
  shared style. System mode follows Qt's reported color scheme.
- Reduced motion: system, on or off. System mode reads available desktop-portal
  animation preferences asynchronously. If none are exposed, animations remain
  enabled and the settings page explains how to choose an explicit override.
- Sidebar translucency blends with the application background; it does not enable
  compositor blur or transparency to other windows.
- Contrast adjusts secondary text and borders. Custom foreground/background pairs
  remain the user's choice; the preview helps inspect their readability.
- Full width uses the available chat width; disabling it centers a bounded reading
  column. Since 0.18 the default is the reading column; profiles that saved an
  explicit value keep it. Pointer mode changes cursors on interactive controls.

Normal, medium, semibold, bold and italic styles are selectable independently for
the three font roles. Fonts unavailable on another machine use Qt's fallback;
exporting a theme does not bundle or install a font.

## Derived text presentation

Smart punctuation is opt-in and applies only to prose in formatted assistant
Markdown. It never edits the composer, stored message, request sent to a provider,
code spans/blocks or original exports. Selecting displayed prose and pressing
Ctrl+C copies the displayed punctuation; the message's Copy action and source
view retain the original Markdown.

Fenced `diff` and `patch` blocks can show added/removed lines in semantic colors
or monochrome. Both modes preserve every original `+` and `-` character. Tool
output remains literal. Code family, size and style apply to fenced/inline code
and literal code/output views. HTML remains escaped, links require explicit
activation, and rendering performs no resource fetches.

The renderer's bounded cache includes appearance in its key. Theme/font changes
therefore refresh existing messages without changing durable events or requiring
another provider turn. [Markdown limits](MARKDOWN_OUTPUT.md) continue to apply.

## Theme exchange and recovery

Copy/export produces a versioned JSON object containing appearance only. Import
accepts format `kira-desktop-appearance`, version 1, with bounded input, unique
keys and strictly validated values. It cannot import paths, credentials, provider
configuration or executable markup. Invalid input and failed writes preserve the
previous settings and show an error. Reset changes appearance only.

Global Codex configuration, desktop theme settings and other applications are
untouched. [Operations](OPERATIONS.md) describes profile storage and safe updates;
[STATUS](STATUS.md) records actual qualification separately from live inference.
