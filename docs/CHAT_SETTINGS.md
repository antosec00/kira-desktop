# Chat and desktop preferences (Desktop 0.3.6)

Desktop 0.3.9 adds the [appearance controls](APPEARANCE.md): themes, colors,
independent fonts, system-aware modes and presentation options. The legacy
chat-size/reduced-animation preferences below remain backward compatible.

## Ownership and defaults

Private GUI preferences live in config/ui.json and are validated before saving
atomically. They do not modify global Codex configuration or any provider's
credentials. Missing or invalid fields use documented defaults; existing density,
onboarding and model-control preferences remain compatible.

| Preference | Default | Values / meaning |
| --- | --- | --- |
| details_visible | false | Show the project details pane on request; persist the choice |
| resume_hint_visible | true | Show the optional workspace hint; dismissal persists and Settings can restore it |
| chat_font_size | 14 | Integer 12–20, normal chat/composer text size |
| reduce_motion | false | Suppress activity animation while retaining status text |
| confirm_close | active | active, always, never; window closure never cancels a run |
| file_opener | system | system association or VS Code, explicit local opening only |
| default_permission | read_only | Initial choice for new projects only; project_write and trusted_host require explicit selection |

Existing project permissions, selected model/effort and backend execution remain
unchanged by these preferences. Trusted host remains arbitrary execution as the
user, not a sandbox. The new-project dialog exposes the selected permission before
creation. System file opening retains existing validation; VS Code uses an
argument vector and reports an unavailable executable rather than guessing.
Web links continue to use the system browser.

## Chat presentation

Desktop 0.8.5 adds the [compact layout](CHAT_LAYOUT.md). The workspace action is
always available in the project header independently of the optional hint.
Activity, elapsed request time, details and cancellation are placed between the
transcript and composer. Consecutive tool entries share a presentation-only
expandable group, retaining their original data and individual controls.

The project details pane starts hidden when no preference exists, and is toggled
by the project header. Narrow-window access must remain usable. The transcript
scrollbar has a larger drag target and minimum thumb, with its own gutter.
Dragging/reading history pauses follow mode; Ultimi aggiornamenti restores it.

The activity indicator describes observable run status. Sto pensando… is a
conventional active/waiting-for-output label, not an exposed reasoning trace.
Tool execution, streamed response, queued/pending, waiting, cancellation and
disconnection are distinguished where available. Animation is lightweight and
stops when idle, hidden, disconnected or reduced motion is selected.

Only the exact routine Codex session configured notice is omitted from the GUI
transcript, including replay. It records the adapter's verified session/model/
permission configuration and remains in durable events and full export. Warnings,
errors and other notices stay visible. No backend event generation is removed.

## Integration boundary

IPC1, schema3 and provider protocols are unchanged. Service connection settings,
resource limits, diagnostics and backup actions remain available in the reorganized
settings page. Preference controls apply immediately; editing backend connection
settings remains an explicit save action. GUI preference tests use isolated
profiles and synthetic conversations to verify persistence and presentation.

Preview a new version with a separate empty profile. Upgrade an existing profile
only after verified idle state and a consistent backup; see
[operations](OPERATIONS.md). [STATUS](STATUS.md) records the scope of qualification.
Synthetic interaction/installed-package tests do not constitute live inference.
