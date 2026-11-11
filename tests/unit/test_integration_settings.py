import pytest

from dayboard.config import Config, ConfigError, ConfigStore
from dayboard.paths import AppPaths
from dayboard.settings import CalendarSettings, WeatherSettings


def test_defaults_do_not_enable_any_integration():
    config = Config()
    assert config.schema_version == Config().schema_version
    assert not config.weather.enabled
    assert not config.calendar.enabled


def test_integration_preferences_round_trip_without_private_data(tmp_path):
    paths = AppPaths(tmp_path / "data", tmp_path / "cache")
    config = Config(
        weather=WeatherSettings(True, 59.3, 18.1, "Example", "Europe/Stockholm", "fahrenheit"),
        calendar=CalendarSettings(True, ("source-id",)),
    )
    ConfigStore(paths).save(config)
    assert ConfigStore(paths).load() == config
    assert not config.onboarding_complete


def test_schema_one_migrates_only_in_memory(tmp_path):
    paths = AppPaths(tmp_path / "data", tmp_path / "cache")
    paths.ensure()
    original = '{"schema_version":1,"onboarding_complete":true}'
    paths.config_file.write_text(original)
    config = ConfigStore(paths).load()
    assert config.schema_version == Config().schema_version
    assert not config.weather.enabled
    assert config.onboarding_complete
    assert paths.config_file.read_text() == original


@pytest.mark.parametrize(
    "kwargs",
    [
        {"latitude": float("nan")},
        {"longitude": 181},
        {"latitude": -91},
        {"latitude": True},
        {"temperature_unit": "kelvin"},
        {"timezone": "Missing/Zone"},
        {"enabled": "yes"},
        {"enabled": True, "label": ""},
        {"label": "bad\x1b[31m"},
    ],
)
def test_weather_settings_validate_coordinates_flags_units_and_timezone(kwargs):
    with pytest.raises(ConfigError):
        WeatherSettings(**kwargs)


@pytest.mark.parametrize(
    "payload",
    [
        {"weather": {"enabled": True, "api_key": "SYNTHETIC_SECRET"}},
        {"calendar": {"enabled": True, "access_token": "SYNTHETIC_SECRET"}},
        {"calendar": {"calendar_ids": "not a list"}},
        {"calendar": {"calendar_ids": ["a", "a"]}},
        {"weather": []},
    ],
)
def test_nested_settings_reject_unexpected_or_secret_fields(payload):
    with pytest.raises(ConfigError):
        Config.decode({"schema_version": 2, **payload})
