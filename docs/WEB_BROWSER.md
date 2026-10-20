# Web research and isolated browser

Desktop 0.9.2 introduced these capabilities for **OpenAI direct · ChatGPT**.
The 0.16.0.dev1 experimental source also enables the Kira-owned browser for
ordinary Ollama chats. See [STATUS](STATUS.md) for source, browser, authenticator
and provider qualification; a successful browser check does not establish a
website login.

## Research and reading

The direct adapter supplies OpenAI's hosted `web_search` tool when chat tools
are enabled. The per-chat Web dialog selects `live` (default), `cached` or
`disabled`. Live permits external web access; cached sets
`external_web_access:false`. These are provider search modes, not a local web
index. Account/model/workspace policy can reject the tool. Rejections remain
visible; Kira never changes account, model, effort or billing to work around them.

Hosted search/open-page/find-in-page activity is observed from Responses events,
bounded and stored as tool activity with provider provenance. Completed ordinary
answers include clickable HTTP(S) citation links. Structured Team output retains
its JSON contract. Only a successful response terminal confirms completed hosted
calls. Original native items remain in private context; public events do not
contain opaque reasoning. Disabling hosted search does not disable the separate
browser option. OpenAI direct requires the chat's tools to be enabled. Ollama's
browser uses its own browser/computer tool set and does not enable project file,
command or other Kira tools when the general tools setting is off.

Hosted web metadata follows the [Responses WebSearchCall contract](https://developers.openai.com/api/reference/resources/responses/methods/retrieve).
The action description is optional; an `open_page` URL may be absent or null.
Desktop 0.8.4 records these as `action_details_available:false` or
`destination_available:false`, respectively, without inventing an action or URL.
They do not invalidate an otherwise confirmed provider response. Supplied URLs
must still pass validation, and `find_in_page` still requires its URL/pattern.
Missing metadata never substitutes for a successful response terminal and a
completed tool status. Failed/incomplete/disconnected streams remain unconfirmed.
Original native items and citation annotations retain their existing roles.

Current developer guidance requires a real retrieval attempt when the user asks
to read, summarize or verify a linked page, including search-result URLs.
Ordinary wording is sufficient; users do not need to name tools. Earlier
assistant claims of unavailable browsing do not define current capabilities.
Explicit no-browsing requests and project no-network instructions remain binding;
Kira should explain such a restriction instead of claiming that tools do not
exist. URL-formatting/translation tasks do not implicitly authorize navigation.
For search-engine links, the isolated browser may receive different results,
a consent page or an anti-bot challenge. Report the observed condition; a hosted
search of the same public question is a separate search, not the user's
personalized browser view.

## Browser inside Kira

Kira owns a headed Chromium browser through pinned Playwright, in a separate
worker process and private profile. A private Xvfb display contains its native
windows; their interactive view appears beside the chat inside Kira. This display
does not expose the user's desktop or other applications. It is available to
ordinary OpenAI direct and Ollama chats; Codex keeps its existing native tool
environment. The agent opens it on demand. The same browser, page and tabs remain
available after a successful turn, until the user closes the browser, changes its
provider configuration or stops the service.

Ollama receives Kira's browser and visual-computer tools through its local tool
calling API, including `browser.search` for local public-web retrieval. Browser
text tools require the selected model to pass Kira's
side-effect-free tool probe. They remain separate from project tools when the
general `tools` setting is off. Screenshot and visual-computer tools are offered
only when `/api/show` declares image input support; Kira sends their bounded,
owned PNGs as Ollama chat message images and keeps them out of durable events.
OpenAI-hosted `web_search` remains specific to OpenAI direct. Ollama can search
and retrieve web pages through Kira's isolated browser. Tool-call and image support depend on
the selected model and its Ollama server.

The Browser pane provides tabs, address entry, back/forward/reload, a draggable
chat/browser split and an expanded view for small windows. **Prendi il controllo /
Take control** pauses agent browser actions and enables manual mouse, keyboard,
Unicode/IME input and explicit clipboard copy/paste. During agent control, the
view shows page pixels through CDP; during manual control, **Browser completo /
Full browser** identifies the native browser surface, including its own dialogs.
Native frames and manual input remain transient GUI data, outside the composer,
transcript and model context. Password fields are excluded from explicit selection
copy. The model's text and screenshot tools still operate on pages only.

Pending browser/computer tools display **In attesa del browser · controllo tuo /
Waiting for browser · your control**, with the work animation stopped.
The ownership wait has no automatic takeover or retry. **Restituisci a Kira /
Return to Kira** releases it; **Ferma / Stop** cancels the model request without
discarding a browser that is only waiting for its user. Returning control is
disabled while an authentication request tracked by the Windows bridge is pending:
finish or cancel that request first. A rendering failure is shown explicitly;
Kira does not substitute page-only pixels for a failed native view.

Address editing survives frame/status updates. Submitting a bare domain through
the address bar adds HTTPS; complete HTTP(S) addresses retain their scheme.
This GUI convenience does not bypass service URL or network validation. Viewport
maintenance does not clear a failed navigation/input message.

Minimizing the pane suspends viewport transmission without closing its page. The
pane's **×** and **Chiudi browser** controls close the actor in the service and
wait for identity-verified process cleanup; they no longer mean hide-only. A
separate panel handoff can still hide the view. The model also has a no-argument
`browser.close` tool, scoped to the current chat; it does not need website-write
permission and prevents that run from reopening the browser. Completed turns
keep the browser open while the Kira desktop remains connected. If the last
desktop connection closes, Kira closes idle browsers immediately or after their
active run finishes. Uncertain cleanup remains visible and blocks replacement
browsers.

`browser.navigate`, `snapshot`, `find`, `click`, `type`, `press`, `scroll`,
`screenshot`, `tabs`, `select_tab`, `close_tab` and `back` operate only this run's
browser actor. `browser.close` closes the chat's actor. These service/event identifiers use `browser.*`; the OpenAI wire
API exposes them as `kira_browser.*` because `browser` is a reserved provider
namespace. Ollama receives `browser__*` function names. Only typed tool definitions
and native call identity fields are translated, including historical calls used
for context maintenance. User text, tool results and opaque provider state remain
unchanged.
A fresh snapshot returns bounded page text and references to actual
visible elements. Old references expire; the model cannot supply arbitrary
JavaScript or selectors. Search and text snapshots are preferred over screenshots.
Dynamic sites can be rendered normally. Only an explicit screenshot tool call
sends viewport pixels to the model; its private preview is available in Web.

Agent browser reads are available in read-only projects and planning/review modes.
Agent clicking, typing and key presses require execute mode, a writable project and a
user consent for the specific interaction or the chat-wide policy below. Approval is not proof of
the website accepting the action; its observed result is retained separately.
File upload, automatic downloads and password-field automation are unavailable.
Some sites may require manual interaction, reject automation or present a CAPTCHA.
There is no promise of access to every website.

## Kira Computer visual tools

Desktop 0.8.2 adds a Kira-owned visual controller to the same isolated browser.
Ordinary OpenAI direct chats receive `computer.observe` and `computer.act` when
browser tools are enabled. The wire namespace is `kira_computer`, while durable
tool activity uses `computer.*`. Ollama receives the same service-owned tools
using provider-compatible `computer__observe` and `computer__act` function names.
It uses a supported Kira function/image exchange;
it does not register the provider-native `computer` tool, change the selected
model/account or require a different billing route.

The model observes a screenshot, chooses one action in its pixel coordinates and
receives a new screenshot after the action. Supported actions are click,
double-click, move, scroll, text insertion into the focused editable control,
an allowlisted key, a drag path of 2–32 points, and a wait of at most 1500 ms.
Screenshots include their viewport dimensions; coordinates belong to that image,
not the application window or the user's desktop. Existing browser navigation
and text tools remain useful for reading and for unstable visual content.

The service requires a current observation from the same run/actor. The worker
binds that observation to the page and masked screenshot, and checks it before
preparing and executing an action. Resizing, navigation, user takeover or a changed
screen requires another observation. This conservative check can reject actions
on continuously animated pages; it does not claim all sites remain visually stable.
Successful actions consume their observation and supply a fresh one. If an effect
ran but the new screenshot failed, the result states `action_executed:true` rather
than suggesting a blind retry. Duplicate or uncertain calls are not re-executed.

Click/double-click/type/key/drag require execute mode, a writable project and the
user-selected browser action approval policy. Manual control and field masking follow the
browser rules above. All input stays within the owned browser; there is no shell,
arbitrary JavaScript or global-desktop input in this tool vocabulary.

There are at most 32 computer visual-cycle attempts per run, including declined
or failed attempts after admission. Capacity is reserved before an action can
have an effect. Each screenshot retains the 2 MiB bound and private artifact
ownership. The two most recent browser/computer images share the model working
context budget. Live GUI frames are not sent to the model. Provider guidance
combines text retrieval with visual actions as the task requires.

This is an application-owned implementation of the [custom UI tool pattern](https://developers.openai.com/api/docs/guides/tools-computer-use-integration#use-your-own-ui-tools).
The [ChatGPT-plan route limits](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
still apply. Visual operation does not make browser traffic indistinguishable
from manual browsing and does not guarantee that website challenges disappear.

## Browser action consent

Each ordinary supported browser chat has `browser_action_approval:ask|always`, defaulting to
`ask`. **Consenti sempre le azioni dell’agente in questa chat / Always allow the
agent’s actions in this chat** saves `always` immediately from Web settings, even
while a turn is active. A locally issued browser-action decision also offers
**Consenti sempre in questa chat / Always allow in this chat**. That choice
resolves the current decision and saves the same preference. The Browser header
shows when actions are allowed.

The preference covers browser click/type/press and Computer click/double-click/
type/key/drag. Execute mode, project permission, current page/observation checks,
manual ownership and cancellation still apply. Manual login completion, native
passkey prompts, host commands and provider-native approvals retain their existing
contracts. A browser preference never authorizes an operation in another chat.

Revocation is available during work. Automatic consent is re-read immediately
before dispatch, including after manual-control waits; already dispatched actions
are not undone or repeated. An explicit one-action decision applies only to that
action. Changing the preference does not close the browser, resume manual control
or change provider/model/effort. It survives restarting the application in the
same chat; new/forked chats default to asking. Provider/account transitions clear
the grant, including returning to a previously used provider. There is no model
tool for changing this preference and webpage text cannot authorize it.

A policy-only `session.web.update` may run during activity and resolve only
eligible pending browser-action decisions in its own chat. GUI updates include
`expected_generation`, checked before mutation so a delayed choice cannot grant
consent after another client changes the provider/account. Other Web settings
retain idle admission. `run.approve` accepts `accept_always` only for a pending
service-owned action bound to the current browser actor/generation. Its durable
decision notice records `accept_always`, `browser_action_approval:always` and
`scope:chat`; tool results separately establish execution. The GUI's
`allow_always` marker is supplied by that trusted registration, not by provider
approval content. Stale, cancelled or unrelated decisions cannot grant the policy.

## Profiles and manual login

By default each chat browser gets a temporary isolated profile, retained across
turns while that browser remains open. It is removed only after
owned-process cleanup is confirmed. **Conserva il profilo del browser per questa
chat** explicitly selects a persistent private profile for that chat, account
and provider generation. It never adopts cookies, passwords or tabs from a
personal browser. Disabling persistence leaves the retained profile available
for later explicit re-enabling; it does not delete user data.
Chromium retains persistent cookies and site storage according to its normal
rules. Session-only cookies can disappear when the user closes the browser;
such websites may require another manual login. Kira does not override website
expiry or manufacture a valid server-side login session.

The user can ask Kira to open a website for manual login. `browser.manual_login`
pauses automation and displays an inline decision. Enter credentials in the
embedded browser, then use **Ho finito / Done** after completing login. During
an authentication prompt tracked by the Windows bridge, its dedicated banner
replaces the login explanation;
**Ho finito / Done** returns when the prompt ends. **Mantieni il controllo / Keep
control** declines automated continuation while leaving the browser open.
Temporary profiles support login for that browser's lifetime; opt in to
persistence before opening it when eligible login
state should survive browser close. The live view remains available to the user
during the pause, but no model capture runs: form values are excluded from model
text snapshots and form/editable controls are masked in model screenshots.
This is not a universal detector for sensitive information displayed elsewhere
on a page. The browser's retained cookies/profile are private credential-bearing
data and must not be published.

### Passkeys and the Windows authentication bridge

Headed mode exposes Chromium's own authentication UI; it does not create an
authenticator. On Linux, only authenticators actually accessible to this isolated
browser can work. In particular, headed Linux Chromium alone does not provide
Windows Hello or import a personal browser's passkey manager.
Linux-native choosers appear in the full manual surface; their lifecycle is not
observed by the Windows bridge's pending-status banner.

In WSL, Kira can attach its optional bridge when Windows interop, Windows
PowerShell and a supported Windows WebAuthn API are available. Kira's bundled
extension uses Chrome's documented
[`webAuthenticationProxy` API](https://developer.chrome.com/docs/extensions/reference/api/webAuthenticationProxy)
to route supported requests from its owned Chromium actor to the
[Win32 Windows WebAuthn API](https://learn.microsoft.com/en-us/windows/win32/webauthn/-webauthn-portal).
This is Kira's integration using those APIs, not a replacement Windows browser
or a transfer of Chrome cookies, profiles or saved credentials.

A model-requested manual handoff does not authorize a native authentication
operation. The user must explicitly take control or use **Continua autenticazione /
Continue authentication** when offered. Windows owns its secure passkey/PIN/
biometric dialog: it is an operating-system window outside the Linux browser pixels.
The model cannot see or operate that window. PINs, biometrics, native view frames
and WebAuthn request/response bodies are not tool input, conversation content or
durable events. A bounded transient status identifies the origin, provider and
pending/result state; successful credential exchange is distinct from the site's
confirmation that login or registration completed.

The bridge accepts a bounded subset of WebAuthn and preserves the browser-supplied
HTTPS origin and relying-party binding. Cross-origin frames, PRF and other
unsupported extensions/options are rejected rather than silently omitted.
Cancellation, timeout and an unconfirmed native result do not trigger a retry.
Availability depends on the Windows API version and configured authenticators;
the bridge does not guarantee that a particular website's passkey flow works.
Qualify an actual account flow through an explicit user test, separately from
synthetic protocol and native-dialog checks.

## Resource, network and ownership boundaries

- At most two browser workers and four tabs per worker. Worker requests and
  responses are bounded; page text and element lists expose truncation. Up to
  eight embedded frames share the text/element budget and retain source URLs.
  Reads expose at most 80 visible element references. Snapshots stay below the
  client-tool response limit, including UTF-8 text and frame metadata.
- The embedded view uses event-driven CDP page frames or changes on the private
  native display during manual control, capped at 10 fps and 500 KiB
  of encoded image data per frame. The service and GUI retain the latest frame
  instead of queuing every image. Frames are memory-only, are sent only to
  subscribed private clients and never become durable events or model tokens.
  Static pages do not cause periodic metadata or screenshot polling. Hidden views
  stop capturing frames. Model screenshot requests remain separate below.
- Screenshots use the current bounded viewport (up to 1600×1200), 2 MiB each and 16 captures per
  run. The working context keeps at most two recent browser screenshots, replacing
  older pixels with explicit references. Original artifacts remain saved. User
  attachments are separate and are never silently relabeled as screenshots.
- Public HTTP/HTTPS uses ports 80/443 through an authenticated local proxy. It
  validates all DNS answers and connects to a validated numeric address, including
  after redirects. Local/private/special-use addresses, embedded URL credentials,
  non-web navigation and proxy bypass are blocked. Chromium keeps its sandbox and
  normal TLS verification; service workers, QUIC and non-proxied WebRTC are disabled.
- Network concurrency and transferred bytes are bounded. The browser does not
  inherit provider credentials or HTTP proxy variables. These protections scope
  Kira's browser; `trusted_host` command authority retains its existing meaning.
- A worker is registered in a dedicated durable browser process registry before
  initialization. Unconfirmed cleanup blocks further browser use and retains
  affected run reservations. Interrupted actions are never automatically repeated.
  The service owns all tool effects; the GUI submits settings, decisions and
  explicitly authorized manual input and displays the resulting viewport.

## IPC and persistence

`session.web.get {project_id,session_id}` returns settings and a bounded browser
status/preview. `session.web.update` accepts the same identity plus any of
`web_search`, `browser_enabled`, `browser_persistent`; it requires an idle ordinary
OpenAI direct or Ollama chat with no pending queue or overlapping reservations.
The `web_search` mode applies to OpenAI direct; Ollama browser settings control
the Kira-owned browser. Settings are
stored with the active provider generation. Changing them preserves chat history,
draft, selected model and reasoning effort.

Schema 8 adds browser actors and independently owned process records, after a
consistent private backup. Schema-7 tables and rows are retained unchanged.
Existing session settings, tool events, artifact events and private image-attachment
records retain their roles.
Screenshots belong to their generating run/chat through service artifact events;
they are not added to the user's original message attachment list. Checkpoints
store owned image identifiers and restore validated original pixels when needed.
No base64 pixel strings enter ordinary events or SQLite checkpoints.

`session.browser.open`, `watch`, `unwatch`, `command` and `close` are private
desktop APIs. Watch returns a client/actor-bound `input_stream_id`; commands use
that `stream_id`, a monotonic `sequence` and a request UUID. The last 128 receipts
and a high-water mark reject duplicate, stale or uncertain execution even after
receipt eviction. Viewport events use the transient `browser_event` envelope,
separate from the durable run queue. Inputs and frame bytes are never stored in
SQLite, logs or conversations. Service restart closes old owned actors and never
replays pages or browser actions.

Frames carry an ephemeral `surface_id` and `surface_mode` (`page` or `native`).
Manual mouse, key, text and copy commands belong to the surface actually shown.
Changing tab, dimensions or control invalidates old input; queued input is never
relabeled for new pixels. Headed views reject page frames during manual control
and native frames during agent control, including late coalesced frames.
Durable `tool.progress` records contain only call identity/name, the
`waiting_for_browser_control` or `resuming` phase and `manual_control` reason.
They do not contain page data, credentials or authentication payloads, and do
not imply that a tool action has executed.

## Setup and verification

Install the profile-local Chromium engine explicitly:

```sh
kira-desktop browser-install
# From a source checkout:
uv run kira-desktop browser-install
```

Use the same KIRA_HOME/XDG environment as Kira. The command downloads the pinned
engine into the selected data directory's `browser-engine/`; it does not start
inference, change a system browser or remove prior revisions. Source deployments
also need Chromium's Linux system libraries plus `xvfb` and `libxtst6` for the
private native display; native packages declare these dependencies. The
browser is not downloaded silently on chat start or package upgrade.

Run the synthetic suite normally. To include a real sandboxed Chromium check on
a synthetic document with an explicitly selected engine:

```sh
KIRA_TEST_BROWSER=1 PLAYWRIGHT_BROWSERS_PATH=/absolute/path/to/selected/engine \
  QT_QPA_PLATFORM=offscreen uv run pytest tests/test_browser_engine.py tests/test_web_network.py
```

This is browser evidence, not a live OpenAI account/model test. The implementation
uses the [official web search contract](https://developers.openai.com/api/docs/guides/tools-web-search),
[ChatGPT-plan tool limits](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
and [Playwright browser API](https://playwright.dev/python/docs/library).
