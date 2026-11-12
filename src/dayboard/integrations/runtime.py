from dayboard.config import Config
from dayboard.core.providers import ProviderRegistry
from dayboard.integrations.ai.codex import CodexLimitsProvider, CodexProvider
from dayboard.integrations.ai.kiro import KiroProvider
from dayboard.integrations.ai.provider import ClaudeProvider
from dayboard.integrations.ai.warp import WarpProvider
from dayboard.integrations.calendar import CalendarProvider
from dayboard.integrations.github.provider import GitHubProvider
from dayboard.integrations.mail import MailProvider
from dayboard.integrations.native import CalendarBridge, bridge_path
from dayboard.integrations.telemetry import TelemetryProvider
from dayboard.integrations.weather import WeatherProvider
from dayboard.paths import AppPaths


def build_live_registry(config: Config, paths: AppPaths) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(TelemetryProvider())
    if config.claude.enabled:
        registry.register(ClaudeProvider(config.claude))
    if config.codex.enabled:
        registry.register(CodexProvider(config.codex))
    if config.codex.limits_enabled:
        registry.register(CodexLimitsProvider(config.codex))
    if config.kiro.enabled:
        registry.register(KiroProvider(config.kiro))
    if config.warp.enabled:
        registry.register(WarpProvider(config.warp))
    if config.mail.enabled:
        registry.register(MailProvider(config.mail, paths))
    if config.weather.enabled:
        registry.register(WeatherProvider(config.weather))
    if config.github.enabled:
        registry.register(GitHubProvider(config.github))
    if config.calendar.enabled:
        registry.register(
            CalendarProvider(
                config.calendar,
                CalendarBridge(bridge_path(paths), calendar_ids=config.calendar.calendar_ids),
            )
        )
    return registry
