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

Model metadata and runtime observations remain separate: installed size, model context metadata, requested `num_ctx`, loaded context, and model VRAM allocation are distinct fields. Total device memory is not inferred from model allocation. Counts and durations come from provider response fields; absent values remain absent/unknown. Release 0.15.1 advertises images as unsupported.

For Ollama, `num_ctx` is a per-chat request override; zero leaves the model's
configured default in effect. The optional `think` setting can preserve that
default or explicitly turn the model's internal reasoning generation on/off.
The adapter never publishes or persists the raw `thinking` field.
The experimental model details expose presets for the provider default, 32K,
128K and 256K. These are requested limits, not a guarantee that the selected
model or machine can load that context; inspect Ollama's loaded-context and
memory metrics after changing the setting.

## DeepSeek account (experimental source)

The 0.16.0.dev1 branch has an account-backed DeepSeek provider. Its sign-in is
implemented in Kira using the first-party DeepSeek Harness account flow as the
protocol reference: PKCE, an exact loopback callback, state validation, and an
account grant stored privately under the selected Kira profile. Kira does not
read browser cookies or import Harness runtime code. The flow and its model
transport are described in the [DeepSeek Harness account packages](https://github.com/deepseek-ai/deepseek-harness/tree/master/packages/credentials/deepseek-account-platform)
and [account Messages provider](https://github.com/deepseek-ai/deepseek-harness/tree/master/packages/llm/llm-deepseek-account).

Inference uses the account grant only with `https://api.deepseek.com`, through
the Anthropic-compatible Messages transport and `x-dsh-auth-token`. The catalog
comes from the account model list. The adapter reuses Kira's Ollama tool loop,
so local file/command permissions, Kira service tools, browser/Computer,
questions, Teams, research and other callbacks stay service-owned. An inherited
Ollama `tools: false` preference does not disable DeepSeek's Kira tool loop;
project permission scopes still filter or reject operations. Internal
thinking blocks are discarded; Kira does not display raw private reasoning.
Normalized image attachments are sent as base64 image blocks when the selected
catalog model advertises vision; text-only models reject image inputs. The same
capability check applies to the optional DeeperSeeker catalog.
If DeepSeek ends a response with incomplete tool JSON, Kira drops every tool
call from that response, reports an interrupted turn, and marks execution as not
started. It does not retry those calls automatically; continue explicitly after
reviewing any earlier completed tool effects in the same turn.
Non-success Messages responses retain only bounded JSON `code`, `type` and
`message` fields for diagnosis; the raw provider body is discarded. Authentication,
quota, rate-limit and server errors during the tool probe now stop the turn
instead of being misreported as unsupported tools followed by another request.
This account route follows a rapidly changing developer-preview project and
has not received live account or inference qualification.

The same experimental DeepSeek provider can optionally connect to a
DeeperSeeker instance. The user selects this mode in the DeepSeek pane, supplies
the instance base URL and API key, and Kira stores the key in a separate
user-owned `0600` file inside the private profile credential directory; it is
not returned by status or exported settings. Kira calls `GET /v1/models` and
streams Anthropic Messages requests to `POST /v1/messages` with Bearer
authentication. The configured URL must use HTTP or HTTPS and cannot contain
embedded credentials, query or fragment components. Keep the instance bound to
loopback unless remote access is deliberately secured. Kira keeps its service
owned tool loop, permissions and durable context handling for this transport.
Image and context/output metadata are taken from the proxy catalog when present;
tool availability still requires Kira's side-effect-free runtime probe. This
path has not received live proxy qualification.

## DeepSeek web session (experimental source)

The separate `deepseek_web` backend uses a manually supplied `userToken` from
the user's DeepSeek web session. Kira stores it in a private file under the
selected profile, validates it against `chat.deepseek.com`, and sends it only to
that origin. It does not import another browser profile, use the Harness PKCE
grant, or call the account-backed Messages API. The current web model entries
(`deepseek-v4-flash` and `deepseek-reasoner`) are configured locally because the
adapter has no live model-inventory endpoint. The web transport solves the
site's Proof-of-Work challenge and streams completions. Kira mediates text-only
browser/web search, project, file, command and information-question tools through
strict JSON envelopes. The prompt asks the model to use one JSON envelope per
tool call; the parser also accepts legacy DeepSeek DSML envelopes and a single
matching Markdown code fence. Surrounding prose is rejected. For a malformed
envelope, Kira asks DeepSeek for a corrected JSON call up to two times
consecutively; a valid tool envelope resets this repair limit for the next tool
round. Kira executes no tool until parsing and validation succeed. Calls continue through the
service's permission, mode and approval checks. If a stream ends before a complete
response, Kira saves the last confirmed parent and marks the next turn to resume
in the same remote chat; it does not replay tools from the incomplete response.
If the Kira service itself stops during a turn, the next explicit continuation
can also resume that saved parent when every run after it is recorded as
interrupted. Kira supplies a bounded record of the interrupted request and
completed tool outcomes as historical context, and instructs the model not to
repeat recorded side effects. A missing or mismatched account, model, or remote
reference still starts a new remote chat rather than guessing which thread to use.
This is a Kira-managed protocol,
not native DeepSeek function calling. The `deepseek-v4-flash` and
`deepseek-reasoner` model choices accept image inputs in this web route: Kira uploads normalized user attachments and requested
Browser screenshots to the DeepSeek web file endpoint, waits for processing, then
passes the resulting file references in vision mode. Computer interaction tools are not exposed. This image-upload route is
reverse-engineered from the web client and is separate from the documented API-key
vision endpoint. Uploaded images are sent to DeepSeek under the connected web
account. Kira persists the remote chat ID and, when DeepSeek returns one, its
parent-message ID; it reuses that chat across turns for the same account and
model. Without a parent ID Kira resends its bounded transcript in that remote
chat. If the chat reference is missing or the model/account changes, Kira starts
a new remote chat seeded from the bounded local transcript. An interrupted Kira
run can resume from the last confirmed parent when its saved remote reference is
still valid. The full Kira instruction set, current project
context and tool catalog are sent when a remote chat is bootstrapped. Later
turns send a compact current-scope and request update; Kira refreshes the full
bootstrap when its application instructions, project instructions or memory,
mode, scope, settings or registered tool schemas change. Tool-loop continuations
send only new tool results and a short continuation instruction. The integration
remains experimental and has not received broad live qualification.

DeepSeek Harness' account exchange has been reported to create a platform
managed key that may remain after local sign-out; the Harness issue tracker
reports that its logout route does not revoke that key ([issue 9472](https://github.com/deepseek-ai/deepseek-harness/discussions/9472)).
Kira's DeepSeek sign-in pane discloses this before login. Logging out removes
Kira's local grant and asks DeepSeek to end the account session, but cannot
claim that the separate managed key was revoked.

The experimental 0.16.0.dev1 source maps Ollama function calls to Kira's
registered tool set: project files and commands, history and questions,
assistant/team operations, research, knowledge, project tools, goals, Studio,
browser and Computer. Each callback retains its Kira service validation,
permission and approval checks. With the general tools setting off, Ollama
receives only the separately enabled browser/Computer tools. Browser text tools
are registered only when the model passes the side-effect-free tool probe.
Visual tools additionally require `/api/show` to declare `vision`; owned
screenshots are sent in Ollama chat messages and are not persisted in events.
Kira adapts web search to `browser.search`; OpenAI-hosted `web_search` itself
remains specific to OpenAI direct. This source change has not been live
qualified across the full tool set; see [STATUS](STATUS.md).

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

Ollama chat streams use a 600-second read-inactivity timeout, separate from the
short metadata deadlines. Large prompts can take several minutes to prefill
without yielding a stream chunk; this limit is not a total run deadline and
explicit cancellation remains available. A read timeout is reported with this
cause instead of a bare HTTP client exception. When verified tools are supplied,
the Ollama-only system instructions tell the model to execute the next useful
tool call instead of ending on an announcement, and to continue after tool
results until completion or a concrete blocker. This improves prompting but
cannot force a model to emit a tool call. No thinking content is exposed or
persisted by this guidance.

If a local model template returns the specific `No user query found in messages`
error after tool results, Kira retries once with the original request as an
explicit provider-only continuation. Completed tool results remain in context;
Kira does not replay them. The recovery is bounded to one attempt per run and
does not apply to other HTTP 500 errors.

DeepSeek also uses Kira-managed automatic context compaction. The service pages
the durable original history into a provider-specific Messages checkpoint. Kira
estimates pressure from the selected model catalog and calibrates its byte/token
ratio using actual DeepSeek input usage; there is no preflight counter on this
account route. It compacts before sending a request, uses a no-tool/no-thinking
summary call, validates cited source IDs and retains a recent tail. The default
trigger is the lower of 80% of the active window and the window less the 65,536
token local request output cap and 65,536 token headroom. The most recent 16% of the
usable window is retained verbatim. Fallback catalog capacity is capped at 256K
and marked unverified. Original chat events and tool results remain unchanged;
historical tool requests are inert records and are never replayed. The summary
call consumes DeepSeek account usage. A bounded HTTP 400/413 response explicitly
identifying context overflow allows one compaction and retry; uncertain requests
are never retried.

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

## Claude Code through the Agent SDK (personal use, experimental source)

The `claude_code` backend drives the unmodified Claude Code binary bundled with
the optional `claude-agent-sdk` dependency. Install it in a development checkout
with `uv sync --frozen --extra claude-code`; the extra is not part of the Debian
package, and a plain `uv sync` removes it again. Claude Code keeps its own
sign-in: Kira has no sign-in flow for it and never reads, stores or forwards
Claude credentials. Sign in once with `claude` and `/login` in a terminal.
This provider is intended only for the signed-in user's own local work; do not
enable it in a build distributed to other people. Anthropic's terms ask
developers of products built on the Agent SDK to use API key authentication.

Kira starts Claude Code through a private generated launcher in its runtime
directory. The launcher keeps the process identity, makes it the leader of its own
session so the service can register and clean it up like Codex, and passes only
desktop basics plus `CLAUDE_*`, `ANTHROPIC_*` and locale variables. Claude Code
loads no user or project settings (`setting_sources=[]`), no external MCP servers
(`strict_mcp_config`) and no `CLAUDE.md`; Kira supplies instructions and context.

The project permission selects the built-in tools: read-only offers `Read`, `Glob`
and `Grep`; project write adds `Edit`, `Write` and `NotebookEdit`; trusted host and
full access add `Bash`, `WebFetch` and `WebSearch`. Subagents and other built-ins
are not offered. Background commands are disabled
(`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`): Claude Code ends with the Kira run, so
a command must finish within its Bash timeout. Claude Code's own per-directory
automatic memory is disabled (`CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`): Kira supplies
project memory, chat notes, cross-chat excerpts and the research registry, and the
model proposes durable records through `kira.research_propose` for user review.
Permission mode `dontAsk` denies anything not listed, and a
`PreToolUse` hook denies every unlisted tool and every file path or Glob pattern
that resolves outside the project unless full access is enabled. Observed tool
events keep the recorded identity `claude_code.<Tool>` (Kira MCP tools keep their
Kira names after the prefix); the transcript receipts show the same operation
labels as Kira's own tools with the literal path, command, pattern, URL or query,
and unknown native tools stay literal as `Claude Code · <Tool>`. Built-in tool
execution belongs to Claude Code; Kira records `tool.started`/`tool.completed`
observations and artifacts for written files and never re-executes them.
Kira's assistant, history, knowledge, research, project-tool, goal, Studio,
browser, Computer and question callbacks are served through one in-process MCP
server named `kira`; the turn stops if Claude Code reports that server as not
connected. The browser and Computer namespaces are offered under the same
conditions as for the other providers (browser enabled for the chat, Kira tools
on, not a Team member). A successful `browser.screenshot`, `computer.observe` or
`computer.act` returns its text result plus the owned screenshot as an MCP image
block (PNG, at most 2 MiB); a failed image delivery returns an error result while
the screenshot itself stays in Kira's private artifacts. Claude Code keeps those
images in its own session transcript like any tool result; Kira does not trim
them. Up to four imported PNG attachments of the current turn are sent as image
blocks of the user message; earlier turns' images are not re-sent after a
provider switch, and portable history stays text.

Discovery reads Claude Code's own model catalog and supported effort levels
without sending a prompt. The account summary contains only the plan name and
provider; the address and organization are not returned. The Claude Code session
ID is the chat's native reference and is resumed on later turns; after a provider
switch the first turn receives Kira's portable history. Only a result whose
`origin` is absent or `human` completes the Kira prompt. Claude Code reports
injected turns separately, for example a notification about a background command
stopped with an earlier run, which a resumed session handles before the prompt;
Kira records a notice and keeps waiting. **Ferma** sends an
interrupt and waits up to 15 seconds for Claude Code's terminal result. Usage
reports provider token counts only; Claude Code's API-equivalent cost estimate
is not shown. The turn's usage is normalized so that input includes the tokens
read from and written to the prompt cache (the uncached share stays visible);
the last API request of the turn is kept apart as the current context occupancy,
and the model's context window comes from Claude Code's per-model report.
Claude Code's rate-limit notice — the five-hour and seven-day plan windows its
own usage indicator shows — is recorded as an account-scoped usage event and
served by `backend.usage` to the **Uso e risorse** panel and the composer
footnote; see [ACCOUNT_USAGE.md](ACCOUNT_USAGE.md#claude-code-source).

Synthetic fixtures cover option mapping, the path guard, the launcher, streaming,
tool observation, MCP callbacks, browser/Computer screenshot delivery, image
attachments, questions, cancellation, injected-turn results and discovery. Live smoke tests ran the backend outside the service with a
signed-in subscription account: a project file read, a denied read outside the
project in the resumed session, an interrupted stream, and a resumed session
holding a stopped background command's notification, which still returned the
prompt's answer. Through an isolated service, a Claude Code chat proposed a
research record, the record was accepted, and a new chat retrieved it. The GUI integration has not been live
qualified.

```sh
uv sync --frozen --extra claude-code
uv run pytest tests/test_claude_code.py
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

The experimental DeepSeek Account adapter uses the same service-bound mailbox
callback for coordinators and members. Team member runs keep their dedicated
assignment history and mailbox; ordinary-chat context checkpoints are not
attached to them.

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
