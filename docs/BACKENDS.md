# Backend integration and evidence

Kira distinguishes inference with a local tool loop (`OllamaBackend`) from an existing agent runtime (`CodexBackend`). Both implement `domain.contracts.Backend`; only the service persists events and changes run state. Providers never treat assistant narration as execution evidence.

## Codex App Server

The adapter was developed against `codex-cli 0.162.0` on Ubuntu 24.04 amd64. The application uses its configured binary, default `codex` from PATH, without replacing or updating that installation. An explicit binary path must refer to the user's own installation.

`codex app-server generate-json-schema --out protocol-schema` generated 317 schemas. The initial 27 selected schemas are retained in `protocol-schema/`, including local definitions; later feature-specific selections have their own subdirectories and manifests. The full initial generation manifest is a private historical record; see the [evidence retention policy](evidence/README.md). To audit a newer binary, regenerate into a separate temporary directory and compare the methods and fields before changing the adapter. These schemas are evidence and test inputs, not dynamically downloaded application dependencies.

The bridge starts `codex app-server --listen stdio://`, performs `initialize`/`initialized`, and correlates request IDs. Stdout parsing and stderr draining run independently. Stderr is counted and discarded because provider diagnostics can contain sensitive data. Protocol frames are bounded to 8 MiB and the notification queue to 2,048 entries. Invalid frames, overload, and connection loss end the bridge explicitly; an active turn without terminal confirmation is interrupted with an unknown outcome.

Discovery calls `account/read` and paginated `model/list`. It exposes account connection/type/plan without email or account identifiers. Model entries preserve the actual model IDs, supported reasoning efforts, defaults, and advertised service tiers. A catalog entry has `access_verified: false`: discovery alone does not prove entitlement.

Runs use their own managed App Server child. New sessions call `thread/start`; saved backend references call `thread/resume`. Each request explicitly applies the selected model, selected reasoning effort, directory, and permission choice. The resolved model, effort, and sandbox type are checked before `turn/start`; no fallback model or billing tier is selected. The session overlay also restricts shell environment inheritance to Codex's `core` setting. For an explicitly configured absolute binary, its directory is prepended to the child PATH so npm/nvm wrappers can find their sibling Node interpreter under a desktop or systemd environment. The existing PATH is retained; no project directory is added. No global configuration file is modified.

| Kira permission | Codex sandbox | Turn policy |
| --- | --- | --- |
| `read_only` | `read-only` | `readOnly`, network disabled |
| `project_write` | `workspace-write` | `workspaceWrite`, project writable root, network disabled, temporary directories excluded |
| `trusted_host` | `danger-full-access` | `dangerFullAccess`; commands act as the host user |

Codex read-only/workspace policies restrict writes and network according to the installed runtime; they are not a promise that project data is the only readable data. Trusted host execution has no filesystem containment. Approval policy is `never`, so ordinary operations authorized by the project policy proceed without repeated prompts. Unexpected approval requests cannot enlarge a read-only or project-write policy. Trusted-host callbacks can be shown through the service approval seam; Kira accepts only one-operation accept/decline/cancel decisions and never installs persistent command/network policy amendments. Additional permission grants are denied. Structured Codex question callbacks use the separate durable information-question service described below; they never become permission approvals or inferred consent.

`turn/start` receives the prompt and inspectable Kira-supplied project context. Codex's internal prompt remains opaque. Agent message deltas and completions use stable item IDs. Native command/file/MCP events are normalized into durable `tool.started`/`tool.completed` observations; they never call Kira's `execute_tool`. Duplicate item lifecycle notifications are ignored. Reasoning content is discarded. File-change completion publishes an artifact only when a corresponding file can be resolved inside the project and hashed. Files written exclusively by native commands may require explicit inspection rather than an automatic artifact entry.

`turn/interrupt` requests cancellation. A matching `turn/completed` with `interrupted` confirms it; missing confirmation after 15 seconds leaves an interrupted outcome. A late successful terminal event remains completed. Each child has a Kira-owned process group, terminated on bridge close with bounded escalation; Kira never sends signals to unrelated provider sessions. Resume means a new explicit turn on the saved Codex thread, not automatic replay of interrupted work.

Authentication uses Codex-managed `account/login/start` with `type: chatgpt`. The control App Server must remain alive while the browser completes the callback. The service retains it for discovery/login and closes it at shutdown or an applicable settings change. The response exposes only the official browser URL and login identifier; these are transient UI data. Optional `wait_login` correlates the completion notification and returns only success/failure. Kira never reads, copies, or stores OAuth tokens or browser cookies. The supported local login flow is documented by the [official App Server reference](https://learn.chatgpt.com/docs/app-server); exact request/response fields come from the installed schemas.

### Version 0.4 chat controls

[WORKBENCH](WORKBENCH.md) defines the public service APIs and recovery rules.
Selected Codex 0.162.1 schemas and their generated hashes are retained in
[`protocol-schema/workbench-0.162.1/`](../protocol-schema/workbench-0.162.1/manifest.json).
Schema availability and deterministic adapter tests establish protocol handling,
not successful live inference with every installation or account. Check
[STATUS](STATUS.md) for current qualification.

Native planning uses `turn/start.collaborationMode` after checking that
`collaborationMode/list` advertises plan mode. Ordinary turns explicitly select
the default collaboration mode so an earlier plan does not remain sticky.
The selected model and resolved reasoning effort remain unchanged. Plan,
review and recap use a read-only execution context; review and recap are explicit
Kira requests, not claims that a provider ran a separate native review job.
Applying a plan requires a later explicit execute request.
Public `item/plan/delta` and completed `plan` items become assistant message
events with `source: codex.plan`; the completed message has `phase: final_answer`
and replaces provisional text with the final Markdown plan. Private reasoning
items remain excluded from the conversation and persisted events.

Active-run steering uses `turn/steer` with the exact thread, `expectedTurnId`
and a stable `clientUserMessageId`. Its acknowledgment must identify the same
turn. The service saves an intent before dispatch and records accepted guidance
as a user message only after acknowledgment. Acknowledgment does not prove that
the model has consumed the guidance. Stale turns, unsupported controls and
uncertain delivery remain visible and are never silently sent to a new turn.
Steering preserves the active model, effort and permissions.

Native branching checks source-thread idle state, calls `thread/fork` and
requires a different idle thread identifier. Intermediate branches pass a known
`lastTurnId`; an unrecorded boundary is rejected. `deferGoalContinuation` prevents
a copied native goal from starting work merely because a branch was created.
The original thread remains intact. Kira's service persists request identity
before this effect and reserves the project against overlapping work.
The branch inherits native provider context; Kira retains a link to the original
chat and begins a new local transcript. Export reports only locally recorded
branch history rather than fabricating duplicated source events.

Explicit compaction checks idle state and rejects an independently active native
goal before resuming the thread. `thread/compact/start` acceptance is insufficient:
the adapter observes a matching `contextCompaction` item and successful terminal
turn evidence before reporting completion. Disconnect, timeout or mismatched
events leave an unconfirmed result. Kira retains its local transcript and does
not infer token savings. The managed App Server is closed after the operation;
global provider configuration and credentials remain unchanged.

Native control children register durable process ownership before thread effects.
The service verifies cleanup against recorded process identity before releasing
the project reservation; unavailable cleanup proof keeps the reservation blocked.
Ordinary thread resume and compaction reject unfinished native Codex goals that
could cause unsupported automatic continuation, without pausing, completing or
rewriting those goals. Native forks defer inherited goal continuation. These
provider-owned goals are separate from Kira's explicit, locally stored objectives.

Queues, archive visibility and explicit Kira objectives are local service
features. A queue request starts an ordinary provider turn only when admitted.
An active objective supplies inspectable context to later explicit turns; Kira
does not install a provider goal or enable automatic goal continuation. Marking
it complete records the user's declaration.

### Persistent information questions

When the service supplies `RunContext.request_input` and `read_input`, the adapter
opts its connection into the experimental API and sets only the thread-local
`features.default_mode_request_user_input` and `features.send_message_to_user_async`
overlays to true. It verifies both effective values with
`experimentalFeature/list` before inference. These features were verified in an
isolated, inference-free Codex 0.162.1 thread; an older runtime that does not expose
them fails visibly before starting a turn. Global Codex configuration, credentials,
selected model/effort, sandbox and existing saved threads are preserved. Both
`thread/start` and `thread/resume` receive the overlay. Dynamic tools or a replacement
thread are unnecessary for this native integration.

Two native protocol shapes are supported. `item/tool/requestUserInput` is a
server request with `threadId`, `turnId`, `itemId`, `questions` and `isBlocking`.
Kira passes its structured questions to the durable service with the item ID as
the run-scoped deduplication key, then replies with only the explicit validated
answer map. A successful reply pipe write emits `input.delivered` with transport
`json-rpc-response`; this records sending, not model consumption. A failed write
after retrieval emits `input.delivery_failed`. Nonblocking requests return a
service ID immediately and the bridge
waits through `read_input`; blocking requests wait through `request_input`.
The deprecated `autoResolutionMs` is not a consent timeout.

The installed native `request_user_input_async` tool instead produces an
`item/completed` **agentMessage** with `delivery: async` and structured
`questions: [{title, options: [string]}]` metadata. Its text is only a display
summary. The adapter recognizes the metadata, assigns stable per-request `q1`
through `q3` IDs, and enables a free-text Other response. It never parses prose
into a question. After the explicit answer, it sends the native
`<send_user_message_question_reply>` JSON envelope through `turn/steer`, with
`expectedTurnId` and the durable request UUID as `clientUserMessageId`. Each reply
contains the question, answer, and native `questionItemId` tuple encoding.
`input.received` means the adapter retrieved the answer; `input.delivered` is
emitted only when the steer response confirms the expected active turn.
A rejected/stale/unknown delivery emits `input.delivery_failed`, retains the
saved answer for copying, and does not retry or start a different turn.

A real isolated probe exposed this second shape: its first attempt wrote the
independent file but no persistent question appeared because the initial adapter
handled only the server request. The controller cancelled that disposable run;
the metadata route and delivery tests were added before repeating qualification.
This distinction is why fixture success alone is not live qualification.

Pending question callbacks run independently of provider notification consumption.
Command observations, assistant messages, turn completion and cancellation continue.
An exact `serverRequest/resolved` correlation cancels its waiter; completion,
disconnection or interruption cleans up remaining waiters. The service closes
unanswered requests when their waiter or run ends. Late submission is not a new
model turn, and a saved answer is distinct from receipt by the provider callback.
No timeout supplies a default answer. Unsupported secret questions are rejected
before persistence; passwords and tokens belong in provider authentication flows.

Questions are bounded by the shared domain contract (three questions, six choices,
64 KiB per request and sixteen requests per run). Correlation uses the exact active
thread and turn; repeated RPC IDs must have identical payloads and reuse the cached
reply. Conflicting input, malformed service results or a failed callback stop with
an explicit unknown outcome. The provider prompt instructs the model to use native
structured questions, keep working on independent tasks where possible, and never
use missing answers as permission. This prompt cannot turn an ordinary text
question into a structured callback retroactively.

The historical generated question schemas and inference-free configuration
probe are retained privately under the [evidence retention policy](evidence/README.md).
Deterministic coverage in
`tests/test_backends_questions.py` proves callback routing, async observation,
answer delivery, cancellation and disconnection, stale correlation, duplicate IDs,
input bounds, unsupported secrets and feature-preserving resume. These tests and
the configuration probe do not by themselves prove live model invocation; live
integrated qualification is reported separately.

### Version 0.3 team bridge

Team members use independent Codex threads through the same adapter. They retain
the explicitly selected model, reasoning effort, service tier and project
permission policy. `RunContext` optionally supplies a provider-start hook; the
adapter calls it with its App Server PID after initialization and before creating
or resuming a thread. Failure to record ownership closes the child and prevents
inference. The service owns the durable relationship between the process, member
and run; this callback does not establish ownership from an arbitrary saved PID.

The service registers only the `team` dynamic-tool namespace, containing
`send_message` and/or `read_messages`. A namespace has `type: namespace`, `name`,
`description` and `tools`; each function has `type: function`, `name`,
`description` and an object `inputSchema`. These exact shapes come from the
installed experimental schemas. As documented by the
[official App Server reference](https://learn.chatgpt.com/docs/app-server),
the adapter enables `capabilities.experimentalApi` only for connections using
these callbacks, passes `dynamicTools` to `thread/start`, and relies on the saved
thread definition during `thread/resume`. Single-agent sessions with the persistent question callbacks also opt into this
experimental API, without registering team tools.

A server request `item/tool/call` is distinct from a native tool observation. The
adapter checks the exact current thread and turn, a nonempty call ID of at most
256 characters, namespace `team`, the registered function name and JSON-object
arguments. It dispatches `client_tool_call(call_id, canonical_name, arguments)`;
it never routes these requests through the ordinary project-file tool executor.
The service validates recipients and arguments, scopes messages to the team and
member, commits effects and their events, and returns an explicit `{ok: bool}`
result. The adapter replies with `success` and a JSON-text `contentItems` entry
of type `inputText`. It suppresses duplicate native lifecycle observations for
these two mailbox tools because the service records their actual outcomes.

Definitions, arguments, callback results, structured-output schemas and final
structured text are each bounded to 64 KiB of UTF-8 JSON/text. Non-finite JSON is
rejected. Repeated callback IDs with identical canonical inputs return the cached
reply; conflicting inputs stop the run with an unknown outcome. The in-memory
retry cache is bounded to 2,048 calls and 8 MiB, reserving response capacity before
the next effect. Reaching either bound stops visibly. These are storage bounds,
not model reasoning or turn limits. Durable idempotency across service/process
failures remains the service's responsibility. A callback exception or invalid
result after dispatch is interrupted rather than reported as safely unexecuted.
Cancellation stops the callback and requests normal provider interruption; a
callback that ignores cancellation for one second produces an explicit unknown
outcome. No callback is replayed to recover an uncertain effect.

The planner can request stable `turn/start.outputSchema`. Only a completed
assistant item with final or unspecified phase supplies `outcome.final_text`;
commentary is excluded, and the field remains null if no final item arrived.
The service must still parse and validate the returned plan. A successful provider
terminal event alone does not prove that the plan satisfies Kira's contract.

For team members and an explicitly marked coordinator, the thread overlay sets
`features.multi_agent`, `features.multi_agent_v2` and
`features.multi_agent_v2_dynamic_tools` to false. Before inference, the adapter
queries `experimentalFeature/list` for that actual thread and requires all three
effective values to be false. Missing values, enabled delegation, invalid replies
or an unverifiable configuration stop before inference. This disables Codex's
built-in subagent delegation; it does not create an extra security sandbox for
arbitrary commands. Filesystem/network permissions remain the policy above.

Selected experimental schemas and their hashes are retained separately in
[`protocol-schema/teams-0.162.1/`](../protocol-schema/teams-0.162.1/manifest.json).
The eight selected schemas are byte-identical in the locally available Codex
0.162.0 and 0.162.1 binaries. Four isolated, inference-free protocol probes
confirmed the exact model/effort, read-only sandbox, optional namespace acceptance
and effective disabled delegation in both versions, with experimental capability
both off (planner) and on (mailbox). No binary upgrade, global configuration edit
or authentication change was required. These probes do **not** establish account
entitlement, model-generated tool invocation or a functioning complete team;
those require the coordinator's separate integrated live qualification.

The subsequent isolated live provider check on 2026-10-12 used Codex 0.162.1,
`gpt-6-astra` / `ultra`, the real shared `mailbox_tool_specs()` and
`coordinator_output_schema()`, and an empty read-only project. In 21.651 seconds,
the model called `team.read_messages`, then `team.send_message` with the exact
public code returned by the first callback, and produced valid final JSON with
`decision: needs_input`, empty members/tasks and a summary. Ten checks passed,
including preserved configuration, unique call IDs, no native tools observed,
no project files created and child-process exit. The callbacks deliberately used
an in-memory benign mailbox: this establishes real provider protocol operation,
not service persistence, scheduling or collaboration between real members.
The historical probe report is retained privately, SHA256
`cdc97ec9b8d7dc9e8e02734f98de2dcf802708ade4c2e359f622f981e831cfe5`.
Its hash identifies the original report, not a downloadable public artifact.
Provider stderr, authentication payloads and private reasoning were not captured.
No protocol correction was required by this historical live check. See
[STATUS](STATUS.md) for the scope of current qualification.

### Codex evidence

- Historical private discovery records document real initialization, authenticated account state, and catalog. They are not proof of access for another installation.
- Historical private live records document a real `gpt-6-astra` / `ultra` read, file edit, and independent assertion run, including durable events, an artifact hash, and reconnect/idempotency results. The controller independently checked the generated result against a fresh file input.
- Deterministic tests separately cover stream corruption, stderr backpressure, response correlation, policy/model/effort preservation, native-tool deduplication, resume, failure, cancellation, approval boundaries, and login correlation. They are not authentication or live model tests.

Raw provider records are excluded from the public repository under the
[evidence retention policy](evidence/README.md). [STATUS](STATUS.md) records
historical result scope and current qualification separately.
New local checks write reports under ignored `build/evidence/`; review and
sanitize any report before deliberately sharing it.

## Ollama native API

The configured endpoint defaults to `http://127.0.0.1:11434`. Explicit HTTP(S) remote endpoints are supported; embedded credentials are rejected. Kira does not switch endpoints, pull models, or download weights. The native API uses NDJSON, not SSE. `/api/tags`, `/api/show`, `/api/ps`, and `/api/chat` provide catalog, capability metadata, loaded model details, and inference. These interfaces are documented by [Ollama chat](https://docs.ollama.com/api/chat), [tool calling](https://docs.ollama.com/capabilities/tool-calling), and [running models](https://docs.ollama.com/api/ps).

Model metadata and runtime observations remain separate: installed size, model context metadata, requested `num_ctx`, loaded context, and model VRAM allocation are distinct fields. Total device memory is not inferred from model allocation. Counts and durations come from provider response fields; absent values remain absent/unknown. Images are advertised as unsupported by this release even when an underlying model accepts them.

The 0.2 audit correction bounds metadata before JSON decoding: 262,144 bytes per
response and a 10-second total deadline. Requests require identity encoding;
unexpected compression is rejected before HTTPX decompression. Discovery has a
20-second total deadline, 64-model cap and 512,000-byte serialized response cap.
Model names are bounded to 512 bytes; retained details to 32,768 bytes each and
512,000 bytes/64 entries overall. Shape checks precede field access. Optional
loaded-model/detail failures are reported explicitly; direct run initialization
uses the same validated detail/cache path. These limits may reject large catalogs
or proxies that ignore the requested identity encoding. No silent endpoint or
model change is performed.

Tool metadata alone does not enable execution. When a run requests tools for a model advertising tool support, a fresh side-effect-free function call probe checks that it returns an exact random value in valid tool arguments. Failure preserves chat availability with a visible chat-only notice. Verification is cached for the adapter lifetime; run events record whether the probe passed.

The service owns the actual `list_files`, `search_files`, `read_file`, `write_file`, and `run_command` operations. Tool schemas are restricted by project permission. File writes require an expected SHA256 for an existing file or null for create-only; commands use argv without implicit shell interpretation. The provider validates complete argument objects and sends structured errors back to the model when they are invalid.

The parser handles split UTF-8 and JSON records incrementally, with a 1 MiB record limit. Assistant content, private thinking required by the API, and tool calls are accumulated until `done: true`. Tools are never executed from a partial response. Thinking is retained only in the active in-memory exchange and never published or persisted; the service also removes private fields recursively from retained provider messages. Portable assistant/tool message fields are preserved for subsequent turns. If interrupted history contains a tool request without a recorded result, the next explicit continuation receives `UNKNOWN_OUTCOME` and an instruction to inspect current state; no side effect is replayed by the adapter.

Cancellation closes the active client stream and cancels Kira-owned local work through the service. The result explicitly does not promise remote generation termination. Broken streams cannot become successful runs. HTTP overload and invalid tool requests are visible failures. A configured `max_tool_rounds` is enforced; three identical successive tool requests/results stop with a stated lack-of-progress reason. Response bytes and command timeouts use the service's configured resource limits. These guards are not represented as model reasoning limits.

### Version 0.4 portability

Ollama uses the same durable queue, archive/export controls, private GUI drafts
and explicit Kira objectives. Plan, review and recap narrow the local execution
policy to read-only and supply the selected request context; they do not pretend
to enable a Codex-specific native mode. Native steering and native context
compaction are unavailable for this adapter.

A branch copies the selected readable conversation into separate service-owned
source context while preserving the original session and provider settings. It
does not clone an Ollama process or create fabricated historical run events.
The stored source and subsequent model excerpt have explicit bounds described
in [Project chats](PROJECT_CHATS.md). Accepted steering messages already present
in a source transcript are treated as recorded user text; tool execution and
uncertain effects are not replayed.

### Ollama evidence and remaining dependency

There was no reachable Ollama instance or installed `ollama` binary in the build environment. Real local conversation, model loading, local tool capability, and measured GPU/Ollama metrics remain **NOT RUN** pending a user-configured instance with an installed model. No weights were downloaded. Deterministic HTTP fixtures test malformed/partial streams, UTF-8 splits, tool assembly and validation, chat fallback, cancellation, metadata/loaded-context distinctions, persistent tool history over two runs, and uncertain historical outcomes.

Run provider regression checks with:

```sh
uv run pytest tests/test_backends_codex.py tests/test_backends_ollama.py
uv run pytest tests/test_backends_teams.py
uv run pytest tests/test_backends_workbench.py tests/test_service_workbench.py tests/test_conversation_controls.py
uv run ruff check src/kira/backends tests/test_backends_codex.py tests/test_backends_ollama.py tests/test_backends_teams.py
uv run mypy src/kira/backends
```

## Kira assistant tools (0.5)

Ordinary Codex chats may receive the scoped `kira` namespace through a private
per-run MCP overlay. The four tools inspect the current environment, request
managed Team delegation, inspect results and record Team messages. Existing
thread IDs are resumed; this does not migrate conversations to a new thread.
The bridge verifies provider inventory/initialization before the turn, then routes
callbacks through a capability bound to the originating run. Current model and
reasoning choices are preserved. Kira tools alone receive explicit approval in
this thread-local overlay; project permissions are still checked by the service.

Managed Team members use the existing dynamic `team` mailbox namespace. Both
paths disable and verify native delegation so the visible Kira roster remains
authoritative. MCP notifications remain observable tool evidence, including
approval failures, and never cause a second execution of an action.

The service-owned assistant operating contract uses supported developerInstructions;
project instructions, memory and tool content are never promoted into that field.
Tool-disabled and unsupported backends do not claim this integration. A real
provider check is available as `scripts/live_assistant_check.py`; exact model
and effort are required and generated reports are private.


## OpenAI direct with a ChatGPT plan

Desktop 0.6 adds `openai_chatgpt` alongside Codex and Ollama. It uses official
Sign in with ChatGPT, account-bound sessions and a Kira-owned Responses tool
loop. See [authentication, capabilities and limits](OPENAI_DIRECT.md). Existing
Codex sessions retain App Server ownership and their native references.
