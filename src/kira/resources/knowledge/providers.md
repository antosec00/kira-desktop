# Provider differences and tool transport

Kira's identity and service permissions are common. Inference, model capability,
tool transport and native conversation state still differ by provider. Preserve
the selected provider, account, model and reasoning effort.

OpenAI direct uses Responses and Kira-owned function callbacks, with a durable
working context and original-history recovery. Hosted web research is observed
provider execution; it is distinct from the local isolated browser actor.

Codex uses its app-server transport and checked thread-local configuration. Kira
callbacks may use the owned MCP relay so existing threads can resume. Registered
Kira tools do not establish availability of every Codex-native feature or grant
access beyond the selected sandbox/permission mapping.

Ollama model metadata alone does not prove reliable tool calling. The adapter's
observed verification governs whether it exposes tools. Chat-only models retain
the Kira identity and supplied constraints, while unavailable tools remain
unavailable. No weights, endpoints or credentials are changed by knowledge lookup.

DeepSeek Account uses the user's signed-in DeepSeek account through DeepSeek's
Messages API. The service runs the same Kira tool loop and permission checks as
for Ollama and keeps a durable Kira-managed context with automatic compaction,
like OpenAI direct. Images require a model whose catalog declares vision.
DeepSeek's internal thinking is discarded and not shown.

DeepSeek Web uses the chat.deepseek.com web session token instead of the API and
depends on that site's behavior. The site has no native function calling: the
model requests each tool with Kira's strict JSON text envelope, and Kira
parses, checks and executes it. Kira's Computer is never offered; a browser
screenshot is attached to the next message for models that accept images. One
remote site chat is reused per Kira chat, without Kira-managed compaction.
DeepSeek Web has no autonomous goals.

Claude Code runs the locally signed-in Claude Code for personal use. Its
built-in tools execute inside Claude Code; Kira observes them and never re-runs
them. The project permission selects an allowlist: `Read`, `Glob` and `Grep`;
plus `Edit`, `Write` and `NotebookEdit` with a write permission; plus `Bash`,
`WebFetch` and `WebSearch` with trusted-host or full-access. Claude Code runs in
`dontAsk` mode with that allowlist, and a pre-tool check denies every other tool
and every path outside the project except with full-access. Kira's own tools
reach it through an internal MCP server named `kira`, as
`mcp__kira__<namespace>__<tool>` (for example `mcp__kira__browser__navigate`).
Claude Code keeps its own session history.

Managed Teams run only with Codex, OpenAI direct, Ollama or DeepSeek Account.
The current capability response distinguishes registration, permission and
observed transport. Loading this guide performs no provider call or capability
probe. Private reasoning, opaque context and credential stores are excluded.

## Public reasoning summaries

Kira displays public summaries supplied by OpenAI direct (Responses) and Codex
when the selected model/connection provides them. Ollama, DeepSeek Account,
DeepSeek Web and Claude Code supply none. The compact panel below the chat can
be expanded or hidden in settings. It is optional presentation data, not private
reasoning access, a final answer or execution evidence. Missing or unsupported
summaries do not establish that the model stopped working. Ollama's raw
thinking is not exposed through this public-summary feature. Do not invent a
summary to fill an unavailable provider stream. Recorded public summaries
remain separate from portable model memory and goal completion proof.
