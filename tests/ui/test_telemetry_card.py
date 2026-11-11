from datetime import UTC, datetime

from dayboard.app import DayboardApp
from dayboard.config import Config, ConfigStore
from dayboard.core.models import ConnectionState, Metric, ProviderKey, Snapshot, SourceStatus
from dayboard.integrations.runtime import build_live_registry
from dayboard.paths import AppPaths
from dayboard.ui.telemetry import TelemetryCard, format_bytes, sparkline


def test_graphs_preserve_unknown_gaps_and_use_explicit_percent_scale():
    assert sparkline([None, 0, 50, 100], ceiling=100) == "·▁▄█"
    assert sparkline([0, 0, 0]) == "▁▁▁"
    assert len(sparkline(list(range(100)), width=32)) == 32
    assert format_bytes(1536) == "1.5 KiB"
    assert format_bytes(None) == "Unknown"


def test_default_runtime_registers_only_local_telemetry_without_cloud_setup(tmp_path):
    registry = build_live_registry(Config(), AppPaths(tmp_path / "data", tmp_path / "cache"))
    assert [provider.spec.key.provider for provider in registry.all()] == ["mac-telemetry"]
    assert not (tmp_path / "data").exists()


async def test_mac_card_shows_live_units_and_short_history(tmp_path):
    paths = AppPaths(tmp_path / "data", tmp_path / "cache")
    ConfigStore(paths).save(Config(onboarding_complete=True))
    app = DayboardApp(paths=paths)
    async with app.run_test() as pilot:
        now = datetime.now(UTC)
        reading = Snapshot(
            ProviderKey("mac-telemetry"),
            now,
            now,
            (
                Metric("cpu_percent", "CPU", 30),
                Metric("memory_percent", "RAM", 50),
                Metric("memory_used", "Used", 8 * 1024**3),
                Metric("memory_total", "Total", 16 * 1024**3),
                Metric("memory_available", "Available", 8 * 1024**3),
                Metric("network_receive", "Down", 2048),
                Metric("network_send", "Up", 1024),
                Metric("sample_status", "Sampling", "Ready"),
            ),
        )
        card = app.query_one("#card-mac", TelemetryCard)
        card.show_telemetry(SourceStatus(ConnectionState.READY, reading), (reading,))
        rendered = str(card.render())
        assert "30" in rendered
        assert "GiB" in rendered
        assert "KiB/s" in rendered
        assert "SYNTHETIC" not in rendered
        await pilot.press("q")


async def test_demo_does_not_construct_real_telemetry(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Real telemetry must not run in demo.")

    monkeypatch.setattr("dayboard.integrations.telemetry.TelemetryProvider.__init__", forbidden)
    app = DayboardApp(demo=True, paths=AppPaths(tmp_path / "data", tmp_path / "cache"))
    async with app.run_test() as pilot:
        assert all(provider.spec.synthetic for provider in app.registry.all())
        await pilot.press("q")
