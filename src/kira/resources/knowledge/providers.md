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

The current capability response distinguishes registration, permission and
observed transport. Loading this guide performs no provider call or capability
probe. Private reasoning, opaque context and credential stores are excluded.

## Public reasoning summaries

Kira 0.14.1 can display public summaries supplied by OpenAI Responses and Codex,
when the selected model/connection provides them. The compact panel below the
chat can be expanded or hidden in settings. It is optional presentation data,
not private reasoning access, a final answer or execution evidence. Missing or
unsupported summaries do not establish that the model stopped working.
Ollama's raw thinking is not exposed through this public-summary feature.
Do not invent a summary to fill an unavailable provider stream. Recorded public
summaries remain separate from portable model memory and goal completion proof.
