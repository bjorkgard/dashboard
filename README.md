# Dayboard

A local-first terminal workspace for your Mac: a single place for your day, your
machine, your development work, and your AI activity.

Dayboard is built with Python and [Textual](https://textual.textualize.io/).
The long-term dashboard will combine calendars, reminders, email, Mac telemetry,
weather, a flip clock, GitHub activity, AI usage, and a clickable Apple-news ticker.

**Dayboard v0.2.0 adds the first live integrations:** opt-in macOS Calendar and
Open-Meteo weather, automatic CPU/memory/network telemetry, and selected-repository
GitHub.com open-PR and recent-default-branch commit status.
Advanced hardware sensors and the other account integrations remain planned.
Demo values are synthetic, not readings from your Mac or accounts.

**New in v0.3.0:** an [in-dashboard Settings screen](#in-dashboard-settings) provides city
lookup, Calendar authorization and GitHub editing, with live application of saved
preferences. The existing v0.2.0 integrations remain supported.

**New in v0.4.0:** Overview, Schedule, Mac and GitHub tabs provide expanded views of
the existing live sources. v0.3.0 retains the single overview.

**New in v0.5.0:** opt-in Claude Code local usage and a fifth AI tab, with
metadata-only accounting and explicit local-source coverage.

**New in v0.6.0:** Apple Mail headers and Inbox (`6`), independent
Codex/Kiro/Warp-Oz modules, multi-source AI rendering, and recent-first Claude indexing.

## What works today

- A terminal workspace with a ticking local clock and foundation-preview cards.
- An isolated `--demo` mode with changing synthetic readings.
- First-run onboarding that can save an unconnected workspace or quit without saving.
- In-dashboard Settings with city lookup and live Save/Cancel configuration.
- Keyboard/mouse tab navigation and expanded agenda,
  Mac telemetry history, and bounded GitHub PR/commit detail views.
- Strict, versioned configuration with owner-only permissions and atomic writes.
- A macOS Keychain credential wrapper with no plaintext fallback.
- Provider contracts, independent asynchronous collection, timeouts, retry backoff,
  last-good snapshots, and bounded history/public-cache infrastructure.
- Redacted `doctor` diagnostics that do not read credentials.
- Read-only macOS Calendar events and location-configured live weather.
- Automatic local CPU/RAM/network readings and short live histories.
- Selected-repository GitHub PR previews, draft/review/check states,
  and recent default-branch commits, using GitHub CLI's existing authentication.
- Opt-in Claude Code local token categories, observed sessions and model summaries,
  with bounded read-only collection and an in-memory ledger.
- Apple Mail header previews, Codex local/optional limit reads, observed-format Kiro
  credits and documented Oz run usage, with independent source states and setup.
- Unit, headless UI, and real pseudo-terminal startup/shutdown tests.

Direct cloud account login, the animated flip clock, GPU/temperature graphs,
Google/Microsoft calendar connections, direct Gmail/Graph/IMAP and task providers,
GitHub issues/notifications/personal review queues, additional AI formats/quotas,
and the [aapl.io](https://aapl.io) news ticker
are planned work. Calendars already synced with macOS use the OS's existing accounts
after explicit permission; new cloud-account login is not implemented in this slice.

## v0.6.0 release quick start

Use this path to install the published release without cloning the repository.
You need macOS, an interactive terminal, Python 3.13+ and uv 0.13.7+
([prerequisites and uv installation](#requirements)).
The wheel and source archive are Python packages, not a signed/notarized standalone
macOS application. No PyPI publication is configured.

### Download and install

Download these three files from the [v0.6.0 release](https://github.com/bjorkgard/dashboard/releases/tag/v0.6.0)
into the same directory:

- `dayboard-0.6.0-py3-none-any.whl`
- `dayboard-0.6.0.tar.gz`
- `SHA256SUMS`

In that download directory, verify both distributions before installing the wheel:

```zsh
shasum -a 256 -c SHA256SUMS
uv tool install --python 3.13 ./dayboard-0.6.0-py3-none-any.whl
dayboard --version
dayboard --demo
```

Both checksum entries should report `OK`, and the version should be `dayboard 0.6.0`.
Stop if verification fails. Checksums detect corruption, not publisher identity or
code-signing status. Demo mode uses synthetic data and does not open real accounts,
provider sources, Keychain or saved application state.

If `dayboard` is not found, run `uv tool update-shell` and open a new terminal.
The uv tool installation is separate from a checkout's `.venv`; use `dayboard` for
the installed release and `uv run --locked dayboard` for the current source checkout.

### Upgrade from an older release

Quit your running Dayboard instance before upgrading. If it exists, copy
`~/Library/Application Support/Dayboard/config.json` to a private backup location
outside the repository and protect the copy with owner-only access. Do not export
credentials or copy provider session files as part of this backup.

**Compatibility:** v0.6.0 reads older schemas in memory, preserving existing
integration choices and leaving new sources disabled. An explicit Save or first-run
setup writes **schema 5**, which v0.5.0 and earlier cannot read. Downgrading requires
restoring a compatible preference backup; reinstalling an older package alone is not enough.

After downloading and verifying the v0.6.0 assets as above, replace the existing tool:

```zsh
uv tool install --reinstall --python 3.13 ./dayboard-0.6.0-py3-none-any.whl
dayboard --version
dayboard --demo
```

Installation does not enable new integrations, grant permissions or rewrite preferences.
Preview first; start normal mode only when ready to use your saved configuration:

```zsh
dayboard
```

### First-run setup and everyday use

On a new profile, choose **Configure** to open Settings, **Continue** for an
unconnected workspace, or **Quit** without saving. Existing profiles load their
saved settings. CPU/RAM/network telemetry starts automatically in normal mode.

Press **s** from any workspace tab, configure only the sources you want, and use
**Save / Ctrl-S** to apply without exiting. **Cancel / Escape** discards form edits.
Settings Save restarts collectors and rebuilds in-memory AI history, so allow a
brief warm-up rather than repeatedly saving while indexing.

- [Calendar and weather](#calendar-and-weather-setup): explicit Calendar authorization
  and a selected weather location; no automatic geolocation.
- [GitHub](#github-prs-and-recent-commits): existing GitHub CLI login and one to five repositories.
- [Claude](#claude-code-local-usage-v050): confirmed local source and timezone; no API key.
- [Mail, Codex, Kiro and Warp/Oz](#apple-mail-and-remaining-ai-setup-v060):
  explicit source/permission/key setup, with format and coverage limits described below.
  Mail/AI setup is in Settings, not additional email/AI CLI subcommands.

Use **1 Overview**, **2 Schedule**, **3 Mac**, **4 GitHub**, **5 AI** and **6 Inbox**.
**Tab / Shift-Tab** moves focus; **Left / Right** switches tabs when the tab bar is
focused; arrow keys and **Page Up / Page Down** scroll a focused region.
Press **?** for help and **q** to quit from a workspace tab.

### Troubleshooting setup

- **Wrong version or executable:** run `command -v dayboard`, `uv tool list` and
  `dayboard --version`. A globally installed tool and `uv run --locked dayboard`
  may intentionally use different versions.
- **Not connected:** the source is disabled or has no registered collector. Enable
  the desired source and Save; selecting a workspace tab does not connect an account.
- **Indexing / partial:** leave the dashboard open while bounded collection progresses.
  Caps and sampled sources can prevent complete coverage. Restarting or saving
  rebuilds memory-only history; it does not recover deleted provider logs.
- **Unknown / unsupported:** missing counters, baselines or unrecognized formats are
  not zero usage. Check the source-specific compatibility notes and diagnostics;
  do not work around this by copying credentials or editing provider databases.
- **Mail closed or denied:** open Mail yourself and use explicit authorization in
  Settings. Background collection neither launches Mail nor requests permission.
- **Codex limits or Warp unavailable:** check the separate opt-in, existing Codex
  login or Dayboard-owned Warp key, selected scope, network and Keychain status.
  Local usage, account quotas and Oz run previews are separate capabilities.

For allowlisted local diagnostics:

```zsh
dayboard doctor --json
```

This checks configuration/backend availability without reading credentials, Mail or
AI source contents; it does not prove provider connectivity or unlocked Keychain access.
An absent configuration is expected before first-run setup.

## Run from a source checkout on a Mac

### Requirements

- macOS and a terminal with an interactive TTY.
- Git.
- [uv](https://docs.astral.sh/uv/getting-started/installation/), version **0.13.7
  or newer**.
- Python **3.13 or newer**. The commands below install Python 3.13 through uv.

Demo mode and weather do not require Xcode, administrator access, API keys, or
Calendar/Mail permissions. Native Calendar and Mail modules require macOS 14+ and Swift
Command Line Tools, plus explicit Calendar/Automation permission respectively.
Git may prompt you to install Apple's Command Line Tools if it is not already available.

### 1. Install uv

If you use [Homebrew](https://brew.sh/):

```zsh
brew install uv
uv python install 3.13
```

Without Homebrew, use the [official uv installation instructions](https://docs.astral.sh/uv/getting-started/installation/),
then run `uv python install 3.13`.

### 2. Clone and install

```zsh
git clone https://github.com/bjorkgard/dashboard.git
cd dashboard
uv sync --locked --python 3.13
```

For the exact published source rather than the latest branch, optionally run
`git switch --detach v0.6.0` and rerun `uv sync --locked --python 3.13`.
The installed release and source
checkout use the same default preferences, so the schema-5 backup warning above
also applies when saving from source.

Dependencies are installed in the project's `.venv`. You do not need to activate
it or install Python packages globally. The initial install needs internet access.
Normal mode reads local CPU/RAM/network counters without making network requests.
After weather setup, normal mode also contacts Open-Meteo; disabled account/weather
sources and demo mode make no provider requests. Enabled GitHub status makes a
bounded read query through GitHub CLI about once per minute.
Opt-in Warp/Oz and Codex account-limit reads can also contact their configured services
about every five minutes; local Claude/Codex/Kiro readers do not make provider requests.

### 3. Start Dayboard

Preview the interface without accessing accounts, Keychain, or application state:

```zsh
uv run --locked dayboard --demo
```

Start the normal workspace:

```zsh
uv run --locked dayboard
```

On first launch, use **Configure** to set up integrations, **Continue** to proceed
without cloud accounts, or **Quit** without saving. (v0.2.0 uses the longer
Continue without accounts / Quit without saving labels.) Only explicitly configured
sources become live; normal mode never substitutes synthetic data. Basic local telemetry
is available automatically.

### Workspace navigation (v0.4.0+)

Dayboard includes six workspace tabs (AI requires v0.5.0+; Inbox requires v0.6.0+):

- **1 — Overview:** clock, weather and compact integration cards. Agenda previews
  remain limited to six events; GitHub previews show up to five PRs and five commits.
- **2 — Schedule:** the existing seven-day Calendar window, expanded to show all
  returned occurrences (up to 100), with source labels, local dates/times, all-day
  markers and explicit truncation when reported by the helper.
- **3 — Mac:** CPU/RAM/network graphs from the same bounded sample history, plus
  collected metric values. Dots mean unknown samples; VPN/bridge traffic can be
  counted more than once. Disk, battery, processes and advanced sensors are not added.
- **4 — GitHub:** grouped PR and recent default-branch commit lists from selected
  repositories, including draft/review/check/merge states and partial-access notices.
  The existing query returns at most ten PRs and five commits per repository
  (up to 50 PRs and 25 commits across five repositories); this is not a complete
  account-wide feed or personal review queue.
- **5 — AI:** Claude Code local usage, calendar-window totals and model summaries,
  with explicit partial-coverage and indexing indicators. v0.6.0 adds independent
  Codex local/limits, Kiro credits and Warp/Oz run sections.
- **6 — Inbox (v0.6.0+):** bounded Apple Mail header preview.

Press **1–6** or click a tab.
**Tab / Shift-Tab** moves focus; **Left / Right**
switches tabs when the tab bar is focused. Focus a scroll region to use arrow keys
and **Page Up / Page Down**; number shortcuts focus the selected region directly.
The overview uses one column below 100 terminal columns and two on wider terminals.

All views share one collector registry, scheduler and snapshot store. Switching
tabs does not issue requests, restart collectors or reset history. Source errors
retain last-good data with stale/error indicators and observation timestamps.
Disabled sources show Not connected with a Settings hint rather than sample data.

Open Settings with **s** or the visible button from any tab. Save and Cancel return
to the selected tab; Save resets old-scope readings in both overview and detail
views. Number keys remain normal text input in Settings and do not navigate
underneath onboarding. Tab selection is session-only, not saved as a preference.
Demo tabs use synthetic data only; no new permissions or credentials are required.
v0.5.0 introduced the Claude AI tab. v0.6.0 adds Mail and the other AI modules
described below.

### Apple Mail and remaining AI setup (v0.6.0+)

Back up preferences before upgrading: v0.6.0 writes **schema 5** on Save,
which v0.5.0 and earlier cannot read. New sources are disabled when older preferences
migrate in memory; existing source choices remain preserved. Open **Settings (`s`)**
to opt in, then **Save / Ctrl-S** to apply. Cancel discards form edits.

#### Apple Mail

- Uses accounts already configured in macOS Mail, not separate Google/Microsoft/IMAP
  logins. Open Mail yourself; background collection never launches it.
- Click **Authorize Mail Automation** explicitly to build the owned helper and request
  access. Requires macOS 14+ and Swift Command Line Tools. The OS permission is broader
  than the fixed read-only operations Dayboard performs; Cancel does not revoke a grant.
- Click **Discover mailboxes**, select up to five, enable Mail and Save.
  Discovery is bounded to 20 accounts, 200 mailbox entries and eight levels, so it may
  not show every mailbox. Previously saved selections remain available for deselection.
- Inbox (`6`) samples up to 20 candidate headers per mailbox and 50 combined, sorted
  by received date within the sample—not guaranteed globally newest mail. Overview
  previews five. Sender/subject/date/read state and source labels remain in memory.
- No bodies, raw source, attachments, password reads, message opening, marking read,
  deletion, moving or synchronization commands. Closed/denied/unavailable Mail is
  reported safely; failed collection retains last-good headers marked stale.

#### Codex

- Confirm the Codex root (normally `~/.codex`; `CODEX_HOME` may be offered as a candidate),
  timezone, and enable local usage. Only `sessions` and `archived_sessions` rollout
  JSONL are read; credentials, response text and tool arguments are not retained.
- The tested UUID-session/token-count profile derives event deltas from cumulative
  counters, handles resets and replay, subtracts cached input from inclusive input,
  and does not add reasoning on top of output. Missing initial baselines are labelled
  partial; unknown models and unsupported formats are not guessed. Cache writes stay unknown.
- **Codex account-limit reads** is a separate opt-in. It uses an owned stdio app-server
  with initialization and account-rate-limit reads only, about every five minutes.
  Codex must already be installed and signed in separately. It owns authentication
  and may maintain its own state; Dayboard never copies its tokens or starts a turn.
- Multiple limit windows remain separate, with nullable reset/duration values and fresh
  observation timestamps. Quota failure does not disable local token collection.

#### Kiro

- Confirm a sessions root such as `~/.kiro/sessions`, timezone, and enable.
- The implemented observed profile is `<workspace-hash>/sess_<uuid>/messages.jsonl`
  with V3 `usage_summary`/execution IDs and reported credit units. Revisions replace
  earlier per-execution observations; decimal credits are never inferred from context,
  token windows, response sizes or message text.
- Local recorded-credit windows are not account balances. Tokens and model attribution
  remain unavailable; `session.json` is deliberately not read.
- CLI-header and SQLite-only formats are unsupported. A CLI version alone does not
  prove format compatibility: the public example used CLI 2.16.1 `--v3`, while this
  Mac's researched CLI was 2.31.1 and current web documentation describes 3.x.
- No auth tables, private ACP commands, chat `/usage` probes, token extraction,
  billable prompts, login or automatic upgrade.

#### Warp / Oz

- Enter a Dayboard-owned API key in the **masked** field, optionally select a team UID,
  then use **Test Warp connection** explicitly. Test reads do not save preferences/keys;
  changing the draft during a test invalidates its result.
- Enable and Save to store the key in **macOS Keychain only**, with a nonsecret
  reference in preferences. Opening Settings does not read Keychain or call the API.
  Locked/unavailable Keychain fails closed; there is no plaintext/env/CLI-token fallback.
- The transport only sends fixed-host GET requests to the documented Oz run-list API,
  at most 25 runs per page, about every five minutes. It does not control/start runs,
  read Warp desktop databases, use private GraphQL, or retain prompts/titles/artifacts.
- Per-run/conversation tokens, separate inference/compute/platform credits and USD
  observations may overlap across resumed and parent/child runs. They are **not summed**
  into account totals. USD may be billed or fallback estimates; API source time can lag.
  Desktop-wide usage and remaining account credits remain unavailable.
- A key may have broader permissions than Dayboard uses. Key rotation stages a new
  credential so failed preference writes do not overwrite the working key. Referenced
  keys are preserved if a late write failure leaves commit outcome uncertain.
  Prior Dayboard key references stay bounded for explicit cleanup.
- Disabling preserves keys. **Disconnect** requires confirmation, saves disabled state,
  stops the Warp poller before Keychain cleanup, and removes only Dayboard-owned keys.
  Cleanup failure leaves Warp disabled and references available for retry; unrelated
  Oz/Warp credentials and provider preferences are unchanged.

All these modules share the existing scheduler/store but render separately. Kiro credits,
Warp credits/USD, Codex quotas and local token categories are not interchangeable.
Demo uses synthetic examples without private-source, credential, API or permission access.
Live authorization and account/source validation remain explicit user actions; automated
verification uses fixtures, fake Keychain and mocked transports only.

### Claude Code local usage (v0.5.0+)

1. Run Dayboard v0.5.0+ in normal mode, then press **s**.
2. Read the Claude privacy notice. Confirm a single absolute Claude root containing
   `projects/` (normally `~/.claude`; a single `CLAUDE_CONFIG_DIR` value can be offered
   as a candidate). There is no automatic search of alternative locations.
3. Choose an IANA aggregation timezone, such as `UTC` or `Europe/Stockholm`.
4. Enable **Claude local usage** and press **Ctrl-S**. Only this explicit Save validates
   the source directory and starts collection; opening Settings/Cancel does not scan it.
5. Press **5** for AI details. The overview shows compact today counters.

The source JSONL files contain conversations as well as usage. The reader transiently
reads bounded lines but copies only allowlisted IDs, timestamps, model/version and token
metadata into an in-memory ledger. It does not retain prompts, responses, tool arguments,
raw lines, project paths in usage records, or account credentials. It never edits Claude
files/settings, changes their permissions, installs hooks, launches Claude or sends
network/inference requests. Symlinks, hard links, nonregular files and unexpected ownership
are rejected rather than followed; the source must be a directly accessible owned tree.

The observed Claude Code 2.x `message.usage` profile is compatibility-tested, not a
vendor-guaranteed transcript API. Input, output, cache writes and cache reads are separate;
missing categories remain Unknown. Repeated message identities are reconciled across
files/forks, coherent streaming revisions replace partial counters, and conflicting
records are quarantined. Distinct subagent requests are included. Usage covers only the
selected local source, not claude.ai, other machines or a verified account identity.

AI details show today and the last 7/30 calendar dates in the saved timezone, observed
sessions (not active processes), top-ten models by observed request count and a remainder.
Latest usage activity is separate from source-check freshness. Cost, subscription
consumption and account quotas are **unavailable**, never inferred from tokens.

The ledger/cursors are memory-only: every restart or successful Settings Save reindexes;
deleted source history cannot be recovered. Collection targets five seconds; directory
rediscovery targets 30 seconds. Initial limits are 5,000 files, eight directory levels,
20,000 discovery entries, 8 MiB/two seconds of scan work per pass, 1 MiB per JSONL record,
and 100,000 canonical requests over 30 calendar dates. Large backlogs index incrementally.
v0.6.0 indexing prioritizes recent file mtimes within the bounded discovery scope,
not guaranteed event recency. Claude reserves one bounded recent-tail preview while
ordered backfill continues; identity reconciliation prevents replay double counting.
Original-date metadata is separately bounded so old-only history cannot block fresh usage.
Unfinished directory discovery resumes on the next pass, including within a directory;
the rediscovery interval starts only after that traversal finishes. An inaccessible
project does not block readable siblings. At most one directory iterator remains open
between passes, and cancellation/disable/shutdown closes it.
Caps, skipped/unsupported records and conflicts produce explicit partial/observed-so-far
indicators, not claims of complete history. Diagnostics persist for the reader lifetime;
Save/restart resets them while rebuilding history. Even a successfully scanned window
without normalized usage remains unknown; zero requires actual recorded evidence.

Disable Claude in Settings and Save to stop reading and clear its in-memory state.
The source/timezone preferences remain saved; Claude's own logs are never removed.
`dayboard doctor` reports enablement without scanning the source or printing its path.
Demo AI data and Settings remain isolated and synthetic. Live verification of private
sessions is user-directed and is not claimed by the synthetic test suite.

### In-dashboard Settings

In v0.3.0 or newer, press **s** or click **Settings [s]** on the overview.
The first-run **Configure** action opens the same screen.

#### Quick setup

1. Start normal mode with `uv run --locked dayboard` from this checkout, or
   `dayboard` if installed from a release. Demo Settings are read-only.
2. Open Settings with **s**, the **Settings [s]** button, or first-run **Configure**.
3. Set up only the sources you want:
   - **Weather:** search for your city and select the correct region/country result,
     or enter coordinates manually; choose the timezone and units, and enable Weather.
   - **Calendar:** click **Authorize Calendar**, review the macOS permission request,
     and enable collection after access is granted.
   - **GitHub:** enable status and enter up to five `OWNER/REPO` selections. If
     necessary, authenticate separately with GitHub CLI as described below.
   - **Claude:** review the privacy notice, confirm the local source and timezone,
     then enable collection. No login or API key is required.
   - **Mail / Codex / Kiro / Warp:** follow the
     [v0.6.0 provider setup](#apple-mail-and-remaining-ai-setup-v060) for explicit
     mailbox/source selection, optional limits or masked Keychain setup.
4. Click **Save [Ctrl-S]** or press **Ctrl-S**. The overview stays running while
   collectors restart with the new settings; CPU/network rates briefly warm up.
   To discard edits instead, use **Cancel [Esc]** or **Escape**.

Disabled sources keep their previously saved location/repository choices. Saving
does not start a login or silently request Calendar access.

#### Settings behavior and safety

- **Weather:** type a city/postal code, press Search or Enter, then explicitly select
  a city/region/country result. No result is selected automatically and no IP geolocation
  is used. Coordinates, display name, IANA timezone and units can also be entered manually.
  Search sends the typed query to Open-Meteo only on an explicit action; GeoNames-based
  results and the public endpoint's noncommercial terms apply.
- **Calendar:** opening Settings checks status without compiling a helper or requesting
  access. **Authorize Calendar** explicitly builds/requests permission; **Save** is still
  required to enable collection. Cancel can stop the operation but does not undo an OS
  permission grant already accepted. Existing calendar-ID filters remain unchanged.
- **GitHub:** enable status and enter one to five `OWNER/REPO` selections separated by
  commas or spaces. Authentication remains owned by `gh`; no credential fields or silent
  login are introduced. Saved enablement and current readability are shown separately.
- **Save / Ctrl-S:** validate and persist preferences, stop old collectors, reset their
  in-memory readings, and start the saved sources without exiting. CPU/network rates warm
  up again; data from an old location/filter/repository scope is never relabelled as new.
- **Cancel / Escape:** discard edits and return to the overview (or first-run screen).
  Search/authorization tasks and owned subprocesses are cancelled. During the short
  atomic save, duplicate submissions and Cancel are disabled to avoid racing a write.

Disabled sources preserve their saved location/repositories rather than saving incomplete
inputs. Integration edits detected when saving cause a conflict message instead of
silently replacing those changes; Cancel and reopen to load the latest values.
Failed writes leave the form open and the running sources unchanged. If sources cannot
restart after a successful write, old collectors stay stopped and the app reports it.

Actions remain visible in small terminals; Tab/Shift-Tab navigate, Enter activates, and
typing `q` in a text field does not quit the app. Settings in demo mode are read-only:
no file access/saves, city requests, native status checks or permission prompts.
The existing CLI setup commands remain supported and still require restarting a running
dashboard to pick up CLI-only changes.

### CPU, memory and network monitoring

Run `uv run --locked dayboard` for the live **Mac pulse** card. No sudo, Swift helper,
cloud account, or extra macOS permission is required. Sampling runs about once per
second on a worker thread and stops when the dashboard exits.

- **CPU:** aggregate utilization from cumulative CPU-time deltas, normalized to 0–100%.
- **RAM:** total minus OS-available memory, with available/total amounts. Available memory
  includes memory the OS can reclaim; this is not the same as macOS memory-pressure status.
- **Network:** upload/download bytes per second, derived from per-interface counter
  changes and monotonic elapsed time. Units use powers of 1024: KiB/s, MiB/s, etc.
- **Graphs:** short, bounded recent-sample histories. CPU/RAM graphs use a fixed
  100% scale; upload/download graphs adapt to their recent peak. Dots represent unknown
  samples, not zero usage.

The first CPU/network sample establishes a baseline. Counter resets, interface changes,
and gaps longer than ten seconds reset that baseline rather than producing spikes.
Unavailable groups remain unknown without hiding other working groups.

Network totals include all active non-loopback interfaces, not only the default route.
VPNs, tunnels and bridges can count the same traffic more than once; this is host
interface throughput, not an internet speed test or per-app bandwidth meter.
Dayboard does not inspect IP/MAC addresses, connections, packet contents, or processes,
and does not issue network probes. Telemetry history stays in memory and is not uploaded.
Demo mode never constructs the real telemetry provider.

### GitHub PRs and recent commits

Install [GitHub CLI](https://cli.github.com/) and sign in explicitly if you have not
already done so:

```zsh
brew install gh
gh auth login --hostname github.com --web
```

Use GitHub CLI's secure credential storage; do not use `--insecure-storage`.
If gh reports a plaintext-storage fallback, resolve Keychain access before connecting.
Dayboard delegates authenticated requests to gh; it never reads `gh auth token`,
copies credentials into its config, or rotates/modifies your login. The gh account
may have broader permissions than this dashboard uses; Dayboard sends read queries only.

Select one to five repositories using repeatable `--repo OWNER/REPO` arguments:

```zsh
uv run --locked dayboard github set --repo bjorkgard/dashboard
uv run --locked dayboard github status
uv run --locked dayboard
```

`github set` saves only repository names and enablement. `github status` shows that
local configuration, not a connectivity/authentication result. Neither command logs
in or contacts GitHub. Restart the dashboard after changing settings.

The **GitHub attention** card shows:

- The server-reported open PR count for accessible selected repositories.
- Up to five recently updated open PRs across those repositories, including drafts,
  authors, review decision, CI/check rollup, and merge state where GitHub reports them.
- Up to five recent commits across selected repositories, drawn from each five-commit default-branch
  history, with shortened SHA, headline, author, and commit time.
- Default branch names, explicit preview scope, and unavailable-repository counts.

This is a repository overview—not a list limited to PRs authored by you, a personal
review-request queue, an all-branches activity feed, or local unpushed commits.
Each request reads at most ten PRs and five commits per selected repository; the card
labels its preview rather than treating displayed rows as the complete server count.
Absent review/check values remain **UNKNOWN**, never assumed successful.
Partial access is shown; if nothing can be read, the card reports a setup/access error.
Failed refreshes retain last-good snapshots with a stale indicator. Reported API rate
limits defer further requests instead of polling aggressively.

The initial adapter supports **github.com only**, uses one bounded GraphQL read query
per refresh, and never merges PRs, writes comments, pushes commits, opens a browser,
or starts a login automatically. Enterprise hosts, multi-login account selection,
issues, notification queues, personal review requests and item-opening actions remain
roadmap work. Returned titles/metadata stay in memory; no PR bodies, patches or email
addresses are queried. Demo mode never constructs this reader.

Disable collection without changing GitHub CLI's credentials:

```zsh
uv run --locked dayboard github disable
```

### Calendar and weather setup

These commands require v0.2.0 or newer. The examples below use a source checkout;
with an installed release, replace `uv run --locked dayboard` with `dayboard`.

For weather, choose your own coordinates and IANA timezone. This example uses
Stockholm as an explicit example location, not an automatically detected position:

```zsh
uv run --locked dayboard weather set \
  --latitude 59.3293 --longitude 18.0686 \
  --name Stockholm --timezone Europe/Stockholm --units celsius
uv run --locked dayboard
```

Weather refreshes about every 15 minutes, showing current model-derived conditions,
apparent temperature, humidity, wind, and a four-hour outlook. It uses the
[Open-Meteo](https://open-meteo.com/) public endpoint with
[attribution and usage terms](https://open-meteo.com/en/terms); that endpoint is for
noncommercial use. Commercial deployment needs a separately supported licensed
configuration, not silent reuse of the free endpoint. No API key or IP-based
geolocation is used. The label, coordinates and units are stored locally; the request
sends the configured coordinates/timezone/units to Open-Meteo.

For macOS Calendar:

```zsh
xcode-select --install  # only if Swift Command Line Tools are missing
uv run --locked dayboard calendar authorize
uv run --locked dayboard calendar status
uv run --locked dayboard
```

`authorize` explicitly compiles the bundled helper and requests Calendar access.
macOS may attribute that permission to your terminal/host application. If access
is denied, review the host's Calendar privacy permission; hosts that cannot request
EventKit access are not bypassed. macOS grants full Calendar access for this API,
but Dayboard performs read-only operations.

The agenda refreshes about every minute, expands recurring occurrences through
EventKit, includes all-day events/source labels, and displays up to six events from
a bounded seven-day window. At most 100 occurrences are transferred per collection;
truncation is shown explicitly. All authorized calendars are included by default.
An optional repeatable `--calendar-id` on `calendar authorize` filters known calendar
identifiers before the native result cap. Calendar contents stay in memory.

Disable collection independently and restart the dashboard to apply changes:

```zsh
uv run --locked dayboard weather disable
uv run --locked dayboard calendar disable
```

Disabling Calendar does not revoke macOS permissions. Normal collection never
builds a helper or opens a permission prompt. Demo mode remains completely isolated.
Both modules retain their last good in-memory readings on failures and mark them
stale; persistent offline weather restoration and account-authenticated calendar
end-to-end verification are not claimed here.

v0.5.0 uses schema 4. v0.6.0 uses **schema 5** for Mail
and additional AI preferences. Schemas 0–4 migrate in memory with new sources disabled;
existing integrations are preserved. Saving/onboarding writes schema 5, which published
v0.5.0 and earlier cannot read. Back up preferences before upgrading and restore
a compatible backup before downgrading.

### Remaining development roadmap

1. Extend workspace detail views as new providers arrive; Overview/Schedule/Mac/GitHub
   tabs shipped in v0.4.0, and Settings shipped in v0.3.0.
2. Expand calendar providers with ICS/CalDAV and Google/Microsoft authorization.
3. Add Apple Reminders, Google Tasks/Gmail, Microsoft To Do/Graph mail and IMAP;
   Apple Mail header sampling shipped in v0.6.0.
4. Expand baseline Mac metrics with disk/battery/process views, then capability-checked
   GPU/temperature/fan/power readings.
5. Expand GitHub status with personal review queues/issues/notifications and local
   repository monitoring; open-PR/default-branch commit previews are now implemented.
6. Extend AI analytics: Codex, observed-format Kiro credits and Oz run previews are
   included in v0.6.0; broader formats, durable history, account-wide Kiro/Warp
   limits and pricing estimates remain follow-ups. Claude shipped in v0.5.0.
7. Add the clickable aapl.io ticker, animated flip clock, responsive detail views,
   reduced-motion options and resource/performance hardening.

### Controls and diagnostics

- **q** — quit from any workspace tab; in Settings, cancel when not editing text.
- **1 / 2 / 3 / 4** — Overview / Schedule / Mac / GitHub (v0.4.0+).
- **5** — AI / independent Claude, Codex local/limits, Kiro and Warp/Oz sections.
- **6** — Inbox / Apple Mail headers (v0.6.0+).
- **Left / Right** — switch tabs when the tab bar is focused (v0.4.0+).
- **s** — open Settings (read-only in demo mode).
- **Ctrl-S** — save and apply Settings without exiting the dashboard.
- **Escape** — cancel Settings without saving; close an open dropdown first.
- **?** — help.
- **Tab / Shift-Tab** — move focus.
- **Arrow keys / Page Up / Page Down** — scroll the focused scrollable area.

```zsh
uv run --locked dayboard --version
uv run --locked dayboard doctor
uv run --locked dayboard doctor --json
```

`doctor` reports package/runtime information, configuration validity, and whether
the native Keychain backend loads. It **does not** test credential access or prove
that Keychain is unlocked. A missing configuration is normal before onboarding.

The dashboard cannot run through a pipe or another noninteractive terminal. Use
`doctor --json` for script-friendly output.

### Updating a source checkout

After committing or setting aside your own changes:

```zsh
git pull --ff-only
uv sync --locked --python 3.13
```

### Installing a published release

Follow the [v0.6.0 release quick start](#v060-release-quick-start) for downloads,
checksums, installation, first-run usage and troubleshooting, or the
[upgrade instructions](#upgrade-from-an-older-release) for an existing tool.
Use the filenames attached to your selected release if installing a different version.

## Security and local data

Default macOS locations:

- Preferences/account references: `~/Library/Application Support/Dayboard/config.json`.
- Disposable public cache: `~/Library/Caches/Dayboard/`.
- Credentials: macOS Keychain, through an explicit native backend only.

Application directories use mode `0700`; state files use `0600`. Unsafe symlinks,
hard links, nonregular state files, oversized files, and unsupported configuration
schemas are rejected. A corrupt configuration is not silently reset or overwritten.

The v0.1.0 foundation did not collect private account data. Since v0.2.0,
enabled Calendar reads upcoming events only after explicit permission; it never
persists event contents. Keychain behavior is unit-tested with synthetic adapters;
diagnostics only check backend availability. Demo mode does not read or write
normal application state.

Never commit credentials, `.env` files, exported private data, or local session logs.
The repository ignores the virtual environment, caches, build outputs, and common
credential-file patterns. Future integrations must request consent explicitly.

## Development

Install the locked development dependencies:

```zsh
uv sync --locked --extra dev --python 3.13
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy src scripts
uv run --locked python scripts/release.py validate
```

To apply formatting, use `uv run --locked ruff format .`.

Tests use synthetic data and temporary directories rather than live accounts.
GitHub Actions runs these checks on `macos-15` with Python 3.13, then builds and
smoke-tests both distributions. Other Python versions and OS versions are not
covered by that CI job.

Build and inspect packages locally:

```zsh
uv build --out-dir dist/verify
uv run --locked python scripts/release.py prepare --dist dist/verify
uv run --isolated --no-project --python 3.13 --with dist/verify/*.whl \
  python scripts/smoke_test.py --version "$(uv version --short)"
uv run --isolated --no-project --python 3.13 --with dist/verify/*.tar.gz \
  python scripts/smoke_test.py --version "$(uv version --short)"
```

Use an empty output directory when changing versions: preparation rejects stale
distributions instead of accidentally including them in a new release.

## Versions and releases

`pyproject.toml` is the version source of truth. The installed CLI, diagnostics,
and dashboard header read package metadata. The root project entry in `uv.lock`
must match. See [CHANGELOG.md](CHANGELOG.md) for human-written release information.

Use `MAJOR.MINOR.PATCH`: patch for fixes, minor for new features, major for breaking
changes. Dayboard is pre-1.0; compatibility is not yet stable. Supported prerelease
spellings are `0.2.0a1`, `0.2.0b1`, and `0.2.0rc1`. Release tags add the `v` prefix,
for example `v0.2.0rc1`. Development/post/local versions and alternative tag spellings
are intentionally rejected by the release gate.

### Maintainer procedure

1. On a development branch, bump the version:

   ```zsh
   uv version --bump patch
   ```

   For a prerelease or an explicit version:

   ```zsh
   uv version 0.2.0rc1
   ```

   `uv version` updates the lockfile unless invoked with `--frozen`. Run `uv lock`
   if the project entry is stale. Review and commit both `pyproject.toml` and
   `uv.lock`; do not hand-edit multiple version constants.

2. Add a matching `## [VERSION]` section to `CHANGELOG.md`, moving the relevant
   Unreleased entries into a nonempty change list. Run the development checks above.
   Commit and merge the reviewed version/changelog changes into `main`.

3. From a clean, up-to-date `main`, validate and push an annotated tag:

   ```zsh
   git switch main
   git pull --ff-only
   git status --short
   VERSION="$(uv version --short)"
   uv run --locked python scripts/release.py validate --tag "v${VERSION}"
   git tag -a "v${VERSION}" -m "Dayboard ${VERSION}"
   git push origin "v${VERSION}"
   ```

   Stop if `git status --short` lists changes. For the first `v0.1.0` release, the
   existing version does not need bumping, but the workflows/docs must already be
   merged and the matching changelog entry must be reviewed.

4. Watch the **Release** workflow in GitHub Actions. It:
   - Requires the tag to match the package and lockfile versions and the tagged
     commit to be reachable from `main`.
   - Runs macOS tests, lint, formatting, typing, build and package-content checks.
   - Smoke-tests the wheel and source tarball in separate isolated environments.
   - Verifies SHA-256 checksums and publishes only after checks pass.
   - Creates a GitHub Release with changelog/install information, generated GitHub
     notes, a wheel, a source tarball, and `SHA256SUMS`.
   - Marks alpha/beta/RC versions as prereleases, without marking them Latest.

### Workflow permissions and failure handling

- **CI** runs on pull requests, pushes to `main`, and manual dispatch.
- **Release** runs only on pushed `v*` tags; malformed or mismatched versions fail
  validation rather than publishing.
- Third-party actions are pinned to commit SHAs. uv is pinned to 0.13.7 in CI;
  update its pin deliberately when changing supported tooling.
- The reusable validation/build workflow has read-only repository permissions.
  Only the separate publishing job receives `contents: write`; its GitHub token
  is exposed only to the publishing step. No PAT or PyPI secret is required.
- A repository administrator must enable Actions and allow the workflow's publishing
  job to write repository contents. Organization policies may further restrict it.
- Restrict creation/modification of `v*` tags to release maintainers using repository
  rulesets. Publishing checks that the tag still resolves to the verified commit.
- If validation fails, fix the code or metadata and publish a new version/tag.
  Do not force-move a published tag. A transient failure before publication can be
  rerun from Actions; existing releases/assets are never automatically overwritten.
- If a failed upload leaves a draft release, inspect it manually before retrying.
  Checksums alone do not make the packages signed or notarized.

Adding these files does not itself publish anything. The workflows must be pushed
to GitHub, and a release requires an explicit version-tag push.
