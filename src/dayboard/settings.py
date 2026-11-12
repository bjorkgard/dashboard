"""Explicit, nonsecret integration preferences. Disabled unless the user opts in."""

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dayboard.errors import ConfigError


@dataclass(frozen=True)
class ClaudeSettings:
    enabled: bool = False
    source_root: str = ""
    timezone: str = "UTC"

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool or not _text(self.source_root, 2048):
            raise ConfigError("Invalid Claude preferences.")
        if self.source_root and (
            not Path(self.source_root).is_absolute() or ".." in Path(self.source_root).parts
        ):
            raise ConfigError(
                "Claude source must be an absolute directory without parent traversal."
            )
        if self.enabled and not self.source_root:
            raise ConfigError("Choose a Claude source directory before enabling collection.")
        try:
            if not _text(self.timezone, 100):
                raise ValueError
            ZoneInfo(self.timezone)
        except (ValueError, TypeError, ZoneInfoNotFoundError):
            raise ConfigError("Choose a valid IANA timezone for Claude usage.") from None

    @classmethod
    def decode(cls, value: Any) -> "ClaudeSettings":
        if not isinstance(value, dict) or value.keys() - {"enabled", "source_root", "timezone"}:
            raise ConfigError("Unexpected Claude settings; credentials are not supported.")
        return cls(**value)

    def encode(self) -> dict[str, str | bool]:
        return {"enabled": self.enabled, "source_root": self.source_root, "timezone": self.timezone}


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


@dataclass(frozen=True)
class MailboxSelection:
    account_id: str
    mailbox_path: tuple[str, ...]
    account_name: str = ""

    def __post_init__(self) -> None:
        if (
            not _text(self.account_id, 256)
            or not self.account_id
            or not _text(self.account_name, 100)
            or not isinstance(self.mailbox_path, tuple)
            or not 1 <= len(self.mailbox_path) <= 8
            or not all(_text(part, 200) and part for part in self.mailbox_path)
        ):
            raise ConfigError("Invalid Mail mailbox selection.")

    def encode(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "mailbox_path": list(self.mailbox_path),
            "account_name": self.account_name,
        }

    @classmethod
    def decode(cls, value: Any) -> "MailboxSelection":
        if (
            not isinstance(value, dict)
            or value.keys() - {"account_id", "mailbox_path", "account_name"}
            or not isinstance(value.get("mailbox_path"), list)
        ):
            raise ConfigError("Invalid Mail mailbox selection.")
        return cls(
            value.get("account_id", ""), tuple(value["mailbox_path"]), value.get("account_name", "")
        )


@dataclass(frozen=True)
class MailSettings:
    enabled: bool = False
    mailboxes: tuple[MailboxSelection, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.enabled) is not bool
            or not isinstance(self.mailboxes, tuple)
            or len(self.mailboxes) > 5
            or (self.enabled and not self.mailboxes)
            or not all(isinstance(item, MailboxSelection) for item in self.mailboxes)
        ):
            raise ConfigError("Select one to five Mail mailboxes when enabled.")
        identities = {(item.account_id, item.mailbox_path) for item in self.mailboxes}
        if len(identities) != len(self.mailboxes):
            raise ConfigError("Duplicate Mail mailbox selection.")

    def encode(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "mailboxes": [item.encode() for item in self.mailboxes]}

    @classmethod
    def decode(cls, value: Any) -> "MailSettings":
        if not isinstance(value, dict) or value.keys() - {"enabled", "mailboxes"}:
            raise ConfigError("Unexpected Mail settings.")
        boxes = value.get("mailboxes", [])
        if not isinstance(boxes, list) or len(boxes) > 5:
            raise ConfigError("Invalid Mail mailbox list.")
        return cls(value.get("enabled", False), tuple(MailboxSelection.decode(v) for v in boxes))


@dataclass(frozen=True)
class CodexSettings:
    enabled: bool = False
    source_root: str = ""
    timezone: str = "UTC"
    limits_enabled: bool = False

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool or type(self.limits_enabled) is not bool:
            raise ConfigError("Invalid Codex enablement.")
        try:
            ClaudeSettings(self.enabled or self.limits_enabled, self.source_root, self.timezone)
        except ConfigError:
            raise ConfigError("Choose an absolute Codex root and valid IANA timezone.") from None

    def encode(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "source_root": self.source_root,
            "timezone": self.timezone,
            "limits_enabled": self.limits_enabled,
        }

    @classmethod
    def decode(cls, value: Any) -> "CodexSettings":
        if not isinstance(value, dict) or value.keys() - {
            "enabled",
            "source_root",
            "timezone",
            "limits_enabled",
        }:
            raise ConfigError("Unexpected Codex settings.")
        return cls(**value)


@dataclass(frozen=True)
class KiroSettings:
    enabled: bool = False
    source_root: str = ""
    timezone: str = "UTC"

    def __post_init__(self) -> None:
        try:
            ClaudeSettings(self.enabled, self.source_root, self.timezone)
        except ConfigError:
            raise ConfigError(
                "Choose an absolute Kiro sessions root and valid IANA timezone."
            ) from None

    def encode(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "source_root": self.source_root, "timezone": self.timezone}

    @classmethod
    def decode(cls, value: Any) -> "KiroSettings":
        if not isinstance(value, dict) or value.keys() - {"enabled", "source_root", "timezone"}:
            raise ConfigError("Unexpected Kiro settings.")
        return cls(**value)


@dataclass(frozen=True)
class WarpSettings:
    enabled: bool = False
    credential_id: str = "default"
    team_uid: str = ""
    retired_credential_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.enabled) is not bool
            or not isinstance(self.credential_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.credential_id)
            or not isinstance(self.team_uid, str)
            or (self.team_uid and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.team_uid))
            or not isinstance(self.retired_credential_ids, tuple)
            or len(self.retired_credential_ids) > 8
            or not all(
                isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value)
                for value in self.retired_credential_ids
            )
            or len(set(self.retired_credential_ids)) != len(self.retired_credential_ids)
            or self.credential_id in self.retired_credential_ids
        ):
            raise ConfigError("Invalid Warp scope or credential reference.")

    def encode(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "credential_id": self.credential_id,
            "team_uid": self.team_uid,
            "retired_credential_ids": list(self.retired_credential_ids),
        }

    @classmethod
    def decode(cls, value: Any) -> "WarpSettings":
        if not isinstance(value, dict) or value.keys() - {
            "enabled",
            "credential_id",
            "team_uid",
            "retired_credential_ids",
        }:
            raise ConfigError("Unexpected Warp preferences; credentials belong in Keychain.")
        retired = value.get("retired_credential_ids", [])
        if not isinstance(retired, list):
            raise ConfigError("Invalid Warp credential references.")
        return cls(
            value.get("enabled", False),
            value.get("credential_id", "default"),
            value.get("team_uid", ""),
            tuple(retired),
        )
