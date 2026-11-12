from dataclasses import replace

import pytest

from dayboard.config import Config, ConfigError, ConfigStore
from dayboard.paths import AppPaths
from dayboard.security.keychain import Keychain
from dayboard.security.warp_credentials import disconnect_warp, save_with_warp_key
from dayboard.settings import WarpSettings
from dayboard.settings_editor import SettingsDraft


class Backend:
    def __init__(self):
        self.values = {}
        self.fail_delete = False

    def get_password(self, service, username):
        return self.values.get((service, username))

    def set_password(self, service, username, password):
        self.values[service, username] = password

    def delete_password(self, service, username):
        if self.fail_delete:
            raise OSError("PRIVATE_BACKEND_CANARY")
        self.values.pop((service, username), None)


@pytest.fixture
def environment(tmp_path):
    store = ConfigStore(AppPaths(tmp_path / "data", tmp_path / "cache"))
    backend = Backend()
    keychain = Keychain(backend=backend)
    old = Config(warp=WarpSettings(True, "old-key"))
    keychain.set("warp", "old-key", "OLD_FAKE_CANARY")
    store.save(old)
    return store, backend, keychain, old


def test_key_rotation_stages_new_id_and_tracks_only_nonsecret_refs(environment):
    store, backend, keys, old = environment
    result = save_with_warp_key(store, old, old, "NEW_FAKE_CANARY", keychain=keys)
    assert result.warp.credential_id != old.warp.credential_id
    assert result.warp.retired_credential_ids == ("old-key",)
    assert keys.get("warp", "old-key") == "OLD_FAKE_CANARY"
    assert keys.get("warp", result.warp.credential_id) == "NEW_FAKE_CANARY"
    assert "CANARY" not in store.paths.config_file.read_text()
    assert "CANARY" not in repr(SettingsDraft(warp_key="NEW_FAKE_CANARY"))


def test_failed_config_write_deletes_staged_key_without_touching_working_key(
    environment, monkeypatch
):
    store, backend, keys, old = environment
    before = dict(backend.values)

    def failed(config):
        raise ConfigError("Cannot save protected configuration.")

    monkeypatch.setattr(store, "save", failed)
    with pytest.raises(ConfigError):
        save_with_warp_key(store, old, old, "NEW_FAKE_CANARY", keychain=keys)
    assert backend.values == before
    assert store.load() == old


def test_disconnect_only_deletes_dayboard_keys_after_disabling_source(environment):
    store, backend, keys, old = environment
    backend.values["not.dayboard", "foreign"] = "OTHER_FAKE_CANARY"
    result, complete = disconnect_warp(store, old, keychain=keys)
    assert complete and not result.warp.enabled
    assert result.warp.credential_id == "default"
    assert keys.get("warp", "old-key") is None
    assert backend.values["not.dayboard", "foreign"] == "OTHER_FAKE_CANARY"
    assert store.load() == result


def test_keychain_delete_failure_still_disables_and_keeps_refs_for_retry(environment):
    store, backend, keys, old = environment
    backend.fail_delete = True
    result, complete = disconnect_warp(store, old, keychain=keys)
    assert not complete and not result.warp.enabled
    assert result.warp.credential_id == "old-key"
    assert store.load() == result


def test_disabled_save_does_not_touch_keychain(environment, monkeypatch):
    store, _, _, old = environment

    def forbidden():
        raise AssertionError("No Keychain access for disabled source")

    monkeypatch.setattr("dayboard.security.warp_credentials.Keychain", forbidden)
    disabled = replace(old, warp=replace(old.warp, enabled=False))
    assert save_with_warp_key(store, disabled, old, "IGNORED_DRAFT") == disabled


def test_post_commit_failure_preserves_the_credential_referenced_by_disk(environment, monkeypatch):
    store, _, keys, old = environment
    original = store.save

    def commit_then_fail(config):
        original(config)
        raise ConfigError("Directory durability check failed after replacement.")

    monkeypatch.setattr(store, "save", commit_then_fail)
    result = save_with_warp_key(store, old, old, "NEW_FAKE_CANARY", keychain=keys)
    assert store.load() == result
    assert keys.get("warp", result.warp.credential_id) == "NEW_FAKE_CANARY"
    assert keys.get("warp", old.warp.credential_id) == "OLD_FAKE_CANARY"


def test_uncertain_commit_outcome_keeps_staged_key_instead_of_destructive_rollback(
    environment, monkeypatch
):
    store, backend, keys, old = environment

    def fail(*args):
        raise ConfigError("Cannot determine commit outcome")

    monkeypatch.setattr(store, "save", fail)
    monkeypatch.setattr(store, "load", fail)
    with pytest.raises(ConfigError, match="uncertain"):
        save_with_warp_key(store, old, old, "NEW_FAKE_CANARY", keychain=keys)
    assert len(backend.values) == 2
    assert keys.get("warp", "old-key") == "OLD_FAKE_CANARY"
