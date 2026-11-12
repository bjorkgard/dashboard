from dayboard.config import Config
from dayboard.core.providers import ProviderRegistry
from dayboard.integrations.ai.provider import ClaudeProvider
from dayboard.integrations.calendar import CalendarProvider
from dayboard.integrations.github.provider import GitHubProvider
from dayboard.integrations.native import CalendarBridge, bridge_path
from dayboard.integrations.telemetry import TelemetryProvider
from dayboard.integrations.weather import WeatherProvider
from dayboard.paths import AppPaths


def build_live_registry(config: Config, paths: AppPaths) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(TelemetryProvider())
    if config.claude.enabled:
        registry.register(ClaudeProvider(config.claude))
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
