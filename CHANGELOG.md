# Changelog

Changes are grouped by package version. An entry records intended release contents;
it does not mean that the corresponding GitHub Release has already been published.
Release tags must match both `pyproject.toml` and `uv.lock`.

## [Unreleased]

No unreleased changes.

## [0.6.1] - 2026-11-12

### Changed

- Add a unified release quick start covering verified downloads, fresh installs,
  upgrades, first-run setup, provider references, controls and troubleshooting.
- Clarify installed-tool versus source-checkout usage and schema-5 backup/downgrade
  requirements. Runtime provider behavior and configuration schema are unchanged.

## [0.6.0] - 2026-11-12

### Added

- Opt-in Apple Mail header sampling, explicit Automation authorization and bounded
  mailbox discovery, with Inbox (`6`) and compact overview previews.
- Codex local rollout accounting and independent optional stdio account-limit reads;
  cumulative deltas preserve cached-input/reasoning subset semantics.
- Observed-format Kiro V3 recorded-credit collection, with exact decimal amounts and
  explicit unsupported/session-only states rather than inferred tokens or account limits.
- Documented fixed-host Warp/Oz run previews with masked Dayboard-owned Keychain setup,
  explicit connection testing and confirmed scoped credential removal.
- Independent per-provider AI views/statuses, synthetic multi-source demo, safe diagnostics,
  source-layout-aware bounded readers and expanded installed-package smoke checks.

### Fixed

- Recent-first bounded file selection and a reserved Claude recent-tail preview during
  ordered backfill, with original-date reconciliation across retention boundaries.
- Historical identity metadata cannot consume active usage capacity; empty unsupported
  windows remain unknown and actual recorded zero remains valid.
- In-flight mailbox edits and changed Warp drafts cannot be overwritten or falsely
  certified by late results. Warp overlap/account-scope warnings remain visible.
- Credential staging reconciles late config commit failures before rollback, and explicit
  Warp disconnect stops polling before potentially slow Keychain cleanup.

### Compatibility and scope

- Schema 5 migrates schemas 0–4 in memory with new sources off and prior choices preserved.
  Explicit Save writes schema 5; published v0.5.0 and earlier cannot read it.
- Mail is a bounded candidate-header preview, not a globally newest inbox or direct
  Gmail/Graph/IMAP client. Permission grants can exceed the app's read-only operations.
- Kiro support is format-specific, without SQLite/auth access, token estimates or quota probes.
  Warp run usage can overlap across conversations and is not a desktop/account total.
- No persistent private history, real permission/account access during tests, automatic
  login, inference requests or automatic installation changes.

## [0.5.0] - 2026-11-12

### Added

- Opt-in Claude Code local usage with an AI tab (`5`) and compact overview counters.
- Metadata-only in-memory usage ledger with source-scoped message deduplication,
  coherent streaming revision replacement, conflict quarantine and separate cache categories.
- Read-only descriptor-based JSONL discovery/tailing with bounded work, cooperative
  cancellation, unsafe-path rejection and partial-coverage diagnostics.
- Today/7-day/30-day calendar-window counters, model summaries, observed sessions
  and separate source-check/activity timestamps. No cost or account-quota inference.

### Compatibility and privacy

- Configuration schema 4 migrates schemas 0–3 in memory with Claude disabled.
  Explicit Save validates the selected source and applies changes live; opening
  Settings/Cancel/demo does not scan Claude sources. Older releases cannot read
  schema-4 preferences after saving.
- Source files include conversations; bounded records are transiently read, but only
  allowlisted usage metadata is retained in memory. No source writes/chmod, hooks,
  credentials, network calls, inference or persistent private usage cache.
- The observed Claude Code 2.x format is not a vendor schema guarantee. Indexing
  and skipped/conflicting data are labelled partial. Restart/Settings Save rebuilds
  available history; deleted source history cannot be recovered. Other AI providers remain planned.

### Fixed

- Claude discovery now resumes queued directories and partially enumerated directories
  across scan budgets, with caps applied to the entire discovery generation.
- Inaccessible, unowned or removed project directories no longer prevent discovery
  of readable siblings. Pending enumeration resources close on cancellation and shutdown.

## [0.4.0] - 2026-11-12

### Added

- Overview, Schedule, Mac and GitHub workspace tabs with visible mouse targets,
  number shortcuts and keyboard navigation, preserving the selected tab across Settings.
- Expanded seven-day agenda (up to 100 returned occurrences), shared telemetry
  history/metric details, and grouped bounded GitHub PR/default-branch commit lists.
- Responsive one/two-column overview and independently scrollable detail panes.
- Navigation, expanded-data, scope-reset and terminal-size regression coverage,
  plus installed-package smoke checks for all detail tabs.

### Behavior and scope

- Tabs share the existing collectors and snapshot store; navigation adds no provider
  requests or permission prompts. Overview previews remain at six events and five
  PRs/commits; details retain the additional records already fetched within existing caps.
- Loading, stale, error, provenance and partial-access states remain visible. Settings
  Save clears old-scope readings across all views; demo tabs remain synthetic and isolated.
- No new account providers, Inbox/AI detail tabs, hardware sensors or persistent tab
  preference are introduced. Configuration schema remains 3.

## [0.3.0] - 2026-11-11

### Added

- In-dashboard integration Settings, opened with `s` or the visible Settings button,
  plus a first-run Configure entry point and persistent keyboard-accessible Save/Cancel.
- Explicit weather city lookup with disambiguated choices and manual coordinate support.
  Results are never auto-selected; searches are bounded, cancellable and rate-limit aware.
- Explicit Calendar authorization in the UI and GitHub repository editing without
  password/token inputs or implicit account-login flows.
- Live collector replacement after saved changes, resetting old-scope readings without
  quitting the application. Failed writes keep the current collectors unchanged.
- Conflict detection for externally changed integration preferences, preserved disabled
  source settings, safe service-error messages, and read-only isolated demo Settings.

### Fixed

- Cancellation cleans up owned native/CLI descendant processes as well as their parent,
  preventing a compiler child holding stdout from extending the cancellation deadline.

### Compatibility and scope

- Configuration schema remains 3; existing v0.2.0 preferences and Calendar filters are
  preserved. Saving changed sources resets their in-memory readings and warm-up baselines.
- This release adds in-app configuration, not new account providers or full workspace tabs.
  Real Calendar authorization remains explicit and dependent on the user's macOS host grant.

## [0.2.0] - 2026-11-11

### Added

- Opt-in GitHub.com status for one to five selected repositories: open PR totals and
  bounded previews with draft/review/check states, plus recent default-branch commits.
- GitHub CLI-owned authentication, explicit repository setup/disable/status commands,
  rate-limit handling, partial-access indicators and safe in-memory metadata normalization.
- Local CPU, memory and network telemetry in normal mode, sampled off the UI thread.
  CPU/rates use owned counter baselines; unavailable/reset/warm-up intervals stay unknown.
- Mac pulse sparklines with bounded histories, RAM in-use/available amounts and
  upload/download rates in IEC byte units. Network scope excludes down/loopback interfaces.
- Initial read-only macOS Calendar provider with a bundled Swift/EventKit bridge,
  explicit permission setup, occurrence/source-aware normalization, and CLI enable/disable controls.
- Opt-in Open-Meteo weather with configured coordinates/timezone/units, current
  conditions and a short hourly outlook, bounded HTTP responses, and rate-limit handling.
- Live overview status updates, observation timestamps, last-good retention and
  actionable permission errors; disabled providers and demo mode never collect live data.
- Strict configuration schema 3 with in-memory migration from schemas 0, 1 and 2.

### Compatibility

- Configuration schema 3 migrates schemas 0/1/2 in memory and rewrites only on explicit
  saves. Older v0.1.0 binaries cannot read schema-3 preferences; restore an older
  preference backup before downgrading.

### Scope

- This release adds the first live integrations after the v0.1.0 foundation.
  Account-authorized Calendar end-to-end verification still requires the user's consent.
  Full Settings/tabs, direct cloud calendar OAuth, daily weather forecasts and persistent
  offline-weather cache restoration remain future work.

## [0.1.0]

### Added

- Milestone-one terminal workspace, first-run onboarding entry, and isolated synthetic demo.
- Immutable provider snapshots, independent asynchronous collection, timeouts,
  retry backoff, last-good retention, and bounded history/public-cache infrastructure.
- Strict configuration validation, schema migration, owner-only atomic state writes,
  explicit macOS Keychain storage wrapper, and redacted diagnostics.
- Unit, headless UI, and pseudo-terminal startup/shutdown tests.
- Local Mac setup documentation and tag-driven macOS CI/GitHub Release automation.
- Version/tag/lockfile/changelog validation, package smoke tests, release notes,
  and SHA-256 manifests for downloadable Python packages.

### Changed

- The dashboard header now reads installed package metadata instead of a hardcoded version.

### Limitations

- Live account integrations, machine telemetry, weather, Apple-news fetching,
  full tab navigation, and flip-clock animation are not implemented in this version.
- Downloads are Python packages, not signed or notarized standalone macOS applications.
