# Account usage

Account quota readings are separate from token counts recorded for an individual
Kira request. A request's token count does not establish the remaining allowance,
price, or next reset of a subscription.

## Codex source

Kira uses the configured Codex App Server's read-only `account/read` and
`account/rateLimits/read` methods. It does not create an inference turn, redeem a
reset, purchase credits, change model settings or modify login configuration.
The bridge checks the exposed account metadata before and after the quota read;
an observed account change or failed read discards the snapshot.

The normalized snapshot contains `source: codex`, `scope: provider_account`, an
UTC `observed_at`, account display metadata, and at most 16 quota groups. Each
group retains up to two reported windows with used and remaining percentages,
duration in minutes, and a reset timestamp in Unix seconds. Reported zero values
remain zero; missing or invalid values are null. The multi-group response takes
precedence over the legacy single-group response. Omitted groups are counted.

Window names must derive from their reported duration, not from their position.
Remaining percentage is `100 - usedPercent` only for a valid percentage. A reset
timestamp or a percentage is not proof that a new request will be admitted.
Credit balance is a provider-supplied string without an inferred currency or
price. Absent credit fields do not mean zero or unlimited credit.

These values describe the account authenticated in Codex. The optional account
identifier comes from the usage response. `account/read` does not expose a stable
identifier, so its metadata checks cannot detect every same-email workspace
change. Kira does not equate a Codex account with a separately registered direct
account based on email or plan.

The implementation uses the rate-limit contract available in Codex 0.162.1.
It does not depend on the newer `account/usage/read` activity endpoint. See the
[official App Server documentation](https://developers.openai.com/codex/app-server/).

## Direct ChatGPT plan source

The documented Sign in with ChatGPT flow provides a link to
[ChatGPT Settings → Usage](https://chatgpt.com/settings/usage) for app usage,
weekly app limits and credit permissions. The reviewed integration documentation
does not provide a direct endpoint for reading those app-specific quota settings.
Kira must not substitute the Codex account's allowance or infer these values from
the direct backend's response token counts.

The `subscription_sharing_usage_limit_exceeded` error may describe an app limit;
it does not establish that the entire plan is exhausted or specify a reset time.
See [tracking usage](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions#tracking-usage)
and [errors and recovery](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery).

Synthetic tests verify normalization, account-change handling and protocol
failures. They are not evidence that a particular account returned quota data.
Current qualification belongs in [STATUS](STATUS.md).

## Claude Code source

Claude Code derives its own usage indicator from the rate-limit headers of its
API responses and forwards them to the Agent SDK as a `rate_limit_event`:
status, representative window, reset time and the `unifiedWindows` percentages
of the five-hour and seven-day windows (Opus and Sonnet weekly windows when the
plan reports them). Kira's `claude_code` backend records that notice as an
account-scoped usage event (`measurement: plan_limits`, `scope: account`) of the
run in which it was observed, bounded to the known window names, statuses and
numeric ranges. `backend.usage {backend: claude_code}` returns the latest
recorded notice normalized to the shared limits shape — one group, one window
per reported Claude window with used/remaining percentages, duration and Unix
reset time, and the extra-usage status as the group's credits entry — together
with the seven-day local records.

Kira never reads Claude Code's credentials, never calls a usage endpoint and
runs no inference to refresh the view: a notice is as fresh as the last turn
that produced it. The observation time is therefore always shown, and a window
whose reset time has passed is presented as reset instead of with its old
percentage. Account identity (address, organization, plan name) is not part of
the notice and is not displayed. While a Claude Code chat is selected, the
composer footnote summarizes the last notice (`Piano Claude · sessione X% ·
settimana Y%`); it is filled by the run events of the current GUI session and by
the panel's explicit refresh. Selecting a provider reads nothing, and nothing polls.

## Service and local records

`backend.usage {backend,account_id?}` is a read-only IPC operation. `account_id`
selects a direct registration only; it cannot select or impersonate a Codex
account. Reads do not create model runs, tools, billing mutations or usage events.
Provider settings transitions and service shutdown block new provider reads.

Direct, Ollama and Claude Code views include measurements recorded by this Kira
profile over the preceding seven days; account-scoped plan notices are not request
measurements and are skipped by the tally. They count runs and distinct reported generation rounds;
the latest measurement for each `(run, round)` replaces repeated notifications.
Additional context-maintenance requests are counted separately and included in
the reported totals, including completed measurements before a later maintenance
failure. Manual maintenance does not create a fake chat run. Cached input is part
of input, never added to the total. Missing token fields stay
unknown. Immutable provider bindings retain the account of historical runs;
legacy rows use session settings only when that session has never changed account
or provider. Unattributable records are not reassigned to today's active account.

The query inspects at most 10,000 recent usage events and discloses incomplete
coverage or truncation. The period, observation time and selected account remain
visible. These records exclude activity in other applications and are never used
to calculate a subscription percentage, credits, prices or a reset time.

## Interface and external links

**Manage usage** opens Kira's usage dialog. Choosing a provider inside that dialog
only selects the account/source being inspected; it does not change the chat's
provider. Refresh is explicit, with coalesced requests and stale-response guards.
Account changes and disconnection discard old account readings. Quota windows,
unknown values, dates and balances use the active interface language.

The official management page remains available with **Open ChatGPT** and
**Copy link**. The browser uses its own signed-in account, which may differ from
the account selected in Kira. Web links, sign-in and usage buttons share a browser
launcher. On WSL, a failed native launch can use the Windows default browser via
a bounded asynchronous process; no desktop packages or system associations are
changed. Failure remains visible, including inside welcome/settings dialogs.
A successful launch means the operating system accepted the request; it does not
prove that the remote page loaded. URLs are passed to the Windows launcher as
stdin data, never interpolated into commands or included in process arguments.
