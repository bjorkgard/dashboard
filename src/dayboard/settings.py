"""Explicit, nonsecret integration preferences. Disabled unless the user opts in."""

import math
import re
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dayboard.errors import ConfigError


def _text(value: str, limit: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= limit
        and all(ord(char) >= 32 and not 127 <= ord(char) < 160 for char in value)
    )


@dataclass(frozen=True)
class WeatherSettings:
    enabled: bool = False
    latitude: float = 0
    longitude: float = 0
    label: str = ""
    timezone: str = "UTC"
    temperature_unit: str = "celsius"

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ConfigError("Weather enabled must be a boolean.")
        for coordinate, limit in ((self.latitude, 90), (self.longitude, 180)):
            if (
                type(coordinate) not in (int, float)
                or not math.isfinite(coordinate)
                or not -limit <= coordinate <= limit
            ):
                raise ConfigError("Weather coordinates are invalid.")
        if not _text(self.label, 100) or (self.enabled and not self.label.strip()):
            raise ConfigError("An enabled weather location needs a valid display name.")
        if self.temperature_unit not in ("celsius", "fahrenheit"):
            raise ConfigError("Use celsius or fahrenheit.")
        try:
            if not _text(self.timezone, 100):
                raise ValueError
            ZoneInfo(self.timezone)
        except (ValueError, ZoneInfoNotFoundError, TypeError):
            raise ConfigError("Use a valid IANA timezone, such as Europe/Stockholm.") from None

    @classmethod
    def decode(cls, value: Any) -> "WeatherSettings":
        if not isinstance(value, dict) or value.keys() - {
            "enabled",
            "latitude",
            "longitude",
            "label",
            "timezone",
            "temperature_unit",
        }:
            raise ConfigError("Unexpected weather settings.")
        return cls(**value)

    def encode(self) -> dict[str, str | float | bool]:
        return {
            "enabled": self.enabled,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "label": self.label,
            "timezone": self.timezone,
            "temperature_unit": self.temperature_unit,
        }


@dataclass(frozen=True)
class CalendarSettings:
    enabled: bool = False
    calendar_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.enabled) is not bool
            or not isinstance(self.calendar_ids, tuple)
            or len(self.calendar_ids) > 64
            or not all(_text(value, 256) and value for value in self.calendar_ids)
        ):
            raise ConfigError("Invalid calendar settings.")
        if len(set(self.calendar_ids)) != len(self.calendar_ids):
            raise ConfigError("Duplicate calendar identifiers.")

    @classmethod
    def decode(cls, value: Any) -> "CalendarSettings":
        if not isinstance(value, dict) or value.keys() - {"enabled", "calendar_ids"}:
            raise ConfigError("Unexpected calendar settings.")
        ids = value.get("calendar_ids", [])
        if not isinstance(ids, list):
            raise ConfigError("Calendar identifiers must be a list.")
        return cls(value.get("enabled", False), tuple(ids))

    def encode(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "calendar_ids": list(self.calendar_ids)}


_REPOSITORY = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?/[A-Za-z0-9_.-]{1,100}\Z")


@dataclass(frozen=True)
class GitHubSettings:
    enabled: bool = False
    repositories: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.enabled) is not bool
            or not isinstance(self.repositories, tuple)
            or len(self.repositories) > 5
            or (self.enabled and not self.repositories)
        ):
            raise ConfigError("Select one to five GitHub repositories when enabled.")
        for repository in self.repositories:
            if (
                not isinstance(repository, str)
                or not _REPOSITORY.fullmatch(repository)
                or repository.split("/")[-1] in (".", "..")
            ):
                raise ConfigError("Use a GitHub repository in OWNER/REPO format, not a URL.")
        if len({value.casefold() for value in self.repositories}) != len(self.repositories):
            raise ConfigError("Duplicate GitHub repositories.")

    @classmethod
    def decode(cls, value: Any) -> "GitHubSettings":
        if not isinstance(value, dict) or value.keys() - {"enabled", "repositories"}:
            raise ConfigError("Unexpected GitHub settings; credentials must remain owned by gh.")
        repositories = value.get("repositories", [])
        if not isinstance(repositories, list):
            raise ConfigError("GitHub repositories must be a list.")
        return cls(value.get("enabled", False), tuple(repositories))

    def encode(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "repositories": list(self.repositories)}
