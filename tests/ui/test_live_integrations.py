from datetime import UTC, datetime

from textual.widgets import Static

from dayboard.app import DayboardApp
from dayboard.config import Config, ConfigStore
from dayboard.core.models import ErrorCode, Metric, ProviderKey, Snapshot
from dayboard.core.providers import Capability, ProviderRegistry, ProviderSpec, ProviderUnavailable
from dayboard.paths import AppPaths
from dayboard.ui.card import SnapshotCard


async def test_live_sources_render_real_status_without_demo_data(tmp_path, monkeypatch):
    class Weather:
        spec = ProviderSpec(ProviderKey("weather"), "Test weather", (Capability.WEATHER,))

        async def collect(self):
            now = datetime.now(UTC)
            return Snapshot(self.spec.key, now, now, (Metric("temperature", "Measured", 12),))

    registry = ProviderRegistry()
    registry.register(Weather())
    monkeypatch.setattr(
        "dayboard.app.build_live_registry", lambda config, paths: registry, raising=False
    )
    paths = AppPaths(tmp_path / "data", tmp_path / "cache")
    ConfigStore(paths).save(Config(onboarding_complete=True))
    app = DayboardApp(paths=paths)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "Measured" in str(app.query_one("#card-weather", SnapshotCard).render())
        assert "SYNTHETIC" not in str(app.query_one("#card-weather", SnapshotCard).render())
        await pilot.press("q")


async def test_permission_errors_are_visible_without_leaking_exception_text(tmp_path, monkeypatch):
    class Calendar:
        spec = ProviderSpec(ProviderKey("apple-calendar"), "Calendar", (Capability.CALENDAR,))

        async def collect(self):
            raise ProviderUnavailable(ErrorCode.PERMISSION_REQUIRED)

    registry = ProviderRegistry()
    registry.register(Calendar())
    monkeypatch.setattr(
        "dayboard.app.build_live_registry", lambda config, paths: registry, raising=False
    )
    paths = AppPaths(tmp_path / "data", tmp_path / "cache")
    ConfigStore(paths).save(Config(onboarding_complete=True))
    app = DayboardApp(paths=paths)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "authorize" in str(app.query_one("#card-schedule", SnapshotCard).render())
        assert "DEMO" not in str(app.query_one("#mode", Static).render())
        await pilot.press("q")


async def test_demo_never_constructs_live_registry(tmp_path, monkeypatch):
    def forbidden(*args):
        raise AssertionError("Demo cannot construct live integrations.")

    monkeypatch.setattr("dayboard.app.build_live_registry", forbidden, raising=False)
    app = DayboardApp(demo=True, paths=AppPaths(tmp_path / "data", tmp_path / "cache"))
    async with app.run_test() as pilot:
        await pilot.press("q")
