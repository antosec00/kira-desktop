# Browser and Kira's Computer

*[Leggi in italiano](../it/BROWSER_E_COMPUTER.md)*

Kira can search the web, read pages and use a real browser, isolated from
yours, alongside the conversation. This guide explains the different forms of
web access, how to set up the integrated browser, how the agent uses it, how
you take and return control, which consents are needed and which network,
resource and privacy limits apply. It is for anyone who wants Kira to work on
web pages and services without exposing their personal browser.

## What they are

Kira offers four different things, which often work together:

| Feature | What it is | Availability |
| --- | --- | --- |
| Hosted web search | The search run by the provider on its own servers, with sources cited in the response | **Direct OpenAI · ChatGPT** only; modes **Live access**, **Indexed content**, **Search disabled** |
| Web search through the browser | The `browser.search` tool: the agent opens a Google search in Kira's isolated browser | Ollama and the other providers with a browser |
| Integrated browser | A dedicated Chromium, with a separate profile, running on a private virtual screen and shown in the **Browser** panel next to the chat | All providers except **Codex** |
| Kira's Computer | Visual control of the integrated browser: the model observes a screenshot and acts by coordinates | Same as the browser, except **DeepSeek Web** |

**Codex** uses its own native tools and does not receive Kira's browser. With
**Ollama** the browser tools are verified at first use with a side-effect-free
probe; screenshots and Kira's Computer are offered only if the selected model
accepts images. **DeepSeek Web** never receives Kira's Computer, and receives
browser screenshots only with a model that accepts images. With **Direct
OpenAI** the chat tools must be enabled for the agent to use the web.

OpenAI's hosted search depends on the model and on your account's
authorizations: a refusal is shown as is, without changing model or payment
mode. **Live access** allows fetching up-to-date pages, **Indexed content**
limits the search to content already available to the provider's service,
**Search disabled** turns it off without turning off the integrated browser,
which is a separate option. This is not a local index: the search happens on
OpenAI's servers.

Sources appear as links in the responses and web operations as rows in
**Activity**. If the provider confirms an operation without reporting a
detail, for example the address of the page opened, Kira leaves it
unavailable: it invents neither sources nor addresses.

## Setup

The browser is not downloaded on its own when a chat starts or the package is
updated. Install it once, with the same profile (`KIRA_HOME` or XDG variables)
the app uses:

```sh
kira-desktop browser-install
# From a source checkout:
uv run kira-desktop browser-install
```

The command downloads a dedicated Chromium engine into the `browser-engine/`
folder of Kira's data. It does not touch the system browser and starts no
inference. If you start Kira from source you also need the system libraries
listed in [Installation](INSTALLATION.md#running-from-source-with-uv), including
Chromium's and the `xvfb` and `libxtst6` packages, which provide the private
virtual screen; the `.deb` packages declare them as dependencies.

Web options are per chat. Open them with **⚙** in the browser panel or with
**Web settings** in the tool tray under the message field:

- **Web search mode**: with Direct OpenAI choose **Live access**, **Indexed
  content** or **Search disabled**;
- **Allow the browser for this chat**;
- **Keep the browser profile for this chat** (see below);
- **Always allow the agent's actions in this chat** (see below).

Search, browser and profile retention are changed while the chat is idle, with
no queued requests. The action consent can be changed during work as well.
None of these choices changes the chat's history, draft, model or reasoning
level.

Check the project instructions too: a network ban written there keeps applying
until you change it, and Kira must explain the restriction to you instead of
saying the tools do not exist.

## How the agent uses it

You do not need to know the tool names. A request such as "read this link and
summarize it", "find the official documentation of this library" or "open the
site and find the contact page" is enough: when you ask it to read, summarize
or verify a page, the agent must genuinely try to open it. The agent opens the
browser when it needs it; the same page and the same tabs stay open between
messages of the same chat.

The tools available to the model are:

| Tool | What it does |
| --- | --- |
| `browser.search` | Opens Google's results for the query in the isolated browser; the model writes the query, which therefore reaches Google |
| `browser.navigate` | Opens an HTTP/HTTPS address |
| `browser.snapshot` | Reads the page's text and structure and returns references to the visible elements |
| `browser.find` | Searches for text or elements in the page |
| `browser.click`, `browser.type`, `browser.press` | Click, typing and key presses on a referenced element |
| `browser.scroll` | Scrolls the page |
| `browser.screenshot` | Captures an image of the view and sends it to the model |
| `browser.tabs`, `browser.select_tab`, `browser.close_tab`, `browser.back` | Tab management and going back |
| `browser.manual_login` | Asks you to sign in to a site in person |
| `browser.close` | Closes this chat's browser |
| `computer.observe`, `computer.act` | Kira's Computer (see below) |

Some rules of use:

- Text snapshots are preferred to screenshots: they are cheaper, more precise
  and work with models without vision as well. Dynamic sites are rendered
  normally.
- Element references expire: after a navigation or a page change, the model
  must read the page again before clicking. It cannot supply arbitrary
  selectors or run JavaScript.
- Reads (navigating, reading, searching, scrolling, screenshots) are also
  available in **Read only** projects and in the **Plan** and **Review**
  modes.
- Clicking, typing and pressing keys require **Execute**, a writable project
  and your consent for that action or for the chat. The consent does not
  guarantee that the site accepts the action: the observed outcome is recorded
  separately.
- Manual sign-in also requires **Execute** and a writable project.
- File upload, automatic downloads and automatic filling of password fields
  are not available.
- If a page cannot be loaded, the tab shows Chromium's error page: the agent
  receives `browser_error_page` instead of content and can go back or open
  another address; during **Take control** the address bar, Back, Forward,
  Reload and tabs keep working. A timed-out operation leaves the browser and its
  tabs open and reports that its outcome is unknown.
- Some sites block automation, ask for cookie consent or show a CAPTCHA. Kira
  must report the obstacle it observed; there is no promise of access to every
  site. If a search engine presents an anti-bot check, a hosted search of the
  same question is a different search, not the personalized view of your
  browser.

If the provider confirms a web operation, you find the matching row in
**Activity**; the private preview of the screenshots requested by the model is
in the browser panel.

## The panel

The **Browser** panel appears next to the conversation. It shows the same page
the agent controls: no separate Chromium window opens.

- **Tabs** and **address bar**: you can type a full address or a bare domain
  such as `example.org` and press Enter; without a protocol HTTPS is used. The
  text you are typing is not wiped by view updates. This convenience does not
  skip the service's address and network checks.
- **Back**, **Forward**, **Reload**.
- The **divider** between chat and browser can be dragged; **Expand browser**
  gives the panel the full width. In narrow windows you switch between browser
  and chat without losing the draft.
- **Take control** pauses the agent's actions and gives you mouse, keyboard,
  Unicode/IME input and explicit copy/paste. The view switches to **Full
  browser** mode: the browser's native surface, including its dialogs (file
  pickers, authentication windows, popups). Password fields are excluded from
  selection copy.
- While an agent tool waits for you to finish, the chat shows **Waiting for
  browser · your control**, with no working animation. Kira never takes the
  browser back on its own.
- **Return to Kira** releases the wait and the agent continues. **Stop**
  cancels the model's request without throwing away a browser that was only
  waiting for you. **Keep control** refuses the automatic continuation and
  leaves the browser open for you.
- A rendering error is shown as such: Kira does not replace the failed native
  view with an image of the page alone.

Hiding the panel, switching area or minimizing Kira suspends the view update
without closing the page. **Close browser** and the panel's **×** button really
close the service's Chromium, wait for the process cleanup and invalidate the
actions still pending. A completed turn leaves the browser open as long as the
Kira window stays connected; when the last window disconnects, Kira immediately
closes the browsers that are idle or under manual control, and the others as
soon as the current turn ends. You can also ask Kira to close it:
`browser.close` concerns only this chat's browser, requires no write permission
and prevents that run from reopening it.
A cleanup with an uncertain outcome stays visible and blocks the opening of a
replacement browser until it is resolved.

Before updating Kira, close the browser or return control and finish the
active work; the full procedure is in [Installation](INSTALLATION.md).

## Action consent

Every chat with a browser has a policy for the agent's actions: **ask** (the
initial value) or **always allow**.

With **ask**, every click, typing or key press brings up the **Decision
required** window: **Allow once** authorizes only that action; **Always allow in
this chat** authorizes it and saves the policy for the chat; **Decline** refuses
it. The same choice is available, with every provider that uses the browser, in
the web settings with **Always allow the agent’s actions in this chat**, which
is saved immediately, even during work. The browser bar shows **actions
allowed** when the policy is active.

The policy covers the browser's clicks, typing and key presses and Kira's
Computer's clicks, double clicks, text, keys and drags. It does not cover:

- the mode and the project permission, which remain required (**Execute**,
  writable project);
- the check that the page or the observation is still current;
- manual sign-in, which always ends with your **Done**;
- passkeys and native authentication;
- commands on the host and the providers' native approvals;
- any other chat: a consent applies only in the chat where you gave it.

You can revoke it at any time from the same settings, even during work: Kira
re-reads the policy immediately before every action, even after a wait for
manual control, so actions not yet sent go back to asking for confirmation.
Actions already executed are neither undone nor repeated. Changing the policy
does not close the browser, does not take back manual control and does not
change provider, model or reasoning level.

The consent survives an app restart in the same chat. A new chat or a branch
starts from **ask**. Changing provider or account resets the consent, even
when going back to a provider used before. There is no model tool to change
this preference and no text of a web page can grant it: the decision comes
only from your buttons.

## Profiles and manual sign-in

Every chat browser starts with a temporary profile, isolated from your
personal browser and kept as long as that browser stays open. It is removed
only after the process closure is confirmed. By enabling **Keep the browser
profile for this chat** you choose instead a persistent private profile,
distinct per chat, account and provider: the chat uses it again whenever it
returns to that provider and account, including after a change of its Kira
identity. Disabling retention does not delete the profile already saved: it
remains available if you enable it again.

Chromium keeps persistent cookies and site data according to its normal
rules. Session-only cookies can disappear when you close the browser, so some
sites ask for a new sign-in even with retention enabled. Kira does not extend
site expirations and does not fabricate sign-in sessions. It never imports
cookies, passwords, tabs or profiles from the browser you use every day.

When a site requires a sign-in, ask Kira to open it for a manual sign-in.
`browser.manual_login` suspends automation and shows a box in the chat. Enter
username, password and codes directly in the site, inside the panel, then
press **Done**. **Keep control** leaves the browser to you without resuming
the agent. With a temporary profile the sign-in lasts as long as that browser:
if you want it to survive closure, enable profile retention before opening
it.

During the pause the view stays available to you, but no capture for the
model is in progress. Form field values are excluded from text snapshots and
editable fields are masked in the screenshots meant for the model. This is not
a universal detector of sensitive data shown elsewhere in the page: when the
agent takes control back, it can read the other visible content. Never write
credentials in the chat: they would go to the provider and stay in the
history.

## Passkeys and the Windows authentication bridge

The integrated browser exposes Chromium's authentication interface, but does
not create an authenticator. On Linux only authenticators actually reachable
from the isolated browser work; native Linux dialogs appear in the **Full
browser** view when you take control. The integrated browser by itself does
not provide Windows Hello and does not import your personal browser's passkey
manager.

In WSL, Kira can enable an optional bridge when Windows interoperability,
Windows PowerShell and a supported version of the Windows WebAuthn API are
available. An extension bundled with Kira forwards the supported requests from
the isolated browser to the Windows API; it does not open a Windows browser
and does not transfer cookies, profiles or credentials from Chrome.

The flow is always in your hands:

1. The site asks for a passkey. A box in the chat shows the site and the step
   requested.
2. You take control or press **Continue authentication** when it is offered.
   A sign-in request made by the agent does not authorize this step on your
   behalf.
3. Windows opens its secure window for **Windows Hello**, PIN or biometrics.
   It is an operating system window, outside the Linux image in the panel:
   the model does not see it and cannot operate it.
4. While the request is in progress, **Return to Kira** is disabled; **Keep
   control** stays available. **Done** reappears when the prompt ends.

PIN, biometric data, view frames and the contents of WebAuthn requests do not
enter the model's tools, the conversation or the recorded events. The bridge
accepts only part of WebAuthn: PRF requests, unsupported extensions and
authentications from cross-origin frames are refused explicitly, not silently
dropped. Cancellation, timeout and an unconfirmed native outcome do not cause
a retry. The box tracks only the requests passed through the Windows bridge;
native Linux dialogs are not followed by this state.

A successful credential exchange is not confirmation that the site completed
the sign-in or the registration: verify the outcome in the site. Availability
depends on the Windows API version and on the configured authenticators; the
bridge does not guarantee that a particular site's flow works.

## Kira's Computer

**Kira's Computer** lets the model use the integrated browser the way you
would while looking at the screen: it observes a screenshot
(`computer.observe`), chooses an action in that image's coordinates
(`computer.act`) and receives a new screenshot after the action. You can ask,
for example: "Open the site, observe the page and use the menu to reach the
contacts section". It needs a model with vision; the chat's browser and the
tools must be enabled.

The supported actions are:

| Action | Details |
| --- | --- |
| Click, double click | On a point of the observed screenshot |
| Move | Moves the pointer without clicking |
| Scroll | In a direction, from a point |
| Text | Enters text in the focused editable control |
| Key | Only keys from an allowed list |
| Drag | A path of 2 to 32 points |
| Wait | At most 1500 ms |

Coordinates belong to the observed screenshot, which reports its own
dimensions: not to your desktop or to the Kira window. Kira's Computer cannot
move the system pointer, use other applications, run commands or JavaScript.

The service accepts an action only with a current observation from the same
run. If the page changes while the model decides (navigation, resize, you
taking control, a different screen), the action on the old screenshot is
refused and the model must observe again. This cautious check can refuse
actions on pages with continuous animations: in those cases text reading or
your intervention is preferable. If an action was executed but the new
screenshot failed, the result says so clearly instead of suggesting a blind
retry; uncertain or duplicate actions are not repeated.

Every request has at most 32 visual cycles, including refused or failed
attempts; when the limit is reached Kira reports it. The original images stay
in the private artifacts, while the model's context keeps only the two most
recent images (browser and Computer together), replacing older ones with
references. Form fields are masked in the images sent to the model. Clicks,
double clicks, text, keys and drags follow the same rules as the browser:
**Execute**, writable project and the chat's action consent. Visual control
does not make the traffic indistinguishable from manual browsing and does not
make the sites' anti-bot checks disappear.

## Resource and network limits

| Scope | Limit |
| --- | --- |
| Browser | At most 2 browsers and 4 tabs per browser |
| Reads | At most 80 references to visible elements per snapshot; up to 8 embedded frames share the text budget and keep their origin address; truncations are flagged |
| Screenshots for the model | View up to 1600×1200, 2 MiB per image, 16 captures per request |
| View frames in the panel | At most 10 fps and 500 KiB per frame; only the last frame is kept, hidden views do not capture, static pages generate no polling |
| Network | Public HTTP/HTTPS only on ports 80 and 443, through an authenticated local proxy of the service |
| Blocked addresses | Local, private and special addresses (even after a redirect), credentials embedded in the address, non-web navigation, proxy bypass |
| Chromium | Normal sandbox and TLS verification; service workers, QUIC and non-proxied WebRTC disabled; if the system prevents the sandbox from starting, the browser does not open and Kira says why, never starting Chromium without it |
| Credentials | The browser inherits neither the providers' credentials nor your environment's HTTP proxy variables |

The proxy validates every DNS response and connects to a verified numeric
address, even after redirects. At most 32 simultaneous connections and 32 MiB
per connection; each browser may make 3,000 requests and transfer 128 MiB per
budget, which restarts with every navigation you or the agent request and
otherwise every five minutes. When a limit blocks or cuts requests, the agent's
page reads report `network_budget_exhausted`. Connections such as WebSockets
stay open while they transfer data and close after 60 seconds without traffic.
These limits concern Kira's browser: with **Trusted host** or **Full access** a
command run by the model has your user's network, as described in
[Permissions and security](PERMISSIONS_AND_SECURITY.md).

Every browser is recorded in a private process registry before it starts.
When the service restarts, old browsers are closed and no page or action is
repeated. The service owns all tool effects: the interface sends settings,
decisions and the manual input you authorize, and shows the resulting view.

## Privacy

- The view frames you see in the panel and everything you do during manual
  control (mouse, keyboard, text, copy) are transient interface data: they do
  not enter the model's context, the draft, the conversation, the database or
  the logs. Text typed in the browser does not end up in the chat.
- The model receives only the screenshots it explicitly requests and the
  observations of Kira's Computer. Those images are saved in the chat's
  private artifacts, with form fields masked, and remain distinct from the
  attachments you add.
- The progress rows recorded during a wait for manual control contain only
  the tool name and the phase: no page data, credential or authentication
  content.
- Retained browser profiles (`browser-profiles/` in Kira's data) contain
  cookies and sign-in sessions: they are credentials. Include them in the
  private backup together with the explicit screenshots, and do not publish
  them or copy them into a repository or a shared export.
- A web page is untrusted data: no text read from the browser can change
  permissions, consents or modes. The general rules are in
  [Permissions and security](PERMISSIONS_AND_SECURITY.md).
