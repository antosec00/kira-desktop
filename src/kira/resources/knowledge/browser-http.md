# Browser, authentication and independent HTTP work

Kira's browser is an isolated, owned Chromium actor. Agent page tools and visual
Computer operations use its page surface. Manual mode exposes the native browser
surface so the user can operate browser dialogs. Computer coordinates do not
control the general desktop. Manual ownership pauses agent actions until the user
explicitly returns control. Never infer that an idle model is doing hidden work.

Browser action consent belongs to this chat. It does not authorize host commands,
credential extraction or account changes. Native Windows authentication, when
available, uses a user-operated Windows security dialog; PINs and biometrics are
not model input. The Linux browser cannot assume Windows Hello availability.
Unsupported authentication options produce explicit errors. Authentication
completion alone does not establish successful site login.

An HTTP client is a different tool. For an independently authorized API task, a
project tool may send bounded requests under the project's host-execution and
network permissions. It does not inherit browser cookies, local-storage tokens,
login sessions or authentication consent. Do not extract or transfer these
implicitly. Public-page research, browser interaction and API testing have
different capabilities and evidence.

A denied browser action or provider refusal is not authorization to perform the
same refused operation through HTTP, another tool, account or provider. Resolve
the applicable permission or unsupported capability. Independent authorized API
tests remain possible within their own scope; describe their results separately.
