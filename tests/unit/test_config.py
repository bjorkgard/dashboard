import json
import os
import stat
from dataclasses import dataclass

import pytest

from dayboard.config import AccountRef, Config, ConfigError, ConfigStore
from dayboard.paths import AppPaths, PathSecurityError


@pytest.fixture
def paths(tmp_path):
    return AppPaths(tmp_path / "data", tmp_path / "cache")


def test_missing_configuration_is_not_a_completed_setup(paths):
    assert ConfigStore(paths).load() is None
    assert not paths.data_dir.exists()


def test_round_trip_keeps_only_account_references(paths):
    config = Config(accounts=(AccountRef("work", "google", "Work"),), onboarding_complete=True)
    store = ConfigStore(paths)
    store.save(config)
    assert store.load() == config
    raw = json.loads(paths.config_file.read_text())
    assert raw["accounts"] == [{"id": "work", "provider": "google", "label": "Work"}]


def test_private_directory_and_file_permissions(paths):
    ConfigStore(paths).save(Config())
    assert stat.S_IMODE(paths.data_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(paths.cache_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(paths.config_file.stat().st_mode) == 0o600


def test_atomic_write_failure_preserves_old_config(paths, monkeypatch):
    import dayboard.paths as path_module

    store = ConfigStore(paths)
    store.save(Config())
    before = paths.config_file.read_bytes()

    def fail_replace(*args, **kwargs):
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(path_module.os, "replace", fail_replace)
    with pytest.raises(ConfigError, match="save"):
        store.save(Config(onboarding_complete=True))
    assert paths.config_file.read_bytes() == before
    assert list(paths.data_dir.iterdir()) == [paths.config_file]


def test_config_symlink_is_rejected_without_touching_target(paths, tmp_path):
    paths.ensure()
    target = tmp_path / "outside"
    target.write_text("do not change")
    paths.config_file.symlink_to(target)
    with pytest.raises(ConfigError):
        ConfigStore(paths).save(Config())
    with pytest.raises(ConfigError):
        ConfigStore(paths).load()
    assert target.read_text() == "do not change"


def test_directory_symlink_is_rejected(tmp_path):
    target = tmp_path / "outside"
    target.mkdir()
    link = tmp_path / "data"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(PathSecurityError):
        AppPaths(link, tmp_path / "cache").ensure()


def test_known_schema_zero_migrates_without_overwriting_source(paths):
    paths.ensure()
    old = '{"schema_version":0,"setup_complete":true,"accounts":[]}'
    paths.config_file.write_text(old)
    config = ConfigStore(paths).load()
    assert config == Config(onboarding_complete=True)
    assert paths.config_file.read_text() == old
    ConfigStore(paths).save(config)
    assert json.loads(paths.config_file.read_text())["schema_version"] == Config().schema_version


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 999},
        {"schema_version": 1, "access_token": "SYNTHETIC_PRIVATE_VALUE"},
        {"schema_version": 1, "accounts": [{"id": "a", "provider": "x", "password": "fake"}]},
        {"schema_version": 1, "onboarding_complete": "yes"},
        {"schema_version": 1, "accounts": "not a list"},
        {"schema_version": True},
        {
            "schema_version": 1,
            "accounts": [
                {"id": "same", "provider": "google", "label": "One"},
                {"id": "same", "provider": "google", "label": "Two"},
            ],
        },
    ],
)
def test_invalid_or_secret_bearing_configuration_is_rejected(paths, payload):
    paths.ensure()
    paths.config_file.write_text(json.dumps(payload))
    with pytest.raises(ConfigError) as error:
        ConfigStore(paths).load()
    assert "SYNTHETIC_PRIVATE_VALUE" not in str(error.value)


def test_invalid_json_is_not_overwritten(paths):
    paths.ensure()
    paths.config_file.write_text("{broken")
    with pytest.raises(ConfigError):
        ConfigStore(paths).load()
    assert paths.config_file.read_text() == "{broken"


def test_config_rejects_invalid_values_before_saving(paths):
    with pytest.raises(ConfigError):
        Config(theme="unknown")
    with pytest.raises(ConfigError):
        AccountRef("../bad", "google", "Bad")


def test_existing_overly_broad_permissions_are_tightened(paths):
    paths.ensure()
    paths.data_dir.chmod(0o755)
    ConfigStore(paths).save(Config())
    assert stat.S_IMODE(paths.data_dir.stat().st_mode) == 0o700


def test_hard_linked_config_is_rejected_without_reading_or_overwriting(paths, tmp_path):
    paths.ensure()
    outside = tmp_path / "outside.json"
    outside.write_text('{"schema_version":1}')
    os.link(outside, paths.config_file)
    with pytest.raises(ConfigError):
        ConfigStore(paths).load()
    with pytest.raises(ConfigError):
        ConfigStore(paths).save(Config())
    assert outside.read_text() == '{"schema_version":1}'


def test_oversized_config_is_rejected(paths):
    paths.ensure()
    paths.config_file.write_bytes(b" " * 1_048_577)
    with pytest.raises(ConfigError):
        ConfigStore(paths).load()


def test_non_regular_config_is_rejected_without_blocking(paths):
    paths.ensure()
    os.mkfifo(paths.config_file)
    with pytest.raises(ConfigError):
        ConfigStore(paths).load()


def test_config_save_serializes_only_allowlisted_fields(paths):
    @dataclass(frozen=True)
    class ExtendedConfig(Config):
        access_token: str = "SYNTHETIC_SECRET"

    ConfigStore(paths).save(ExtendedConfig())
    raw = paths.config_file.read_text()
    assert "SYNTHETIC_SECRET" not in raw
    assert "access_token" not in raw
    assert ConfigStore(paths).load() == Config()
