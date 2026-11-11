from datetime import UTC, datetime

import pytest
from textual.widgets import Static

from dayboard.app import DayboardApp
from dayboard.config import Config, ConfigError, ConfigStore
from dayboard.core.models import Metric, ProviderKey, Snapshot
from dayboard.paths import AppPaths
from dayboard.ui.card import SnapshotCard
from dayboard.ui.welcome import WelcomeScreen


@pytest.fixture
def paths(tmp_path):
    return AppPaths(tmp_path / "data", tmp_path / "cache")


def text(widget):
    return str(widget.render())


async def test_header_version_comes_from_installed_package_metadata(paths, monkeypatch):
    import dayboard.app as module

    monkeypatch.setattr(module, "version", lambda name: "9.8.7rc1", raising=False)
    app = DayboardApp(demo=True, paths=paths)
    async with app.run_test() as pilot:
        assert "9.8.7rc1" in text(app.query_one("#release", Static))
        await pilot.press("q")


async def test_demo_is_isolated_even_with_broken_real_config(paths, monkeypatch):
    paths.ensure()
    paths.config_file.write_text('{"secret":"SYNTHETIC_SECRET","broken":true}')
    before = paths.config_file.read_bytes()

    def forbidden(*args, **kwargs):
        raise AssertionError("Demo must not touch real state.")

    monkeypatch.setattr(ConfigStore, "load", forbidden)
    monkeypatch.setattr(ConfigStore, "save", forbidden)
    app = DayboardApp(demo=True, paths=paths)
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        assert not isinstance(app.screen, WelcomeScreen)
        assert "DEMO" in text(app.query_one("#mode", Static))
        assert all(provider.spec.synthetic for provider in app.registry.all())
        assert app.store.history(app.registry.all()[0].spec.key)
        assert "SYNTHETIC_SECRET" not in app.export_screenshot()
        await pilot.press("q")
    assert paths.config_file.read_bytes() == before
    assert app.scheduler.running_count == 0


async def test_collectors_stop_when_application_is_unmounted_without_quit(paths):
    app = DayboardApp(demo=True, paths=paths)
    async with app.run_test():
        assert app.scheduler.running_count == 6
    assert app.scheduler.running_count == 0


async def test_demo_cards_show_samples_on_the_first_frame(paths):
    app = DayboardApp(demo=True, paths=paths)
    async with app.run_test() as pilot:
        assert "SYNTHETIC" in text(app.query_one("#card-mac", SnapshotCard))
        assert "Not connected" not in text(app.query_one("#card-mac", SnapshotCard))
        await pilot.press("q")


async def test_card_does_not_mislabel_reported_reading_as_synthetic(paths):
    ConfigStore(paths).save(Config(onboarding_complete=True))
    app = DayboardApp(paths=paths)
    async with app.run_test() as pilot:
        now = datetime.now(UTC)
        card = app.query_one("#card-mac", SnapshotCard)
        card.show_snapshot(
            Snapshot(
                ProviderKey("test"),
                now,
                now,
                (Metric("unknown", "[bold]External\x1b[31m", None),),
            )
        )
        rendered = text(card)
        assert "SYNTHETIC" not in rendered
        assert "Unknown" in rendered
        assert "[bold]External" in rendered
        assert "\x1b" not in rendered
        await pilot.press("q")


async def test_demo_never_creates_state_or_opens_keychain(paths, monkeypatch):
    import dayboard.security.keychain as module

    def forbidden(*args, **kwargs):
        raise AssertionError("Keychain must not be accessed.")

    monkeypatch.setattr(module, "native_backend", forbidden)
    app = DayboardApp(demo=True, paths=paths)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")
    assert not paths.data_dir.exists()
    assert not paths.cache_dir.exists()


async def test_missing_config_opens_onboarding_and_quit_does_not_save(paths):
    app = DayboardApp(paths=paths)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, WelcomeScreen)
        assert "milestone 1" in text(app.screen.query_one("#welcome-copy", Static)).lower()
        await pilot.press("q")
    assert not paths.config_file.exists()


async def test_continue_without_accounts_saves_only_nonsecret_preferences(paths):
    app = DayboardApp(paths=paths)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#continue")
        await pilot.pause()
        assert not isinstance(app.screen, WelcomeScreen)
        assert ConfigStore(paths).load() == Config(onboarding_complete=True)
        assert not app.registry.all()
        assert "Not connected" in text(app.query_one("#card-mac", Static))
        assert "DEMO" not in text(app.query_one("#mode", Static))
        await pilot.press("q")


async def test_save_failure_leaves_onboarding_open_and_redacts_backend_error(paths, monkeypatch):
    def fail(*args):
        raise ConfigError("Cannot save protected configuration.")

    monkeypatch.setattr(ConfigStore, "save", fail)
    app = DayboardApp(paths=paths)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#continue")
        await pilot.pause()
        assert isinstance(app.screen, WelcomeScreen)
        assert "Cannot save" in text(app.screen.query_one("#setup-error", Static))
        await pilot.press("q")


@pytest.mark.parametrize("size", [(80, 24), (120, 36), (160, 48)])
async def test_shell_renders_and_exits_at_common_terminal_sizes(paths, size):
    ConfigStore(paths).save(Config(onboarding_complete=True))
    app = DayboardApp(paths=paths)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        assert app.query_one("#clock", Static).region.width > 0
        assert not app.registry.all()
        await pilot.press("q")
    assert app.scheduler.running_count == 0
