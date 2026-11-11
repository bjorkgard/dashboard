from dataclasses import replace

import pytest

from dayboard.config import Config, ConfigError, ConfigStore
from dayboard.paths import AppPaths
from dayboard.settings import CalendarSettings, GitHubSettings, WeatherSettings
from dayboard.settings_editor import (
    SettingsDraft,
    SettingsValidationError,
    build_candidate,
    save_draft,
)


def test_enabled_fields_are_validated_and_unrelated_preferences_preserved():
    original = Config(
        theme="light", reduced_motion=True, calendar=CalendarSettings(False, ("work",))
    )
    draft = SettingsDraft(
        calendar_enabled=True,
        weather_enabled=True,
        weather_label="Example",
        latitude="59.3",
        longitude="18.1",
        timezone="Europe/Stockholm",
        units="fahrenheit",
        github_enabled=True,
        repositories="owner/one,\nowner/two",
    )
    candidate = build_candidate(original, draft)
    assert candidate.onboarding_complete
    assert candidate.theme == "light" and candidate.reduced_motion
    assert candidate.calendar == CalendarSettings(True, ("work",))
    assert candidate.github == GitHubSettings(True, ("owner/one", "owner/two"))
    assert candidate.weather.temperature_unit == "fahrenheit"


def test_disabled_sections_preserve_saved_values_and_ignore_unfinished_inputs():
    original = Config(
        weather=WeatherSettings(True, 1, 2, "Saved", "UTC"),
        github=GitHubSettings(True, ("owner/saved",)),
    )
    result = build_candidate(original, SettingsDraft(latitude="broken", repositories="broken"))
    assert result.weather == replace(original.weather, enabled=False)
    assert result.github == replace(original.github, enabled=False)


@pytest.mark.parametrize(
    "draft,section",
    [
        (
            SettingsDraft(weather_enabled=True, weather_label="X", latitude="nan", longitude="2"),
            "Weather",
        ),
        (
            SettingsDraft(
                weather_enabled=True, weather_label="X", latitude="SECRET", longitude="2"
            ),
            "Weather",
        ),
        (SettingsDraft(github_enabled=True, repositories="https://github.com/o/r"), "GitHub"),
    ],
)
def test_validation_identifies_section_without_echoing_input(draft, section):
    with pytest.raises(SettingsValidationError) as error:
        build_candidate(Config(), draft)
    assert error.value.section == section
    assert "SECRET" not in str(error.value)


def test_save_detects_external_integration_edits_without_overwriting(tmp_path):
    store = ConfigStore(AppPaths(tmp_path / "data", tmp_path / "cache"))
    original = Config()
    store.save(original)
    external = replace(original, github=GitHubSettings(True, ("other/repo",)))
    store.save(external)
    with pytest.raises(ConfigError, match="changed"):
        save_draft(store, original, SettingsDraft())
    assert store.load() == external


def test_save_preserves_external_unrelated_preferences(tmp_path):
    store = ConfigStore(AppPaths(tmp_path / "data", tmp_path / "cache"))
    original = Config()
    store.save(replace(original, theme="light"))
    saved = save_draft(store, original, SettingsDraft())
    assert saved.theme == "light"
    assert saved.onboarding_complete
    assert store.load() == saved


def test_failed_save_leaves_file_intact(tmp_path, monkeypatch):
    store = ConfigStore(AppPaths(tmp_path / "data", tmp_path / "cache"))
    original = Config()
    store.save(original)

    def fail(config):
        raise ConfigError("Cannot save protected configuration.")

    monkeypatch.setattr(store, "save", fail)
    with pytest.raises(ConfigError):
        save_draft(store, original, SettingsDraft())
    assert store.load() == original
