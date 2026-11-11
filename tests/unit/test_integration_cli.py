import pytest

from dayboard.cli import main
from dayboard.config import ConfigStore
from dayboard.paths import AppPaths


@pytest.fixture
def paths(tmp_path, monkeypatch):
    value = AppPaths(tmp_path / "data", tmp_path / "cache")
    monkeypatch.setattr(AppPaths, "default", classmethod(lambda cls: value))
    return value


def test_weather_set_persists_explicit_location_and_disable_preserves_it(paths, capsys):
    assert (
        main(
            [
                "weather",
                "set",
                "--latitude",
                "59.3",
                "--longitude",
                "18.1",
                "--name",
                "Example",
                "--timezone",
                "Europe/Stockholm",
            ]
        )
        == 0
    )
    config = ConfigStore(paths).load()
    assert config.weather.enabled
    assert config.weather.label == "Example"
    assert not config.onboarding_complete
    assert main(["weather", "disable"]) == 0
    config = ConfigStore(paths).load()
    assert not config.weather.enabled
    assert config.weather.latitude == 59.3


def test_invalid_coordinates_do_not_create_configuration(paths, capsys):
    assert (
        main(
            [
                "weather",
                "set",
                "--latitude",
                "nan",
                "--longitude",
                "18",
                "--name",
                "Bad",
            ]
        )
        == 2
    )
    assert not paths.config_file.exists()


def test_calendar_status_does_not_build_or_authorize(paths, capsys):
    assert main(["calendar", "status"]) == 1
    assert not paths.data_dir.exists()
    assert "authorize" in capsys.readouterr().out


def test_calendar_authorize_saves_only_after_grant(paths, monkeypatch, capsys):
    import dayboard.calendar_setup as module

    async def fake_build(paths):
        return paths.data_dir / "mock-bridge"

    class Reader:
        def __init__(self, path):
            pass

        async def call(self, operation):
            assert operation == "authorize"
            return {"schema_version": 1, "status": "authorized"}

    monkeypatch.setattr(module, "build_bridge", fake_build)
    monkeypatch.setattr(module, "CalendarBridge", Reader)
    assert main(["calendar", "authorize"]) == 0
    assert ConfigStore(paths).load().calendar.enabled


def test_denial_does_not_enable_calendar(paths, monkeypatch, capsys):
    import dayboard.calendar_setup as module

    async def fake_build(paths):
        return paths.data_dir / "mock-bridge"

    class Reader:
        def __init__(self, path):
            pass

        async def call(self, operation):
            return {"schema_version": 1, "status": "denied"}

    monkeypatch.setattr(module, "build_bridge", fake_build)
    monkeypatch.setattr(module, "CalendarBridge", Reader)
    assert main(["calendar", "authorize"]) == 1
    assert not paths.config_file.exists()
    assert "denied" in capsys.readouterr().out.lower()
