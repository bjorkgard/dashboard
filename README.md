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

**Unreleased development work:** an in-dashboard Settings screen now provides city
lookup, Calendar authorization and GitHub editing, with live application of saved
preferences. This is not included in the published v0.2.0 wheel yet.

## What works today

- A terminal workspace with a ticking local clock and foundation-preview cards.
- An isolated `--demo` mode with changing synthetic readings.
- First-run onboarding that can save an unconnected workspace or quit without saving.
- Strict, versioned configuration with owner-only permissions and atomic writes.
- A macOS Keychain credential wrapper with no plaintext fallback.
- Provider contracts, independent asynchronous collection, timeouts, retry backoff,
  last-good snapshots, and bounded history/public-cache infrastructure.
- Redacted `doctor` diagnostics that do not read credentials.
- Read-only macOS Calendar events and location-configured live weather.
- Automatic local CPU/RAM/network readings and short live histories.
- Selected-repository GitHub PR previews, draft/review/check states,
  and recent default-branch commits, using GitHub CLI's existing authentication.
- Unit, headless UI, and real pseudo-terminal startup/shutdown tests.

Account selection/login, full tabs, the animated flip clock, GPU/temperature
graphs, direct Google/Microsoft calendar connections, mail/task providers, GitHub
issues/notifications/personal review queues, AI-provider analytics, and the [aapl.io](https://aapl.io) news ticker
are planned work. Calendars already synced with macOS use the OS's existing accounts
after explicit permission; new cloud-account login is not implemented in this slice.

## Run locally on a Mac

### Requirements

- macOS and a terminal with an interactive TTY.
- Git.
- [uv](https://docs.astral.sh/uv/getting-started/installation/), version **0.13.7
  or newer**.
- Python **3.13 or newer**. The commands below install Python 3.13 through uv.

Demo mode and weather do not require Xcode, administrator access, API keys, or
Calendar/Mail permissions. The native Calendar module requires macOS 14+ and Swift
Command Line Tools, plus explicit Calendar permission. Git may prompt you to install
Apple's Command Line Tools if it is not already available.

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

Dependencies are installed in the project's `.venv`. You do not need to activate
it or install Python packages globally. The initial install needs internet access.
Normal mode reads local CPU/RAM/network counters without making network requests.
After weather setup, normal mode also contacts Open-Meteo; disabled account/weather
sources and demo mode make no provider requests. Enabled GitHub status makes a
bounded read query through GitHub CLI about once per minute.

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

### In-dashboard Settings (unreleased)

From this source checkout, press **s** or click **Settings [s]** on the overview.
The first-run **Configure** action opens the same screen.

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

Configuration is now schema 3. Schemas 0/1/2 migrate in memory and are saved only on
an explicit settings/onboarding action. The older v0.1.0 binary cannot read schema 3;
do not downgrade against newly saved preferences without restoring an older backup.

### Remaining development roadmap

1. Finish full workspace tabs/detail views; in-app Settings is available in development.
2. Expand calendar providers with ICS/CalDAV and Google/Microsoft authorization.
3. Add Apple Reminders/Mail, Google Tasks/Gmail, Microsoft To Do/mail, and IMAP.
4. Expand baseline Mac metrics with disk/battery/process views, then capability-checked
   GPU/temperature/fan/power readings.
5. Expand GitHub status with personal review queues/issues/notifications and local
   repository monitoring; open-PR/default-branch commit previews are now implemented.
6. Add opt-in Claude/Codex/Kiro/Warp analytics with honest source/estimate labels.
7. Add the clickable aapl.io ticker, animated flip clock, responsive detail views,
   reduced-motion options and resource/performance hardening.

### Controls and diagnostics

- **q** — quit.
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

Once a release has been published, download its wheel, source tarball, and
`SHA256SUMS` from [GitHub Releases](https://github.com/bjorkgard/dashboard/releases).
These are Python distributions, **not a signed/notarized standalone macOS app**.
No PyPI publication is configured.

From the directory containing all three downloads:

```zsh
shasum -a 256 -c SHA256SUMS
uv tool install --python 3.13 ./dayboard-0.2.0-py3-none-any.whl
dayboard --demo
```

The filename above illustrates v0.2.0; use the filename attached to
your selected release. To install a newer downloaded wheel over an existing
installation, add `--reinstall`. If `dayboard` is not found, run
`uv tool update-shell` and open a new terminal. Checksums detect corrupted downloads;
they are not a substitute for verifying the release source.

## Security and local data

Default macOS locations:

- Preferences/account references: `~/Library/Application Support/Dayboard/config.json`.
- Disposable public cache: `~/Library/Caches/Dayboard/`.
- Credentials: macOS Keychain, through an explicit native backend only.

Application directories use mode `0700`; state files use `0600`. Unsafe symlinks,
hard links, nonregular state files, oversized files, and unsupported configuration
schemas are rejected. A corrupt configuration is not silently reset or overwritten.

The v0.1.0 foundation did not collect private account data. In v0.2.0,
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
