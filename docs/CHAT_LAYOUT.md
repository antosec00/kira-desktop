# Native conversation workspace

Kira keeps its mascot and appearance preferences. Since 0.18 the Kira preset
is a warm graphite with an amber accent and the workspace has one top bar; the
sidebar is whole, an icon rail or hidden (Ctrl+B) and separates project/chat
navigation from knowledge, model and settings access. A quiet selection with a
small marker identifies the current chat; project selection does not compete
with it. [DESIGN_018.md](DESIGN_018.md) records the 0.18 layout and state.

## Visual status refinement (0.13.1)

The common `StatusIndicator` uses the configured theme's accent, success, danger,
warning and muted colors. A symbol and accessible label accompany color: running
blue, confirmed completion green, explicit error red, wait/pause/unconfirmed amber,
and queued/cancelled/unknown gray. Interrupted or unsettled effects are not treated
as confirmed cancellation or success. A completed request with recorded failed or
unconfirmed tools keeps those outcomes visible without changing its durable state.

Only recorded running activity pulses, with a 1.4-second opacity cycle and no
geometry animation. Hidden/minimized windows, reduced motion and inactive chat
pages stop it. Cached tool delegates also stop outside the conversation viewport
or after their owning run ceases to be active; a stale running receipt cannot
claim current execution. The existing mascot and request elapsed time remain.
The indicator is an observed state, not a model reasoning or progress estimate.

Tool receipts place the outcome next to the operation name, wrapping on narrow
layouts and retaining the literal target, full original data and copy controls.
The currently running operation has a quiet surface tint. Recorded errors remain
red even while another operation continues. A paused Team has its own amber badge,
independent of the ordinary chat. Pending result delivery and Team-only waits do
not inherit a previous request's green outcome.

Sidebar markers cover only already-known ordinary chats in the currently loaded
project. They show activity or attention from existing scoped metadata/events,
exclude managed member sessions and preserve ordinary related-Team runs. Unknown,
unloaded or historical successful chats gain no fabricated badge. This adds no
service polling, provider call or cross-project state inference.

The existing composer instance sizes from its actual text content, within a
font/window-aware bound. Empty editing starts small; long text scrolls while
keeping selection and caret available. Drafts, attachments, mode, guidance versus
queue actions and the user's full-width preference retain their behavior.

## Consult beside the conversation

`KiraPanelHost` places one `KiraPanel` at the right of the available workspace.
Wide windows reserve its width; narrow or expanded panels overlay the workspace.
The header provides explicit expansion and close controls; Escape closes the
active panel. Clicking the chat does not close it. The composer remains the
same instance, including unsent text and attachments. The browser actor stays
instantiated: changing the visible surface does not close pages or return manual
control. Manual waits, authentication and approval behavior remain explicit.

Team details, knowledge/tools, context, research, usage, workspace recovery,
artifacts and original recorded data use the common host. Full Team and model
configuration views remain available for detailed work. Same-scope knowledge
selection and research/context/workspace drafts survive panel switches. Scope
changes discard stale callbacks and prevent another project's data from appearing
under the new selection. Workspace drafts are kept separately by project in GUI
memory; this does not promise recovery of unsaved form edits after a GUI restart.

## Operations and outcomes

A narrow visual trace accompanies compact tool receipts: operation, available
literal target, recorded state and bounded error preview. Opening a receipt
reveals the existing original output, copy and data controls. Full-output and
group expansion remain separate. The visual trace is neither a model reasoning
trace nor a predicted progress estimate. Unknown tools retain their literal names.
There is no inference from arbitrary command text and no new provider request.

The activity row continues to belong to the selected request. A successfully
terminated request with failed or unconfirmed tools says that the request ended
and reports those tool outcomes separately. Successful completion of a turn is
not converted into success of every tool or of the Team. Counts survive the
bounded transcript window while replay and context changes reset scope correctly.

A saved objective appears as a compact anchor only when an explicit saved record
is available. Reading it uses one scoped metadata request, not an inference turn,
and never reserves the composer or sends the goal back to the model. Opening the
anchor exposes the existing explicit goal controls. A new user choice takes
precedence over an older in-flight read.

## Settings and accessibility

Settings are grouped into Appearance, Work, Connections and Local data. Category
switching keeps controls instantiated and retains uncommitted input. The appearance
preview sits with the theme controls. Team forms group objective, composition,
model and limits; assignment rows retain both recorded member identity and a
readable name. No model, account, effort, permission or theme is changed by the
visual update.

UI labels remain translated; user/provider/tool content is literal. Keyboard
focus, copy, enlarged fonts, minimum window size and reduced motion remain part
of qualification. No background polling or animation is introduced by the panels.

## Earlier compact-chat behavior retained

The GUI gives the conversation more space while preserving the configured theme,
fonts, provider, model, reasoning effort and execution permissions. This is a
presentation change; database schema 8 and IPC 1 remain unchanged.

## Workspace and model controls

The header keeps **Riprendi** available independently of the introductory hint.
It opens the existing workspace dialog with recorded outcomes, the saved next
step and explicitly configured tools. The optional **Il prossimo passo** hint
has a dismiss action. Its private, strictly boolean `resume_hint_visible`
preference defaults to true, persists after dismissal and can be restored in
Settings. Hiding the hint never erases the saved next step or changes project
configuration.

Since 0.18 the header actions (Ricerca, Riprendi, Azioni, Esporta, Dettagli,
Progetto) are icon buttons that show their labels on wide windows and keep
tooltips and accessible names otherwise. Provider/model controls live behind
the composer's model chip: the chip shows the selected identifiers literally
and opens a popover with the same backend, model, effort, session and tool
controls without moving the conversation. Team and question actions remain in
the tool tray; pending questions and errors retain visible attention states.
Interface text follows the selected language without translating model
identifiers or user content.

## Activity and composer

A dedicated row between the transcript and composer contains Kira's activity,
elapsed request time, details and cancellation control. Activity comes from
observed service/provider events. The thinking label is a conventional waiting
state, not a reasoning trace. The elapsed value measures time from the recorded
request creation timestamp; it is not a completion estimate. Missing or invalid
timestamps remain unavailable. A local GUI timer pauses when the window is
hidden, minimized or no active timestamp is available. Reduced motion disables
animation while retaining state and timing information.

Details refer to the selected chat's recorded run. Cancellation remains an
explicit service request; requesting it is not evidence of completion. The
persistent [run error panel](RUN_STATUS.md) continues to expose failed and
interrupted outcomes above the composer.

The composer retains attachments, run modes, queue and steering controls as
chips; the two chip groups stack when the chat column is narrower than 700 px.
File, usage, context, knowledge, browser, Studio, project-memory, Anima, Team,
questions and web-settings entry points form an icon tray under the composer;
**Etichette** restores their labels and persists the choice. They remain
accessible with the browser beside the chat. Permission information remains
visible.

## Tool activity

Consecutive tools from one run share an expandable activity summary. Grouping
does not cross assistant/user messages, notices, team entries or run identities.
It is derived only in the transcript model: original row identities, order,
recorded bodies and raw event details remain intact. Copy and export retain
their existing complete-content semantics and bounds.

Group expansion and individual output expansion are separate presentation
choices. Error or unconfirmed tool outcomes remain identifiable. An expanded
group exposes the existing tool controls and [bounded previews](TOOL_OUTPUT.md).
Activity/evidence views retain individual recorded entries.

## Qualification boundary

Synthetic service/GUI tests and installed-package checks verify presentation and
interaction; they are not live provider evidence. Current results and remaining
limitations are recorded in [STATUS](STATUS.md). Existing-profile activation
requires verified idle state and a consistent private backup as described in
[Operations](OPERATIONS.md).

## Research integration (Desktop 0.9.0)

Desktop 0.9.0 retains the compact chat layout and adds the separate Research
entry point. At narrow widths or larger fonts, secondary header actions use
compact icons with translated accessible labels and tooltips; Research and
Resume keep their text. The header keeps its normal height. Empty-chat guidance
stays clipped to a scrollable transcript area. These presentation adaptations
do not change registry persistence or the existing execution behavior.
