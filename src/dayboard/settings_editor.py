"""Pure form validation and protected persistence, independent of terminal widgets."""

import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from dayboard.config import Config, ConfigError, ConfigStore
from dayboard.core.providers import ProviderUnavailable
from dayboard.integrations.ai.reader import validate_source
from dayboard.security.warp_credentials import WarpCredentialError, save_with_warp_key
from dayboard.settings import (
    ClaudeSettings,
    CodexSettings,
    GitHubSettings,
    KiroSettings,
    MailboxSelection,
    MailSettings,
    WeatherSettings,
)


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
    mail_enabled: bool = False
    mailboxes: tuple[MailboxSelection, ...] = ()
    codex_enabled: bool = False
    codex_limits_enabled: bool = False
    codex_source: str = ""
    codex_timezone: str = "UTC"
    kiro_enabled: bool = False
    kiro_source: str = ""
    kiro_timezone: str = "UTC"
    warp_enabled: bool = False
    warp_team_uid: str = ""
    warp_key: str = field(default="", repr=False)


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
    mail = replace(original.mail, enabled=False)
    if draft.mail_enabled:
        try:
            mail = MailSettings(True, draft.mailboxes)
        except ConfigError:
            raise SettingsValidationError(
                "Mail", "Select one to five discovered mailboxes."
            ) from None
    codex = replace(original.codex, enabled=False, limits_enabled=False)
    if draft.codex_enabled or draft.codex_limits_enabled:
        try:
            codex = CodexSettings(
                draft.codex_enabled,
                str(Path(draft.codex_source.strip()).expanduser()),
                draft.codex_timezone.strip(),
                draft.codex_limits_enabled,
            )
        except (ConfigError, RuntimeError):
            raise SettingsValidationError(
                "Codex", "Choose an absolute root and IANA timezone."
            ) from None
    kiro = replace(original.kiro, enabled=False)
    if draft.kiro_enabled:
        try:
            kiro = KiroSettings(
                True, str(Path(draft.kiro_source.strip()).expanduser()), draft.kiro_timezone.strip()
            )
        except (ConfigError, RuntimeError):
            raise SettingsValidationError(
                "Kiro", "Choose an absolute sessions root and IANA timezone."
            ) from None
    warp = replace(original.warp, enabled=False)
    if draft.warp_enabled:
        try:
            warp = replace(original.warp, enabled=True, team_uid=draft.warp_team_uid.strip())
        except ConfigError:
            raise SettingsValidationError("Warp", "Enter a valid optional team UID.") from None
    return replace(
        original,
        onboarding_complete=True,
        weather=weather,
        github=github,
        calendar=replace(original.calendar, enabled=draft.calendar_enabled),
        claude=claude,
        mail=mail,
        codex=codex,
        kiro=kiro,
        warp=warp,
    )


def save_draft(store: ConfigStore, opened: Config, draft: SettingsDraft) -> Config:
    latest = store.load() or Config()
    if integration_scope(latest) != integration_scope(opened):
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
    if candidate.kiro.enabled:
        try:
            validate_source(candidate.kiro.source_root, subtree=())
        except ProviderUnavailable:
            raise SettingsValidationError(
                "Kiro", "Selected sessions root is unsafe or unavailable."
            ) from None
    if candidate.codex.enabled:
        valid = False
        for subtree in (("sessions",), ("archived_sessions",)):
            try:
                validate_source(candidate.codex.source_root, subtree=subtree)
                valid = True
            except ProviderUnavailable:
                pass
        if not valid:
            raise SettingsValidationError(
                "Codex",
                "Selected root needs an accessible sessions or archived_sessions directory.",
            )
    try:
        return save_with_warp_key(store, candidate, latest, draft.warp_key)
    except WarpCredentialError as error:
        raise SettingsValidationError("Warp", str(error)) from None


def integration_scope(config: Config) -> tuple[object, ...]:
    return (
        config.calendar,
        config.weather,
        config.github,
        config.claude,
        config.mail,
        config.codex,
        config.kiro,
        config.warp,
    )
