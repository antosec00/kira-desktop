# Desktop workspace and native-action contracts

These application APIs were introduced in Desktop 0.2 and remain part of the
current contract. [STATUS](STATUS.md) records qualification and remaining limits;
verification records follow the [evidence retention policy](evidence/README.md).

## Service APIs

All methods validate fields, use the private existing IPC and bounded results.
New desktop actions are explicit local-user operations, not backend tools. They
must not be added to Codex/Ollama execution vocabularies or enlarge run policies.
Native applications receive cleaned Qt/library environments and fixed argv;
request parameters never supply executables or shell fragments.

- `workspace.get {project_id}` -> `{project_id, tools, next_step, resume,
  watches, captures}`. `tools` is a list of `folder`, `terminal`, `editor`.
  `resume` contains `last_run` (id/state/updated_at and bounded outcome summary or
  null), `active_count`, `interrupted_count`. Never fabricate a next step or replay
  interrupted work. Watches contain `path`, `sha256`, `current_sha256`, `state`
  (`unchanged`, `changed`, `missing`, `unavailable`) and `checked_at`.
  Captures contain `id`, `path`, `sha256`, `created_at`.
- `workspace.update {project_id, tools?, next_step?}` -> same workspace result.
  Persist choices per project; next_step is explicitly user maintained.
- `workspace.watch {project_id, path}` -> workspace result. Relative, contained
  regular file only; bounded count and hashing size, no escaping symlinks/FIFOs.
  This explicitly creates/replaces the reference hash. `workspace.unwatch
  {project_id,path}` removes only that reference, not the file.
- `desktop.actions {project_id?, query?}` -> `{actions:[{id,label,description,
  available,reason,requires_project}]}`. IDs: `project.folder`,
  `project.terminal`, `project.editor`, `browser.open`, `browser.search`,
  `screenshot.capture`, `system.settings`, `system.monitor`, `system.updates`,
  `system.backup`. Search filters metadata; no natural-language shell execution.
- `desktop.launch {action_id,project_id?,request_id,query?}` ->
  `{request_id,action_id,status,message,artifact?}`. query is needed only for
  browser.search. Native launch status is `requested`, not proof that the window
  rendered. Other statuses: `unavailable`, `failed`, `completed` (verified capture),
  `unknown` (interrupted dispatch). Same request_id/parameters returns the stored
  result without another launch; conflicting reuse is rejected. Persist intent
  before effects and never automatically replay an uncertain dispatch.
- `workspace.launch {project_id,request_id}` -> `{request_id,results:[desktop
  launch results]}` for saved tools. Same duplicate/uncertain semantics. Native
  applications remain user-owned when Kira closes.
- `system.diagnostics {}` -> bounded whitelist report with app/schema versions,
  platform/session type, service active-run count and native action availability.
  Exclude account identifiers/tokens, environment dumps, project titles/content,
  prompts and raw logs. `diagnostics.export {}` saves that same report privately
  and returns `{path}`. UI displays the report before a user chooses export/share.

The service may use additive workspace/action/watch/capture tables with foreign
keys. Keep existing project/session/run/event columns and contents unchanged.
Read/hash failures must produce visible unavailable state, not an empty success.
Screenshots require an explicit action and a selected project; associate only a
real completed file with its hash. Unavailable native tools explain why.


## UI and verification boundaries

The workspace/resume panel, saved tools, next step, file references, action search
and diagnostics consume these service APIs. Private GUI preferences are separate
from authoritative project data. Model/effort, cancellation and reconnect
semantics are unchanged. Native availability depends on the installed host tools.

Tests must cover migration/data preservation, request idempotency, uncertain
launch recovery, file changes and missing files. A native launch reports requested;
fixtures cannot prove a visible window or a completed real screenshot. Source,
installed-package and live-provider checks are reported separately.
