"""Synthetic providers only. No filesystem, Keychain, network or subprocess access."""

import math
from datetime import UTC, datetime

from dayboard.core.models import Coverage, Metric, ProviderKey, Quality, Snapshot
from dayboard.core.providers import Capability, ProviderRegistry, ProviderSpec

PANELS: tuple[tuple[str, str, Capability], ...] = (
    ("mac", "MAC PULSE", Capability.SYSTEM),
    ("schedule", "TODAY", Capability.CALENDAR),
    ("inbox", "INBOX", Capability.MAIL),
    ("github", "GITHUB ATTENTION", Capability.GITHUB),
    ("ai", "AI ACTIVITY", Capability.AI),
    ("weather", "OUTSIDE", Capability.WEATHER),
)


class SampleProvider:
    def __init__(self, panel: str, name: str, capability: Capability) -> None:
        self.spec = ProviderSpec(
            ProviderKey(f"demo-{panel}"),
            name,
            (capability,),
            interval_seconds=1,
            timeout_seconds=1,
            synthetic=True,
        )
        self.panel = panel
        self._tick = 0

    async def collect(self) -> Snapshot:
        self._tick += 1
        samples: dict[str, tuple[Metric, ...]] = {
            "mac": (
                Metric("cpu", "CPU", round(24 + 10 * math.sin(self._tick / 3)), "%"),
                Metric("memory", "Memory", "18 / 32", "GB"),
            ),
            "schedule": (
                Metric("next", "Next", "14:30 · Design review"),
                Metric("tasks", "Reminders", "3 due today · 1 overdue"),
            ),
            "inbox": (
                Metric("unread", "Unread", 8),
                Metric("latest", "Latest", "Sample sender · Project update"),
            ),
            "github": (
                Metric("reviews", "Reviews waiting", 3),
                Metric("checks", "Checks need attention", 1),
            ),
            "ai": (
                Metric("coverage", "Coverage", "Synthetic local usage example, not account limits"),
                Metric("today-input", "1d uncached input", 124_000 + 850 * self._tick, "tokens"),
                Metric("today-output", "1d output", 5000, "tokens"),
                Metric("today-sessions", "1d observed sessions", 2),
                Metric("cost", "Cost / account limits", "Unavailable in synthetic preview"),
            ),
            "weather": (
                Metric("temperature", "Temperature", 12, "°C"),
                Metric("conditions", "Conditions", "Partly cloudy · Sample location"),
            ),
        }
        now = datetime.now(UTC)
        return Snapshot(
            self.spec.key,
            now,
            now,
            samples[self.panel],
            Quality.DERIVED,
            Coverage.PARTIAL,
            synthetic=True,
        )


def demo_registry() -> ProviderRegistry:
    registry = ProviderRegistry(demo=True)
    for panel, name, capability in PANELS:
        registry.register(SampleProvider(panel, name, capability))
    return registry
