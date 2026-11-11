from datetime import UTC, datetime

import pytest
from textual.widgets import Static

from dayboard.app import DayboardApp
from dayboard.cli import main
from dayboard.config import Config, ConfigStore
from dayboard.core.models import Metric, ProviderKey, Snapshot
from dayboard.core.providers import Capability, ProviderRegistry, ProviderSpec
from dayboard.integrations.runtime import build_live_registry
from dayboard.paths import AppPaths
from dayboard.settings import GitHubSettings


@pytest.fixture
def paths(tmp_path, monkeypatch):
    paths = AppPaths(tmp_path / "data", tmp_path / "cache")
    monkeypatch.setattr(AppPaths, "default", classmethod(lambda cls: paths))
    return paths


def test_github_set_and_disable_never_contact_api_or_read_credentials(paths, monkeypatch, capsys):
    async def forbidden(*args, **kwargs):
        raise AssertionError("Setup must not query GitHub or read credentials.")

    monkeypatch.setattr("dayboard.integrations.github.transport.GitHubCLI.fetch", forbidden)
    assert main(["github", "set", "--repo", "owner/repo", "--repo", "owner/second"]) == 0
    config = ConfigStore(paths).load()
    assert config.github == GitHubSettings(True, ("owner/repo", "owner/second"))
    assert not config.onboarding_complete
    assert main(["github", "status"]) == 0
    assert main(["github", "disable"]) == 0
    config = ConfigStore(paths).load()
    assert not config.github.enabled
    assert config.github.repositories == ("owner/repo", "owner/second")


def test_github_invalid_repository_cannot_write_config(paths, capsys):
    assert main(["github", "set", "--repo", "https://github.com/owner/repo"]) == 2
    assert not paths.config_file.exists()


def test_disabled_github_status_does_not_create_state(paths, capsys):
    assert main(["github", "status"]) == 0
    assert not paths.data_dir.exists()
    assert "disabled" in capsys.readouterr().out.lower()


def test_only_configured_github_is_registered(paths):
    assert not any(
        p.spec.key.provider == "github" for p in build_live_registry(Config(), paths).all()
    )
    registry = build_live_registry(Config(github=GitHubSettings(True, ("owner/repo",))), paths)
    assert any(p.spec.key.provider == "github" for p in registry.all())
    assert not paths.data_dir.exists()


async def test_live_github_card_uses_collected_metadata_without_sample_values(paths, monkeypatch):
    class Provider:
        spec = ProviderSpec(ProviderKey("github"), "GitHub", (Capability.GITHUB,))

        async def collect(self):
            now = datetime.now(UTC)
            return Snapshot(
                self.spec.key,
                now,
                now,
                (
                    Metric("pr", "owner/repo #3", "Live title · DRAFT · checks PENDING"),
                    Metric("commit", "owner/repo abcdef0", "Recent real metadata"),
                ),
            )

    registry = ProviderRegistry()
    registry.register(Provider())
    monkeypatch.setattr("dayboard.app.build_live_registry", lambda config, paths: registry)
    ConfigStore(paths).save(Config(onboarding_complete=True))
    app = DayboardApp(paths=paths)
    async with app.run_test() as pilot:
        await pilot.pause()
        rendered = str(app.query_one("#card-github", Static).render())
        assert "Live title" in rendered and "abcdef0" in rendered
        assert "SYNTHETIC" not in rendered
        await pilot.press("q")


async def test_demo_never_constructs_github_reader_even_if_configured(paths, monkeypatch):
    ConfigStore(paths).save(Config(github=GitHubSettings(True, ("owner/repo",))))

    def forbidden(*args, **kwargs):
        raise AssertionError("Demo must not construct GitHub readers.")

    monkeypatch.setattr("dayboard.integrations.github.provider.GitHubCLI", forbidden)
    app = DayboardApp(demo=True, paths=paths)
    async with app.run_test() as pilot:
        assert all(p.spec.synthetic for p in app.registry.all())
        await pilot.press("q")
