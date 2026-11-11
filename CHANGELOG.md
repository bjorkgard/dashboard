# Changelog

Changes are grouped by package version. An entry records intended release contents;
it does not mean that the corresponding GitHub Release has already been published.
Release tags must match both `pyproject.toml` and `uv.lock`.

## [Unreleased]

Future features and fixes will be collected here before the next version is prepared.

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
