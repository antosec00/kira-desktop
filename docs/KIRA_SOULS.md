# Personalized Kira identities (Desktop 0.15.0)

This contract supersedes the experimental lifecycle in
[the design proposal](KIRA_SOULS_PROPOSAL.md). Qualification belongs to STATUS;
synthetic adapter tests do not establish improved reasoning or live provider behavior.

## Identity and context

Each private application profile can contain multiple named Kira identities.
An ordinary chat explicitly selects one, or uses Kira without personalization.
Existing chats stay unselected on upgrade. Identity selection is separate from
provider, model, effort, permissions, browser consent, and autonomous objectives.
Managed Team members retain their assigned roles and do not receive this personal
memory library or the ordinary chat's profile tool.

A profile contains a name, user-authored working philosophy, structured
collaboration preferences, a learning mode, and immutable revisions. The user
can create, edit, duplicate, archive, restore a prior revision and export/import
the core. Export omits chat history, experience, provenance and old revisions;
import creates a new independent identity. Archiving is reversible retirement,
not deletion from historical transcripts, backups or already delivered context.

The service compiles a user-level capsule deterministically, using exact core
text and accepted preference values. Current specific user/project/chat
instructions override these defaults. Fixed Kira operating instructions remain
separate; editing the profile never grants new tool authority. The short core
can describe habits such as testing assumptions or adapting explanation depth;
it does not prescribe or expose the model's private reasoning.

Bounds: name 96 UTF-8 bytes, core 1,200 bytes, each structured preference 160
bytes, essential capsule 3,072 bytes. Oversized content is visibly rejected,
never silently summarized or truncated. These are context bounds, not model
reasoning/output limits. The editor displays exact bytes of its preview;
tokens are unmeasured. Unsaved preview uses placeholder identity metadata;
the saved core preview is not the entire request or its scoped memory count.

## Admission, continuity and scope

The service snapshots a profile revision and capsule hash inside ordinary run
admission. Queue starts, autonomous cycles and Team result followups use the
same boundary. Later edits affect subsequent admitted turns only. Repeated
context assembly cannot apply the same preference again. Service-maintenance
compaction reads current context without creating a run or learning from it.

Direct OpenAI receives the frozen capsule with each ordinary request outside
its saved native items. Opaque compaction content is retained unchanged; the
capsule is not reconstructed from a generated summary. Codex and Ollama receive
it through their existing user-level supplied-context path, including with
tools disabled. Native history, task anchors and later corrections remain
independent. No persona changes model/account selection or reasoning effort.

Changing the selected identity requires idle project work and a paused autonomous
objective. It atomically retires the current provider generation, clears the
native reference and creates a fresh generation with the same provider settings.
The same chat's ordinary transcript remains available through existing portable
continuity. This is an intentional style/identity change in that conversation,
not deletion of already delivered information. A new separate Kira conversation
uses a new chat and explicit profile selection.

Project-wide instructions and memory retain their existing sharing semantics.
Automatic excerpts from other chats are restricted to the same current identity
and to source runs admitted in that source chat's current identity epoch. Old
titles/first prompts do not reintroduce a previous identity's conversation after
rebinding. Archived chats, foreign projects and Team members remain excluded.
The existing project sharing switch can disable excerpts entirely.

Learned experience defaults to its originating chat. A reviewed proposal may
explicitly share a lesson within its project, still limited to the same identity.
There is no automatic cross-project lesson transfer. GUI inspection is local
user access; model retrieval is bound to its frozen run, revision and scope.

## Learning and provenance

Modes are `manual`, `suggest` and `adaptive`, with `suggest` as the default.

- Manual: user edits and preferences only; the proposal tool is not registered.
- Suggest: `kira.soul_propose` can create a pending source-linked interpretation.
  The user accepts/rejects it in the nonmodal identity panel.
- Adaptive: the same review rule applies to model-proposed interpretations.
  Additionally, an exact original user command `/anima preferenza key=value`
  applies a structured preference automatically at run admission. Canonical keys
  are `tone`, `detail`, `collaboration`, `language`; Italian aliases are `tono`,
  `dettaglio`, `collaborazione`, `lingua`. The command must occupy the entire
  user request, with no extra line. Quoted text, tool output and service triggers
  cannot impersonate it. In Suggest it creates a proposal; in Manual it does not
  change memory. Ordinary natural-language inference is not automatically applied.

Saving structured preferences in the editor is a direct user action in any mode.
No background classifier, second model or per-message reflection call is started.
Profile reads/saves use SQLite and deterministic compilation only. Sending the
slash syntax through the composer remains an ordinary provider turn and may
consume normal provider allowance.

A proposal records statement, applicability, chat/project scope and an original
user or qualifying tool-result event in the current chat. The model supplies an
event reference, not its authority or source kind. The service reads ownership,
type and original order from durable events. Reasoning summaries and assistant
self-reports are not learning evidence. Recalled/generated memory cannot become
independent corroboration of itself. Source linkage does not prove that an
interpretation is correct; accepted lessons keep that distinction when read.

Mutation receipts and tool-call receipts prevent uncertain retries from applying
effects twice. Compare-and-swap revision checks reject stale user edits. Source
order, not delayed proposal acceptance, controls preference precedence. User
edits at the same event cursor outrank old interpretations. Rollback creates a
new revision and invalidates pending older learning against an authoritative
event watermark. Retiring a memory affects future snapshots; an already admitted
turn retains its original snapshot. Private historical evidence is preserved.

The normalized library holds at most 512 active lessons and 512 pending proposals
per identity. Listing and source reads are paged; search is bounded lexical
matching, not semantic retrieval. Limits are disclosed rather than silently
dropping data. Explicit user feedback assigns bounded usefulness scores from −2 to 2 in
immutable revisions. Lexical relevance precedes this tie-breaker; reading never
reinforces a memory, and rollback restores the earlier ranking. General automatic
learning from repeated behavior is not part of this release.

## Service and tool interfaces

Schema 14 adds `souls`, `soul_revisions`, `soul_bindings`, `soul_run_snapshots`,
`soul_sources`, `soul_proposals`, `soul_memories`, `soul_revision_memories`,
`soul_operations` and `soul_tool_calls`. The migration is additive, creates a
consistent private backup and leaves previous rows and objective states intact.
`session.create` accepts an optional `soul_id` to bind a new chat atomically
before its first message; the GUI stores that choice with its unsent drafts.
IPC/event envelopes remain 1. Old binaries refuse schema 14; restore a compatible
backup before an application rollback. No separate `.kira` project folder is made.

GUI methods (all mutations require `request_id`):

- `soul.list {offset?,limit?}` / `soul.get {soul_id}`.
- `soul.create {request_id,name,core,mode?}`.
- `soul.update {request_id,soul_id,expected_revision,name?,core?,mode?}`.
- `soul.preview {name,core,mode?,preferences?}` (plain key/value preferences).
- `soul.snapshot {project_id,session_id}`.
- `soul.bind {request_id,project_id,session_id,soul_id}`; null disables selection.
- `soul.preference {request_id,soul_id,expected_revision,key,value}`; null clears it.
- `soul.proposals {soul_id,project_id?,session_id?,offset?,limit?}`.
- `soul.decide {request_id,soul_id,expected_revision,proposal_id,decision}`.
- `soul.memories {soul_id,project_id?,session_id?,query?,offset?,limit?}`.
- `soul.forget {request_id,soul_id,expected_revision,memory_id}`.
- `soul.feedback {request_id,soul_id,expected_revision,memory_id,helpful}` (GUI only).
- `soul.history {soul_id,offset?,limit?}`.
- `soul.rollback {request_id,soul_id,expected_revision,target_revision}`.
- `soul.archive {request_id,soul_id,expected_revision}`.
- `soul.source {soul_id,source_id,project_id,session_id,offset?,limit?}`.
- `soul.export {soul_id}` / `soul.import {request_id,profile}`; `kira-soul-v1` core only.

Knowledge tools extend `help_search` with scope `soul`; `all` also includes
eligible experience. `help_read` accepts revision-qualified memory/source refs.
Discovery never loads every lesson into the model context. The optional
`kira.soul_propose` tool is available only to an active ordinary run with a selected
learning-enabled profile and tools enabled. Its fields are `statement`,
`applicability`, `source_run_id`, `source_seq`, optional `kind`, `scope`, `key`,
`value`. It proposes only; it cannot archive, rebind, accept or rewrite a core.
User preferences and learning are not execution evidence or goal progress.

## Interface and qualification boundaries

Open **Anima** beside the model controls, or **Settings / Work / My Kiras**.
The panel provides identity editing, structured preferences, experience with
original sources, revision history, rollback, and core copy/import/duplicate.
Editor drafts survive closing the panel or switching chats. A pending uncertain
mutation retains its request identity for verification rather than generating
a new duplicate operation. Model/provider selection and conversation drafts
remain independent of profile editing.

STATUS distinguishes tested source, installed artifacts and any live provider
evidence. Continuity and deterministic reconstruction are testable contracts;
perfect recall, consistent model compliance and better reasoning are not guaranteed.

## 0.15.1 provider bridge correction

The shared client-tool catalog and its exact Kira namespace bound include
`kira.soul_propose`. The Codex MCP relay derives its allowed Kira names from the
same catalog and publishes the matching proposal schema. Unknown profile
mutation methods remain unavailable. This repairs a 0.15.0 registration omission
that rejected any enabled-tool turn with a Suggest or Adaptive profile before
inference, even when the user requested only a greeting. Manual/unselected
profiles did not register the affected tool. Schema 14, permissions, profile
revisions and evidence/review semantics are unchanged.

Service-to-actual-adapter tests now include complete assembled profile tools,
instead of verifying only a supplied identity string or a capture backend.
OpenAI and Ollama use synthetic HTTP, and Codex uses its real private MCP
subprocess with a simulated App Server. These tests reproduce the original
registration failure without accessing live accounts.
