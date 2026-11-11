import json
from importlib.metadata import version

import pytest

from dayboard.cli import main
from dayboard.config import AccountRef, Config, ConfigStore
from dayboard.paths import AppPaths


@pytest.fixture
def paths(tmp_path, monkeypatch):
    result = AppPaths(tmp_path / "data", tmp_path / "cache")
    monkeypatch.setattr(AppPaths, "default", classmethod(lambda cls: result))
    return result


def test_doctor_missing_configuration_does_not_create_state(paths, capsys):
    assert main(["doctor", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["configuration"] == "missing"
    assert result["account_count"] == 0
    assert result["keychain_access"] == "not tested (no credentials read)"
    assert not paths.data_dir.exists()


def test_doctor_omits_account_identity_and_raw_configuration(paths, capsys):
    ConfigStore(paths).save(Config(accounts=(AccountRef("private-id", "google", "PRIVATE_LABEL"),)))
    assert main(["doctor", "--json"]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["account_count"] == 1
    assert "private-id" not in output
    assert "PRIVATE_LABEL" not in output


def test_corrupt_configuration_is_reported_without_contents(paths, capsys):
    paths.ensure()
    paths.config_file.write_text('{"access_token":"SYNTHETIC_SECRET","schema_version":999}')
    assert main(["doctor", "--json"]) == 1
    result = capsys.readouterr().out
    assert json.loads(result)["configuration"] == "invalid"
    assert "SYNTHETIC_SECRET" not in result


def test_invalid_configuration_does_not_launch_ui_or_emit_traceback(paths, capsys):
    paths.ensure()
    paths.config_file.write_text('{"secret":"SYNTHETIC_SECRET"}')
    assert main([]) == 2
    error = capsys.readouterr().err
    assert "configuration" in error.lower()
    assert "SYNTHETIC_SECRET" not in error
    assert "Traceback" not in error


def test_keychain_diagnostic_failure_is_redacted(paths, monkeypatch, capsys):
    import dayboard.diagnostics as module

    def broken():
        raise RuntimeError("SYNTHETIC_SECRET")

    monkeypatch.setattr(module, "native_backend", broken)
    assert main(["doctor", "--json"]) == 1
    output = capsys.readouterr().out
    assert json.loads(output)["keychain_backend"] == "unavailable"
    assert "SYNTHETIC_SECRET" not in output


def test_version_needs_no_state(paths, capsys):
    with pytest.raises(SystemExit) as result:
        main(["--version"])
    assert result.value.code == 0
    assert f"dayboard {version('dayboard')}" in capsys.readouterr().out
    assert not paths.data_dir.exists()
