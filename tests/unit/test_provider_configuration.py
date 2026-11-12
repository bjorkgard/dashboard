import json
from dataclasses import replace

import pytest

from dayboard.config import Config, ConfigError, ConfigStore
from dayboard.paths import AppPaths
from dayboard.settings import (
    CodexSettings,
    KiroSettings,
    MailboxSelection,
    MailSettings,
    WarpSettings,
)
from dayboard.settings_editor import SettingsDraft, build_candidate, save_draft


@pytest.fixture
def store(tmp_path):
    return ConfigStore(AppPaths(tmp_path / "data", tmp_path / "cache"))


@pytest.mark.parametrize("version", range(5))
def test_schema5_migrates_without_enabling_or_rewriting_sources(store, version):
    store.paths.ensure()
    before = json.dumps(
        {"schema_version": version, "onboarding_complete": True}
        if version
        else {"schema_version": 0, "setup_complete": True}
    )
    store.paths.config_file.write_text(before)
    config = store.load()
    assert config.schema_version == 5 and config.onboarding_complete
    assert not any(
        (config.mail.enabled, config.codex.enabled, config.kiro.enabled, config.warp.enabled)
    )
    assert store.paths.config_file.read_text() == before


def test_roundtrip_and_disabled_fields_preserve_prior_scope(store):
    config = Config(
        mail=MailSettings(True, (MailboxSelection("account", ("Inbox",)),)),
        codex=CodexSettings(True, "/codex", "UTC", True),
        kiro=KiroSettings(True, "/kiro"),
        warp=WarpSettings(True),
    )
    store.save(config)
    assert store.load() == config
    candidate = build_candidate(config, SettingsDraft())
    assert candidate.mail == replace(config.mail, enabled=False)
    assert candidate.codex == replace(config.codex, enabled=False, limits_enabled=False)
    assert candidate.kiro == replace(config.kiro, enabled=False)
    assert candidate.warp == replace(config.warp, enabled=False)


def test_external_new_provider_changes_are_detected(store):
    initial = Config()
    store.save(replace(initial, kiro=KiroSettings(False, "/selected")))
    with pytest.raises(ConfigError, match="changed"):
        save_draft(store, initial, SettingsDraft())


def test_new_sources_validate_without_echoing_invalid_input():
    for draft in (
        SettingsDraft(codex_enabled=True, codex_source="PRIVATE_CANARY"),
        SettingsDraft(kiro_enabled=True, kiro_source="PRIVATE_CANARY"),
        SettingsDraft(mail_enabled=True),
    ):
        with pytest.raises(ConfigError) as error:
            build_candidate(Config(), draft)
        assert "PRIVATE_CANARY" not in str(error.value)
