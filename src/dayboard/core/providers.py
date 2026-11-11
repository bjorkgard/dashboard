"""Only registered, explicitly enabled providers may collect data."""

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from dayboard.core.models import ErrorCode, ProviderKey, Snapshot


class ProviderUnavailable(Exception):
    def __init__(self, code: ErrorCode) -> None:
        if not isinstance(code, ErrorCode):
            raise ValueError("A safe provider error code is required.")
        super().__init__(code.value)
        self.code = code


class Capability(StrEnum):
    SYSTEM = "system"
    CALENDAR = "calendar"
    REMINDERS = "reminders"
    MAIL = "mail"
    GITHUB = "github"
    AI = "ai"
    WEATHER = "weather"
    NEWS = "news"


@dataclass(frozen=True)
class ProviderSpec:
    key: ProviderKey
    name: str
    capabilities: tuple[Capability, ...]
    interval_seconds: float = 60
    timeout_seconds: float = 10
    synthetic: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.key, ProviderKey) or not isinstance(self.name, str):
            raise ValueError("Invalid provider identity.")
        if not self.name or len(self.name) > 100:
            raise ValueError("Invalid provider name.")
        for budget in (self.interval_seconds, self.timeout_seconds):
            if type(budget) not in (int, float) or not math.isfinite(budget) or budget <= 0:
                raise ValueError("Collection budgets must be finite positive numbers.")
        if (
            not isinstance(self.capabilities, tuple)
            or not all(isinstance(value, Capability) for value in self.capabilities)
            or type(self.synthetic) is not bool
        ):
            raise ValueError("Invalid provider capabilities or mode.")


class Provider(Protocol):
    @property
    def spec(self) -> ProviderSpec: ...
    async def collect(self) -> Snapshot:
        """Return bounded data; blocking I/O must be delegated off the event loop."""
        ...


class RateLimited(Exception):
    def __init__(self, *, retry_after: float) -> None:
        if not math.isfinite(retry_after) or retry_after < 0:
            raise ValueError("Invalid retry interval.")
        super().__init__("Provider rate limited.")
        self.retry_after = retry_after


class ProviderRegistry:
    def __init__(self, *, demo: bool = False, max_providers: int = 128) -> None:
        if type(max_providers) is not int or max_providers < 1:
            raise ValueError("Provider capacity must be positive.")
        self.demo = demo
        self.max_providers = max_providers
        self._providers: dict[ProviderKey, Provider] = {}

    def register(self, provider: Provider) -> None:
        if provider.spec.synthetic != self.demo:
            raise ValueError("Live and synthetic providers cannot share a registry.")
        if provider.spec.key in self._providers:
            raise ValueError("Provider/account is already registered.")
        if len(self._providers) >= self.max_providers:
            raise ValueError("Provider capacity exceeded.")
        self._providers[provider.spec.key] = provider

    def get(self, key: ProviderKey) -> Provider:
        return self._providers[key]

    def all(self) -> tuple[Provider, ...]:
        return tuple(self._providers.values())
