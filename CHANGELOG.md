# Changelog

Changes are grouped by package version. An entry records intended release contents;
it does not mean that the corresponding GitHub Release has already been published.
Release tags must match both `pyproject.toml` and `uv.lock`.

## [Unreleased]

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
