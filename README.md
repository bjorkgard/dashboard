# Dayboard

A local-first terminal workspace for your Mac: a single place for your day, your
machine, your development work, and your AI activity.

Dayboard is built with Python and [Textual](https://textual.textualize.io/).
The long-term dashboard will combine calendars, reminders, email, Mac telemetry,
weather, a flip clock, GitHub activity, AI usage, and a clickable Apple-news ticker.

**Current status: milestone 1 — secure foundations.** The application runs, but
live account integrations and machine telemetry are **not implemented yet**.
Demo values are synthetic, not readings from your Mac or accounts.

## What works today

- A terminal workspace with a ticking local clock and foundation-preview cards.
- An isolated `--demo` mode with changing synthetic readings.
- First-run onboarding that can save an unconnected workspace or quit without saving.
- Strict, versioned configuration with owner-only permissions and atomic writes.
- A macOS Keychain credential wrapper with no plaintext fallback.
- Provider contracts, independent asynchronous collection, timeouts, retry backoff,
  last-good snapshots, and bounded history/public-cache infrastructure.
- Redacted `doctor` diagnostics that do not read credentials.
- Unit, headless UI, and real pseudo-terminal startup/shutdown tests.

Account selection/login, full tabs, the animated flip clock, live CPU/GPU/temperature
graphs, calendar/mail/task providers, GitHub connections, AI-provider analytics,
weather, and the [aapl.io](https://aapl.io) news ticker are planned work.
There are no accounts to connect in this version.

## Run locally on a Mac

### Requirements

- macOS and a terminal with an interactive TTY.
- Git.
- [uv](https://docs.astral.sh/uv/getting-started/installation/), version **0.13.7
  or newer**.
- Python **3.13 or newer**. The commands below install Python 3.13 through uv.

The foundation does not require Xcode, a Swift build, administrator access, API
keys, or Calendar/Mail permissions. Git may prompt you to install Apple's Command
Line Tools if it is not already available. Native integrations will have their own
requirements when they are implemented.

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
it or install Python packages globally. The initial install needs internet access;
the current application itself makes no provider/network requests.

### 3. Start Dayboard

Preview the interface without accessing accounts, Keychain, or application state:

```zsh
uv run --locked dayboard --demo
```

Start the normal workspace:

```zsh
uv run --locked dayboard
```

On first launch, choose **Continue without accounts** to save onboarding preferences,
or **Quit without saving**. Normal mode shows unconnected cards; it never substitutes
synthetic data.

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
uv tool install --python 3.13 ./dayboard-0.1.0-py3-none-any.whl
dayboard --demo
```

The filename above illustrates the initial version; use the filename attached to
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

The first milestone does not collect private account data or save credentials.
Keychain behavior is unit-tested with synthetic adapters; diagnostics only check
backend availability. Demo mode does not read or write normal application state.

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
