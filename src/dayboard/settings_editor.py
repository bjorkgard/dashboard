"""Pure form validation and protected persistence, independent of terminal widgets."""

import re
from dataclasses import dataclass, replace

from dayboard.config import Config, ConfigError, ConfigStore
from dayboard.settings import GitHubSettings, WeatherSettings


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
    return replace(
        original,
        onboarding_complete=True,
        weather=weather,
        github=github,
        calendar=replace(original.calendar, enabled=draft.calendar_enabled),
    )


def save_draft(store: ConfigStore, opened: Config, draft: SettingsDraft) -> Config:
    latest = store.load() or Config()
    if (latest.calendar, latest.weather, latest.github) != (
        opened.calendar,
        opened.weather,
        opened.github,
    ):
        raise ConfigError("Integration settings changed externally. Cancel and reopen Settings.")
    candidate = build_candidate(latest, draft)
    store.save(candidate)
    return candidate
