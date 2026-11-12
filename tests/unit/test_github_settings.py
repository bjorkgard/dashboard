import json

import pytest

from dayboard.config import Config, ConfigError, ConfigStore
from dayboard.paths import AppPaths
from dayboard.settings import GitHubSettings


def test_github_is_disabled_until_repositories_are_selected():
    assert not Config().github.enabled
    assert Config().schema_version == 4
    assert Config().github.repositories == ()


@pytest.mark.parametrize(
    "repositories",
    [
        (),
        ("https://github.com/owner/repo",),
        ("../repo",),
        ("owner/repo;whoami",),
        ("owner/repo?token=fake",),
        ("owner/repo", "OWNER/REPO"),
        tuple(f"owner/repo-{index}" for index in range(6)),
    ],
)
def test_invalid_empty_duplicate_or_excessive_repositories_are_rejected(repositories):
    with pytest.raises(ConfigError):
        GitHubSettings(True, repositories)


def test_only_nonsecret_repository_settings_are_saved(tmp_path):
    paths = AppPaths(tmp_path / "data", tmp_path / "cache")
    config = Config(github=GitHubSettings(True, ("owner/repo",)))
    ConfigStore(paths).save(config)
    assert ConfigStore(paths).load() == config
    raw = json.loads(paths.config_file.read_text())
    assert raw["github"] == {"enabled": True, "repositories": ["owner/repo"]}
    with pytest.raises(ConfigError):
        Config.decode({"schema_version": 3, "github": {"token": "SYNTHETIC_SECRET"}})


def test_schema_two_preserves_existing_integrations_without_enabling_github():
    config = Config.decode(
        {
            "schema_version": 2,
            "weather": {"enabled": True, "latitude": 1, "longitude": 2, "label": "Example"},
            "calendar": {"enabled": True},
        }
    )
    assert config.schema_version == 4
    assert config.weather.enabled
    assert config.calendar.enabled
    assert not config.github.enabled
