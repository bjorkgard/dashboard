"""Pure form validation and protected persistence, independent of terminal widgets."""

import re
from dataclasses import dataclass, replace
from pathlib import Path

from dayboard.config import Config, ConfigError, ConfigStore
from dayboard.core.providers import ProviderUnavailable
from dayboard.integrations.ai.reader import validate_source
from dayboard.settings import ClaudeSettings, GitHubSettings, WeatherSettings


class SettingsValidationError(ConfigError):
    def __init__(self, section: str, message: str) -> None:
        super().__init__(f"{section}: {message}")
        self.section = section


@dataclass(frozen=True)
class SettingsDraft:
    calendar_enabled: bool = False
    weather_enabled: bool = False
    weather_label: str = ""
    latitude: str = ""
    longitude: str = ""
    timezone: str = "UTC"
    units: str = "celsius"
    github_enabled: bool = False
    repositories: str = ""
    claude_enabled: bool = False
    claude_source: str = ""
    claude_timezone: str = "UTC"


def build_candidate(original: Config, draft: SettingsDraft) -> Config:
    weather = replace(original.weather, enabled=False)
    if draft.weather_enabled:
        try:
            latitude, longitude = float(draft.latitude), float(draft.longitude)
        except (ValueError, TypeError, OverflowError):
            raise SettingsValidationError(
                "Weather", "Enter numeric latitude and longitude."
            ) from None
        try:
            weather = WeatherSettings(
                True,
                latitude,
                longitude,
                draft.weather_label.strip(),
                draft.timezone.strip(),
                draft.units,
            )
        except ConfigError as error:
            raise SettingsValidationError("Weather", str(error)) from None
    github = replace(original.github, enabled=False)
    if draft.github_enabled:
        repositories = tuple(
            value for value in re.split(r"[\s,]+", draft.repositories.strip()) if value
        )
        try:
            github = GitHubSettings(True, repositories)
        except ConfigError as error:
            raise SettingsValidationError("GitHub", str(error)) from None
    claude = replace(original.claude, enabled=False)
    if draft.claude_enabled:
        try:
            claude = ClaudeSettings(
                True,
                str(Path(draft.claude_source.strip()).expanduser()),
                draft.claude_timezone.strip(),
            )
        except (ConfigError, RuntimeError):
            raise SettingsValidationError(
                "Claude", "Choose an absolute source directory and a valid IANA timezone."
            ) from None
    return replace(
        original,
        onboarding_complete=True,
        weather=weather,
        github=github,
        calendar=replace(original.calendar, enabled=draft.calendar_enabled),
        claude=claude,
    )


def save_draft(store: ConfigStore, opened: Config, draft: SettingsDraft) -> Config:
    latest = store.load() or Config()
    if (latest.calendar, latest.weather, latest.github, latest.claude) != (
        opened.calendar,
        opened.weather,
        opened.github,
        opened.claude,
    ):
        raise ConfigError("Integration settings changed externally. Cancel and reopen Settings.")
    candidate = build_candidate(latest, draft)
    if candidate.claude.enabled:
        try:
            validate_source(candidate.claude.source_root)
        except ProviderUnavailable:
            raise SettingsValidationError(
                "Claude",
                "Source unavailable, unsafe or inaccessible. Choose an owned Claude directory.",
            ) from None
    store.save(candidate)
    return candidate
