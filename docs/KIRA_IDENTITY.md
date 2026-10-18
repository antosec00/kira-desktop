# Kira identity, knowledge and project tools

Integration contract for Desktop 0.10.0. Qualification is recorded
in STATUS; this document is not evidence that an untested feature already works.

Desktop 0.15.0 additionally supports [personalized identities](KIRA_SOULS.md).
Their user-level preferences, scoped experience and frozen run snapshots are
separate from this fixed application identity and from project objectives.

## Common identity and progressive context

The service supplies a compact, provider-independent identity through
RunContext.assistant_instructions even when tools are disabled. Kira is the
assistant's application identity; provider/model, capabilities and permissions
are separately reported facts. The identity encourages initiative, discovery,
source retrieval and verification without claiming unsupported capabilities,
consciousness or successful effects.

Current project instructions and explicit pinned chat notes remain binding input.
They are not silently converted into optional retrieval. Large reference material,
project memory, research records, manuals and project tool catalogs are discoverable
through bounded search/read operations. A compact anchor identifies the current
request and any explicitly saved active goal; it never promotes the first historical
request into an everlasting objective. Later user corrections take precedence.

Ordinary chats use the same Kira knowledge/tool gateways on supported Codex,
OpenAI direct and tool-capable Ollama adapters. Chat-only models retain the common
identity and a truthful capability limitation. Managed Team members keep their
assigned roles and existing mailbox/permission scope; this increment does not
automatically grant them ordinary-chat delegation or private chat-history access.

Ordinary Ollama input uses up to eight recent runs and a 32,000-byte history
budget by default. An explicit positive num_ctx selects a conservative byte
budget of twice that setting, bounded to 4,096–64,000 bytes. This is a transport
budget, not a tokenizer or a guarantee that the complete request fits the model.
Original records remain retrievable, omissions are marked, and project
instructions/pins are never silently truncated by this history window.
Codex retains its native thread; direct OpenAI retains its existing compaction.

## Knowledge API

Desktop methods require an owned project and ordinary chat:

- `knowledge.snapshot {project_id,session_id}`: identity, compact anchor,
  capabilities and source summaries, without loading every source body.
- `knowledge.search {project_id,session_id,query,scope?,limit?,cursor?}`:
  bounded lexical discovery; scopes are `all|app|capabilities|chat|project|tools`.
- `knowledge.read {project_id,session_id,ref,offset?,limit?}`: one recognized
  source reference and a bounded text page, never an arbitrary path/URL.

Search returns `items` with `ref,title,kind,summary,revision,provenance`, plus
`has_more,next_cursor`. Read returns `ref,title,kind,revision,content,offset,
next_offset,truncated,provenance`. Original content is literal data; provenance
does not make a model claim a verified result. Model access to research requires
enabled sharing and accepted, linked records. Chat history remains chat-owned.
Explicit pin contents remain available across provider changes.

The model receives only `kira.help_search` and `kira.help_read`, bound to its run;
it cannot supply another project/chat. Procedures are ordinary guide topics:
reading a procedure never executes its steps. Capabilities distinguish registered,
permitted and observed provider transport support. Unknown support remains unknown.

## Project tool library

The model uses one fixed `kira.project_tools` gateway with `action` and `payload`
objects. Actions are `describe|search|read|register|test|run`. Describe returns
the exact bounded argument contract for an action; tool-specific schemas and
implementations are loaded only after discovery. Model arguments cannot select
another project, chat, run, permission, provider or credential store.

V1 registers one UTF-8 Python script already written inside the project, using
an expected source SHA-256. Its immutable revision includes source bytes,
description, input/output schemas, declared test cases and execution bounds.
No source code is imported into the service or GUI. The script receives one JSON
object on stdin and must produce one JSON result on stdout; stderr is bounded.
The supported baseline is Python's standard library, with project cwd and a
clean environment. No dependency installation or browser-session import is implicit.

Registering requires Execute and project_write/trusted_host. Testing and running
always require Execute and trusted_host: arbitrary Python runs as the user,
not inside a claimed sandbox. A test pass never changes these permissions.
Running requires an enabled, explicitly selected revision with successful declared
tests for that revision. A modified project file does not silently replace the
registered implementation or reuse its test evidence.

Tests check declared output expectations as well as valid JSON/schema and exit
status. Results distinguish contract/test success from unverified external effects.
The service reserves a durable operation receipt before effects and registers an
owned worker process before releasing its start handshake. Duplicate/uncertain
operations are not replayed. Cancellation and cleanup preserve known outcomes;
unconfirmed cleanup retains its reservation and blocks conflicting project work.

Desktop methods:

- `project.tools.list {project_id,session_id,query?,limit?,cursor?}`
- `project.tools.get {project_id,session_id,tool_id,revision?}`
- `project.tools.history {project_id,session_id,tool_id,cursor?,limit?}`
- `project.tools.configure {project_id,session_id,tool_id,expected_revision,
  request_id,enabled}`: user-controlled activation/revocation.

Desktop reads do not start inference or run programs. The initial UI inspects
tools and their recorded tests, enables/disables a revision, and can insert a tool
reference into the composer without sending it. Creation and tests are requested
through the ordinary chat/run, using the same durable tool execution path.
Disabling a tool prevents future invocations; it does not undo completed work.

Schema 10 is additive: project tool catalog/revisions, mutation receipts,
invocations and dedicated process/member ownership tables. Historical application
tables remain intact. Runtime code/reports belong to the private selected profile,
not a public source checkout.

## Interface

The existing Kira button opens “Kira · Conoscenza e strumenti”. Its Knowledge
and Project tools views load details on demand and keep existing colors, fonts,
literal source content and project/chat ownership checks. Context and memory and
Research retain their existing editors; the new panel does not create conflicting
copies of user notes or silently change research-sharing preferences.
