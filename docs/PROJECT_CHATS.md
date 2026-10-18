# Project chats and continuity

## Storage and additive IPC

Desktop 0.8 uses database schema 7 and IPC1. Additive tables hold archive, branch,
objective, provider-generation and image-link records without moving or merging
existing transcript rows. Sessions belong to one project and runs to a
session. Named chats store only internal chat_title
metadata in the existing session settings JSON; it is stripped before inference.
No title implies a whitespace-normalized first-request title, or Nuova chat.

- session.create accepts optional title (at most120characters).
- session.list {project_id,offset?,limit?,archived?}: ordinary chats, newest first;
  archived defaults false and true selects archived chats; bounded
  pages (limit<=200,700KB); returns sessions,has_more,next_offset.
- session.snapshot {project_id,session_id}: owned ordinary session, last60runs
  in chronological order and at most200session artifacts; previews/truncation
  are explicit and overall payload bounded to700KB.
- session.rename {project_id,session_id,title}: changes only an owned ordinary
  session title. Managed team sessions are excluded.
- project.context {project_id,session_id?}: inspectable content,sources,
  truncated,enabled,chars for the context described below.
- project.update accepts share_session_summaries:boolean as an atomic flag
  change in the current project memory; it preserves concurrently updated notes.
- session.archive, session.fork, session.export and goal.get/update implement
  the additional controls in [WORKBENCH](WORKBENCH.md). Their internal metadata
  never becomes provider model settings.

An unknown or foreign session is rejected before content is returned. Frontend
selection failures block sending into an unvalidated chat. Generation guards
reject stale project/session snapshots and event callbacks.

## Conversation and activity

Each selected chat shows only its own transcript, usage, supplied context and
artifacts. Serial paged replay keeps older runs ahead of later runs, including
buffered live events. Bounded buffer overflow defers recovery to the appropriate
replay turn rather than repeatedly fetching another chat's first page.
Lifecycle cards are removed from chat. Activity retains project execution state,
request context, errors and detailed metadata, with a link back to its chat.
No durable event or export record is deleted.

New chat starts a blank draft and keeps the explicitly chosen backend/model/
effort. Its durable session is created on first send, avoiding empty duplicates.
Renaming cannot change a provider thread. Composer drafts are saved privately
per project/chat in `composer.json`, including the blank new-chat scope. The
rolling sent-message history contains at most 200 service-acknowledged entries
and is searched within the selected project. Reusing a message inserts editable
text without sending it; clearing that local history preserves conversations and
drafts. The shared file has a 2 MiB limit, at most 100 drafts and 262,144 UTF-8
bytes per text entry. Oldest reusable history entries may be dropped to fit
before a draft save is rejected. Draft-limit failures are visible and do not silently evict
another draft. These GUI records are separate from authoritative service events
and from transient question-answer/team editors.

Pending queue/steer/fork/compact/provider-switch identities are saved before RPC and survive GUI
restart and context changes. Retrying the same pending input reuses its identity;
unknown native effects are not automatically repeated. An explicitly confirmed
reset of the local retry guard only permits a later separate request and warns
that its effect may duplicate an earlier uncertain one.

The last selected chat is saved privately for at most32projects. Preferences are
bounded to16KiB; existing values remain compatible. One active run per project and
existing project/tool permissions are unchanged; reading another chat never
cancels a background run or permits concurrent conflicting writes.

## Archive, branch and export

Archiving an idle chat hides it from the normal list and preserves its provider
reference, history and files. Archived chats remain inspectable and exportable;
restore one before sending another request. New shared excerpts omit archived
chats. Archive state belongs to Kira, not the provider's independent archive UI.

**Continue from here** creates a new chat at the current end or a selected
completed turn. Codex uses a native branch and requires a recorded native turn
boundary for an intermediate selection. An older chat without that boundary can
branch at its current end. Native inherited context stays in the provider thread;
the new local transcript starts with new requests. A banner links back to the
original Kira chat through **Open original chat**. Ollama receives an explicit copy of recorded
conversation context; no fabricated runs/events are inserted. The complete
copied source is bounded to 8,000,000 UTF-8 bytes, with an explicit refusal beyond
that bound. Later model requests receive a disclosed excerpt of at most 100,000
source characters. The source chat remains unchanged, and no branch action
undoes project file changes. Native effects have durable request identities;
an uncertain outcome is not automatically retried.

**Export chat in Markdown** writes the complete readable transcript followed by
retained activity records and the current objective, when present. It includes
accepted steering messages and incomplete assistant streams. Stored truncation
markers remain intact; artifact references are preserved without reading or
embedding their files. Large exports use a private service file and an explicit
destination chosen in the GUI. A display preview is not a full export. A native
branch export includes that branch's locally recorded requests and activity;
it does not invent copies of inherited provider history that Kira did not record
in the branch. Export the original chat separately for its retained transcript.

Active objectives are explicit context for subsequent turns. Pause removes the
objective from new context without retracting already supplied text; resume
restores its inclusion. **Continue with one step** sends a single explicit new
request. Marking an objective complete is the user's declaration, not an
independent evaluation of task success.

## Bounded project continuity

User-maintained project instructions/memory remain primary. At each turn, Kira
can additionally supply recorded excerpts from at most five other ordinary chats
in the same project, excluding the current chat, archived chats and managed team sessions.
Each source uses its latest completed run, request excerpt<=400characters and
latest recorded assistant text<=800characters, with session/run references.
The entire extra block is limited to6000characters; omission is explicit.
A completed backend result is not proof that every claim is true: excerpts are
labelled recorded requests/assistant claims and must be checked against evidence.

This is deterministic extraction, not an extra model summarization call or a
copy of every conversation. SQL ownership joins prevent Kira from retrieving
other projects for this block. Existing provider/tool filesystem permissions do
not change; this is a context boundary, not a new sandbox. The exact supplied
block appears in context.supplied and is inspectable before a subsequent turn.
The current chat retains its own provider history/thread as before.

Sharing defaults on for the requested project continuity feature; the project
memory flag share_session_summaries=false disables future excerpt injection.
Changing it does not retract context already sent into a persistent provider
thread. There is no claimed token/cost reduction percentage; provider prompts,
manual memory and tool data are separate from this6000character bound.

## Human-readable usage

UsageDialog separates last-request token counts, cumulative thread totals and
reported context capacity. Cached input is not added twice; cumulative totals
are not remaining context. Unknown/invalid metrics are labelled unavailable,
zero is valid, and no price or savings estimate is fabricated. RAM/GPU cards
report observed host resources, not remote-model compute or billing. Raw JSON
remains available through Dati originali and the original copy action.

## Verification boundary

Source/Qt/installed tests use clearly synthetic data. A separate two-turn live
Codex test verified the project marker moving from chatA to chatB through the
provided context, with different provider thread IDs and no tool calls. A
synthetic unrelated-project canary was absent from the supplied block. Its
historical private report retains runtime0.3.7, before the release metadata bump
to0.3.8. See [STATUS.md](STATUS.md) for the scope of current qualification and
the [evidence retention policy](evidence/README.md) for historical records.

## Subsequent fixes (0.4.1)

Same-backend model/effort selection now updates the owned idle session through
`session.configure`, preserving chat identity and the native thread. Historical
replay stages presentation and reveals the bounded transcript at its end after
one model reset. See [canonical contract](CONTRACTS.md#in-place-model-selection-and-batched-history-desktop-041).

## Changing providers and sending images

`session.switch` preserves chat identity, title, drafts and transcript while
changing the provider of subsequent messages. Each provider keeps a separate
configuration; returning to Codex starts a fresh native thread with bounded
portable context if a different provider has been selected. Native branches
from older provider generations are rejected before any provider effect.
See [provider continuity](PROVIDER_CONTINUITY.md).

Explicit composer image selection stores private previews and metadata in the
per-chat draft. Sending links those images to the durable user event; text paths
alone remain plain text. OpenAI direct receives supported selected image bytes,
including eligible earlier image turns within the request budget. Other providers
receive only recorded textual context; images are not silently moved into native
threads. See [image attachments](IMAGE_ATTACHMENTS.md).
