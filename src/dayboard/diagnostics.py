"""Allowlisted diagnostics. Never read credentials or print account identities."""

import platform
import sys
from importlib.metadata import version

from dayboard.config import ConfigError, ConfigStore
from dayboard.paths import AppPaths
from dayboard.security.keychain import native_backend


def diagnose(paths: AppPaths) -> tuple[dict[str, str | int | bool], bool]:
    report: dict[str, str | int | bool] = {
        "dayboard_version": version("dayboard"),
        "python_version": platform.python_version(),
        "platform": sys.platform,
        "configuration": "missing",
        "account_count": 0,
        "keychain_backend": "unavailable",
        "keychain_access": "not tested (no credentials read)",
        "live_integrations": (
            "local CPU/RAM/network; calendar/weather/GitHub/Claude are explicit opt-in"
        ),
        "github_enabled": False,
        "github_repository_count": 0,
        "telemetry": "automatic local OS counters in normal mode; no network probes",
        "calendar_enabled": False,
        "weather_enabled": False,
        "claude_enabled": False,
        "claude_source_access": "not tested (no source files read)",
    }
    healthy = True
    try:
        config = ConfigStore(paths).load()
        if config is not None:
            report["configuration"] = "valid"
            report["account_count"] = len(config.accounts)
            report["onboarding_complete"] = config.onboarding_complete
            report["calendar_enabled"] = config.calendar.enabled
            report["weather_enabled"] = config.weather.enabled
            report["github_enabled"] = config.github.enabled
            report["github_repository_count"] = len(config.github.repositories)
            report["claude_enabled"] = config.claude.enabled
    except ConfigError:
        report["configuration"] = "invalid"
        healthy = False
    try:
        native_backend()
        report["keychain_backend"] = "macOS backend available"
    except Exception:
        healthy = False
    return report, healthy
