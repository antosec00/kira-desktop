# Project registry and evidence

Released in Desktop `0.9.0`, schema 9.
This first increment adds inspectable project records and evidence. It does not
implement autonomous experiment execution, formal verification, reusable
procedures or adaptive scheduling. Those remain in [the roadmap](PROJECT_RESEARCH.md).

## Records and assessments

Records belong to one project. Kinds are `objective`, `hypothesis`, `experiment`,
`observation`, `decision`, and `issue`. A record has an opaque `id`, `project_id`,
integer `revision`, `kind`, `title`, `body`, `scope`, `criteria`, `procedure`,
`limitations`, `status` (`open` or `closed`), `assessment`, `accepted`, `sources`,
`dependencies` and `links`. It retains server-authored origin (`user` or
`assistant`, optional originating run and its immutable provider binding),
creation/update times and the latest dependency check. Origin never changes when
a user accepts or edits an assistant suggestion; revisions record the editor.

Assessments are `untested`, `supported`, `contradicted`, `inconclusive`, or
`needs_recheck`. There is no generic `verified` state. Assessments are explicitly
attributed human judgments, separate from observed execution outcomes. Supported
or contradicted requires scope, criteria and at least one owned source. A source
can itself be an assistant claim; its category and provenance stay visible. A
successful run or tool result never changes assessment automatically.

Title is bounded to 200 characters; body to 8,000; scope, criteria and limitations
to 2,000 each; procedure to 4,000. A record permits up to eight sources, eight
declared file dependencies and eight record links, with a 48 KiB encoded bound.
Invalid or oversized input is rejected, never silently truncated on save.

Sources use closed descriptors: `{kind:"run",run_id}`, `{kind:"event",run_id,seq}`,
or `{kind:"artifact",artifact_id}`. Ownership resolves through canonical database
relations. A project owner may explicitly link ordinary or team executions in
that project. Another project's sources are rejected. Clients cannot supply
provenance, artifact paths, observations or execution status as trusted metadata.
Only durable public events can be sources; private native context, transient
browser frames, manual input and credentials are excluded.

Dependencies are `{path,sha256}`, with project-relative paths and SHA-256 values
captured by the service. Links are `{record_id,revision,relation}` with an owned,
existing revision and relation `supports`, `contradicts`, `supersedes` or
`relates_to`. Self-links are rejected. Links to older revisions remain readable
but make their dependency on an older interpretation explicit. A correction
normally revises the same stable record with a reason; it never erases history.

## Persistence and mutations

Schema 9 adds current records, immutable revision snapshots, operation receipts
and project sharing settings. Migration is additive after a consistent backup.
Writes use `request_id` plus an exact input fingerprint. A repeated identity with
identical input returns its receipt and the current record; different input
conflicts. Edits require `expected_revision`. The mutation, revision and receipt
commit atomically, without awaiting inside the transaction.

User-created records are accepted by default. Assistant proposals always begin
unaccepted and untested, and cannot accept themselves or alter existing records.
Only an explicit user save can accept or close a proposal. Metadata operations
do not authorize or execute project commands and do not require a writable
project. Existing project permissions continue to govern actual work.

Changing assessment-relevant content invalidates an earlier assessment unless
the user explicitly requests `reassess:true` with a reason. Ordinary editor saves
must not silently carry a supported assessment across changed criteria or data.
Dependency rechecking hashes only declared files, with bounded no-symlink reads
off the service thread, followed by a revision check before committing. Missing,
changed or unreadable dependencies result in `needs_recheck`. Matching hashes
never restore an earlier positive assessment automatically. This increment has
explicit rechecking, not a continuous whole-project watcher.

## IPC shapes

All methods require `project_id`. IDs, enums, pagination and unknown fields are
validated. List responses are bounded; omitted display text is labeled and can
be read through detail APIs. The complete record is retained independently.

- `research.list {project_id,cursor?,limit?,view?,query?}` returns
  `{project_id,records,settings,has_more,next_cursor}`. Views are `all`,
  `knowledge` (objectives/hypotheses/decisions), `evidence`
  (experiments/observations), `next` (open issues/experiments/objectives), and
  `proposals` (unaccepted records). Limit defaults to 20, maximum 30.
- `research.get {project_id,record_id,revision?}` returns `{project_id,record}`.
- `research.history {project_id,record_id,before_revision?,limit?}` returns
  `{project_id,revisions,has_more,next_cursor}` with newest revisions first.
- `research.save {project_id,request_id,record_id?,expected_revision?,reason?,
  reassess?,record}` creates or revises a record and returns
  `{project_id,record,operation}`. `record` contains only editable fields listed
  above; origin, identities and timestamps are service-owned. Existing records
  require a nonempty reason. `reassess` is user-only and defaults false.
- `research.configure {project_id,request_id,expected_revision,enabled}` returns
  `{project_id,settings,operation}`. Settings are `{enabled,revision}`; an unset
  project defaults to disabled with revision zero.
- `research.sources {project_id,run_id?,cursor?,limit?}` returns
  `{project_id,items,has_more,next_cursor}`. Without a run it lists project runs;
  with one it lists that run's eligible events and artifacts. Each item has a
  `ref` descriptor and a readable `label`, with provenance and actual state.
- `research.source.read {project_id,source,offset?,limit?}` returns bounded literal
  `content`, `offset`, `next_offset`, `has_more`, `source`, `provenance`, and
  explicit `availability`/`integrity`. Artifact reading validates its registered
  ownership, allowed location, hash and stable file identity. Binary artifacts
  have verified metadata rather than a second generic image/file opener.
- `research.dependencies.capture {project_id,paths}` returns `{dependencies}`
  after bounded reads. It neither writes project files nor changes a record.
- `research.recheck {project_id,request_id,record_id,expected_revision}` returns
  `{project_id,record,operation}`. Check results are generated by the service,
  never accepted from RPC input.
- `research.context {project_id}` returns inspectable bounded content, source
  record/revision identities, enabled state and truncation information. Reading
  context never launches a model or an experiment.

Source pages use a bounded offset and at most 16,000 requested characters, while
also respecting encoded response limits. They retain exact continuation offsets
and disclose upstream truncation or missing evidence. A reference cannot turn a
mutable artifact into a frozen snapshot or establish the truth of its contents.

## Provider integration and sharing

Research sharing is an explicit project setting, independent of ordinary
cross-chat summary sharing. Turning it on authorizes supplying accepted registry
records and their explicitly linked project sources to ordinary chats in this
project. It does not authorize arbitrary transcript retrieval. Disabling it
stops new registry context/tool injection; it cannot retract material already
supplied to a provider's native history. Managed team chats do not automatically
receive this new project context or tool access in this increment.

All ordinary backends can receive bounded accepted registry context when enabled.
Codex and OpenAI direct can additionally use the existing `kira` namespace with
`research_list`, `research_read`, and `research_propose`. These are added only
when research sharing and chat tools are enabled. They preserve model, effort,
account and execution permissions. Ollama gets the context and user-operated UI;
no new Ollama client-tool support is claimed.

Provider list/read are restricted to accepted records. `research_read` accepts
`record_id` and optionally `source_index`, `offset`, `limit` to read only an
explicitly linked source. `research_propose` accepts record content, sources and
links; the service binds author/run metadata and a durable request identity to
the actual callback. Proposals cannot forge authorship, accept a record, make an
assessment, supply trusted hash-check outcomes or execute an experiment.
New proposal sources must belong to that active run. Earlier evidence can be
referenced through links to accepted records; a model cannot use proposal
creation to retrieve unrelated chat material from the same project.

## Interface and qualification

The optional project Research dialog presents knowledge, evidence/attempts and
next steps, with a separate proposal filter. It supports record editing,
explicit acceptance/assessment, source selection from actual executions,
paginated originals, revision history and declared-file rechecking. The header's
Research button counts unaccepted proposals from one bounded `proposals` listing
(a `+` marks further pages), is highlighted while any remain, and refreshes on
project opening, reconnection, recorded `kira.research_propose` completions and
the user's own saves. The proposals view offers **Accetta**: it reads the current
revision and saves it unchanged with `accepted:true` and a fixed revision reason,
using the ordinary mutation receipt; origin and content are not rewritten. Research
drafts and uncertain request identities persist separately from chat drafts.
Project switches discard stale callbacks without losing a draft; changing chat
inside a project does not discard the project registry editor.

Tests must distinguish UI/storage/transport checks with synthetic providers from
actual inference quality. Required cases include migration preservation,
ownership, literal content, receipt replay, stale revisions, correction history,
uncertain Computer effects, disabled sharing, dependency failures, restart and
the complete create/link/assess/recheck/resume workflow. Qualification belongs
in [STATUS](STATUS.md); the stable installation is not updated by this branch.
