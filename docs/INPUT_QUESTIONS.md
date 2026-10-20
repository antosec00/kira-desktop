# Persistent information questions — Desktop 0.3.1

## Purpose and boundaries

The question dialog is independent of the transcript. Structured questions stay
available while tools and messages continue streaming. The user can select one
choice or type an Other answer. Nothing is submitted by default. Closing the
dialog leaves the request available through the Questions banner or Ctrl+Shift+Q.
The dialog is nonmodal; a genuinely blocking provider question can still pause
that agent. Unsent drafts survive UI reconnect and dialog dismissal in memory,
but are not saved across application exit or project switches.

Information answers never change project permissions, approve a command, start
a new run or resume interrupted work. Ordinary prose is not parsed into invented
questions. A provider must emit the supported structured metadata or request.
Secret/password questions are rejected; sign-in uses the provider's normal flow.

## Architecture decision

Reuse committed run events as the authoritative question ledger. No extra
database migration is required: schema 3 and IPC envelope 1 remain unchanged.
The feature first shipped as 0.3.1; the matching-version hello handshake
continues to prevent incompatible clients/services. Profile selection and safe
upgrades are documented in [operations](OPERATIONS.md); historical build and
rollout records follow the [evidence retention policy](evidence/README.md).

`domain/questions.py` validates bounded forms and answers. Service `Questions`
owns IDs, persistence, idempotency, cancellation and recovery. `InputController`
and `InputDialog.qml` own presentation and unsent drafts. Providers never accept
GUI permissions implicitly through these callbacks.

## IPC and events

- `input.list {project_id,offset?:0,limit?:100}` returns
  `{requests,has_more,next_offset}`. Pages contain complete forms within 700 KB;
  all pending requests and the latest 100 finished requests are available.
  Older evidence remains in `run.events` and exports. Team-member questions are
  included. The UI refreshes on question events, project selection, reconnection
  and every 2.5 seconds to discover team requests without a run subscription.
- Each request includes `run_id,request_id,questions,blocking,status,received,
  delivered` and optional `answers,reason,delivery_transport`.
- `input.answer {run_id,request_id,answers:{question_id:{answers:[text]}}}` returns
  `{accepted:true,request}` only after durable persistence. Exactly one explicit
  nonempty answer is required for each question. Identical retries are idempotent;
  conflicting answers or closed requests fail. The GUI does not automatically
  retry an uncertain dispatch or choose a suggested option.
- `input.requested` records the form; `input.answered` records the user's saved
  response; `input.received` means the provider adapter retrieved that response.
- `input.delivered` records `transport:'turn/steer'` after a matching native turn
  acknowledgment, or `transport:'json-rpc-response'` after successfully writing a
  native request reply. OpenAI direct and Ollama record their accepted HTTP chat
  request as `responses-http` or `ollama-chat`. Claude Code records `claude-code-mcp`
  when its next model response follows the MCP tool result carrying the answer.
  Neither event proves the model used the answer.
- `input.closed` and `input.delivery_failed` leave the question and any saved
  answer inspectable and copyable. Terminal runs, cancellation, provider request
  invalidation and service recovery close undelivered requests. No automatic
  resubmission or new model turn occurs. Delivery failures cannot be resurrected
  by a late acknowledgment.

Limits: 16 requests per run; 1–3 questions per request; at most six choices per
question; 64 KiB encoded form and 16 KiB aggregate answers. Forms and answers are
redacted before persistence and revalidated. Question IDs must survive redaction
unchanged. Arbitrary model text is rendered as plain text.

## Native Codex integration

Tested protocol: installed Codex 0.162.1. Per-thread feature overlays enable
`default_mode_request_user_input` and `send_message_to_user_async`; effective
values are checked before inference. Global configuration and credentials are
untouched. Unsupported binaries fail visibly instead of silently swallowing
questions. Thread resume preserves history and the user's model/effort settings.

Two distinct native paths exist:

1. `item/tool/requestUserInput` carries correlated thread/turn/item IDs,
   `isBlocking` and questions. Kira holds the RPC response until an explicit
   answer while continuing to observe events. `serverRequest/resolved` invalidates
   an outstanding waiter. Completion and cancellation clean up owned callbacks.
2. `item/completed` with `agentMessage.delivery:'async'` and a `questions` array
   is the actual `request_user_input_async` path. Each native question contains
   `title` and optional string choices. Kira persists this metadata, waits for the
   user's response independently and sends the native question-reply envelope
   through `turn/steer`, with the original `expectedTurnId` and a stable message
   ID. It never sends an answer into a different turn or blindly retries a stale
   or uncertain steering request.

The initial live probe exposed that the old adapter discarded the async metadata
and displayed only the message text. That failed historical probe is retained privately;
synthetic RPC tests alone did not establish actual async compatibility.

Official protocol reference: [Codex App Server](https://learn.chatgpt.com/docs/app-server).
The installed binary's generated schemas and actual private-thread metadata
were used to distinguish the two native paths.

## Verification and rollout

`scripts/live_questions_check.py` uses its own short `/tmp/kira-questions-*`
store and a disposable project. It checks a new and resumed real Codex thread,
actual file work before the answer, IPC disconnect/reconnect, idempotent answer
submission, durable delivery and a file containing the controller-generated
answer. It uses `gpt-6-astra` / `ultra`; this does not change the model selected
inside the user's existing project. UI fixtures and the actual model test are
separate evidence classes. Generated reports belong in the ignored
`build/evidence/` directory and must be reviewed before sharing.
[STATUS.md](STATUS.md) distinguishes current
qualification from historical private reports and their original artifact hashes.

The active user's session must finish before switching its application/service.
No package script restarts services. Use a separate empty `KIRA_HOME` for preview;
never launch the new source against a store owned by a running older service.
After work has finished, take a consistent database backup, close old windows, stop only that
idle service through its existing client, then launch the new version with the
same explicitly chosen store. Do not replace or restart the whole WSL instance.
