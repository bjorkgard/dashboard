"""Strict, versioned nonsecret configuration. Unknown fields fail closed."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from dayboard.errors import ConfigError as ConfigError
from dayboard.paths import AppPaths, private_read, private_write
from dayboard.security.identifiers import validate_identifier
from dayboard.settings import CalendarSettings, GitHubSettings, WeatherSettings


@dataclass(frozen=True)
class AccountRef:
    id: str
    provider: str
    label: str

    def __post_init__(self) -> None:
        try:
            validate_identifier(self.id)
            validate_identifier(self.provider)
        except ValueError:
            raise ConfigError("Invalid account reference.") from None
        if not isinstance(self.label, str) or not 1 <= len(self.label) <= 100:
            raise ConfigError("Account label must contain 1–100 characters.")
        if any(ord(char) < 32 or 127 <= ord(char) < 160 for char in self.label):
            raise ConfigError("Account label contains control characters.")


@dataclass(frozen=True)
class Config:
    schema_version: int = 3
    onboarding_complete: bool = False
    accounts: tuple[AccountRef, ...] = ()
    theme: str = "dark"
    reduced_motion: bool = False
    weather: WeatherSettings = WeatherSettings()
    calendar: CalendarSettings = CalendarSettings()
    github: GitHubSettings = GitHubSettings()

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 3:
            raise ConfigError("Unsupported configuration version.")
        if not isinstance(self.weather, WeatherSettings) or not isinstance(
            self.calendar, CalendarSettings
        ):
            raise ConfigError("Invalid integration preferences.")
        if not isinstance(self.github, GitHubSettings):
            raise ConfigError("Invalid GitHub preferences.")
        if type(self.onboarding_complete) is not bool or type(self.reduced_motion) is not bool:
            raise ConfigError("Configuration flags must be booleans.")
        if self.theme not in ("dark", "light", "terminal"):
            raise ConfigError("Unsupported theme.")
        if (
            not isinstance(self.accounts, tuple)
            or len(self.accounts) > 100
            or not all(isinstance(account, AccountRef) for account in self.accounts)
        ):
            raise ConfigError("Invalid account references.")
        keys = [(account.provider, account.id) for account in self.accounts]
        if len(keys) != len(set(keys)):
            raise ConfigError("Duplicate account reference.")

    @classmethod
    def decode(cls, data: Any) -> Config:
        if not isinstance(data, dict):
            raise ConfigError("Configuration must be an object.")
        data = data.copy()
        version = data.get("schema_version")
        if type(version) is not int or version not in (0, 1, 2, 3):
            raise ConfigError("Unsupported configuration version.")
        if version == 0:
            allowed = {"schema_version", "setup_complete", "accounts", "theme", "reduced_motion"}
            if data.keys() - allowed:
                raise ConfigError("Unexpected configuration fields.")
            data["schema_version"] = 1
            data["onboarding_complete"] = data.pop("setup_complete", False)
        allowed = {"schema_version", "onboarding_complete", "accounts", "theme", "reduced_motion"}
        if version in (0, 1):
            if data.keys() - allowed:
                raise ConfigError("Unexpected legacy configuration fields.")
            data["schema_version"] = 2
        allowed |= {"weather", "calendar"}
        if version in (0, 1, 2):
            if data.keys() - allowed:
                raise ConfigError("Unexpected legacy integration fields.")
            data["schema_version"] = 3
        allowed.add("github")
        if data.keys() - allowed:
            raise ConfigError("Unexpected configuration fields.")
        accounts = data.get("accounts", [])
        if not isinstance(accounts, list) or len(accounts) > 100:
            raise ConfigError("Invalid account references.")
        references = []
        for account in accounts:
            if not isinstance(account, dict) or set(account) != {"id", "provider", "label"}:
                raise ConfigError("Only nonsecret account references may be stored.")
            references.append(AccountRef(**account))
        data["accounts"] = tuple(references)
        data["weather"] = WeatherSettings.decode(data.get("weather", {}))
        data["calendar"] = CalendarSettings.decode(data.get("calendar", {}))
        data["github"] = GitHubSettings.decode(data.get("github", {}))
        return cls(**data)


class ConfigStore:
    def __init__(self, paths: AppPaths) -> None:
        self.paths = paths

    def load(self) -> Config | None:
        try:
            data = private_read(self.paths.config_file)
        except FileNotFoundError:
            return None
        except OSError:
            raise ConfigError("Cannot read protected configuration.") from None
        try:
            return Config.decode(json.loads(data))
        except (ValueError, TypeError, KeyError, RecursionError):
            raise ConfigError("Invalid configuration; existing file was left unchanged.") from None

    def save(self, config: Config) -> None:
        payload = {
            "schema_version": config.schema_version,
            "onboarding_complete": config.onboarding_complete,
            "theme": config.theme,
            "reduced_motion": config.reduced_motion,
            "weather": config.weather.encode(),
            "calendar": config.calendar.encode(),
            "github": config.github.encode(),
            "accounts": [
                {"id": account.id, "provider": account.provider, "label": account.label}
                for account in config.accounts
            ],
        }
        Config.decode(payload)
        try:
            self.paths.ensure()
            private_write(
                self.paths.config_file,
                (json.dumps(payload, indent=2, ensure_ascii=True) + "\n").encode(),
            )
        except OSError:
            raise ConfigError("Cannot save protected configuration.") from None
