import asyncio
import threading
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from textual.widgets import Button, Input, Select, Static, Switch

from dayboard.app import DayboardApp
from dayboard.config import Config, ConfigError, ConfigStore
from dayboard.core.models import Metric, ProviderKey, Snapshot
from dayboard.core.providers import Capability, ProviderRegistry, ProviderSpec
from dayboard.integrations.geocoding import LocationChoice
from dayboard.paths import AppPaths
from dayboard.settings import CalendarSettings, GitHubSettings, WeatherSettings
from dayboard.ui.settings import SettingsScreen
from dayboard.ui.welcome import WelcomeScreen


class LocalProvider:
    spec = ProviderSpec(ProviderKey("mac-telemetry"), "Test local", (Capability.SYSTEM,))

    async def collect(self):
        now = datetime.now(UTC)
        return Snapshot(self.spec.key, now, now, (Metric("cpu_percent", "CPU", 1),))


@pytest.fixture
def environment(tmp_path, monkeypatch):
    paths = AppPaths(tmp_path / "data", tmp_path / "cache")
    store = ConfigStore(paths)
    store.save(Config(onboarding_complete=True))

    def registry(config, paths):
        registry = ProviderRegistry()
        registry.register(LocalProvider())
        return registry

    async def status(paths):
        return {"schema_version": 1, "status": "not_built"}

    async def forbidden(*args):
        raise AssertionError("No real search or permission request is allowed in UI tests.")

    monkeypatch.setattr("dayboard.app.build_live_registry", registry)
    monkeypatch.setattr("dayboard.ui.settings.calendar_status", status)
    monkeypatch.setattr("dayboard.ui.settings.authorize_calendar", forbidden)
    monkeypatch.setattr("dayboard.ui.settings.Geocoder.search", forbidden)
    return paths, store


async def open_settings(app, pilot):
    await pilot.press("s")
    await pilot.pause()
    assert isinstance(app.screen, SettingsScreen)
    return app.screen


async def test_shortcut_and_visible_button_open_settings_and_cancel_changes_nothing(environment):
    paths, store = environment
    before = store.load()
    app = DayboardApp(paths=paths)
    async with app.run_test() as pilot:
        assert await pilot.click("#settings-open")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, SettingsScreen)
        screen.query_one("#github-repos", Input).value = "edited/repo"
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, SettingsScreen)
        assert store.load() == before
        await open_settings(app, pilot)
        await pilot.press("escape")
        await pilot.press("q")


@pytest.mark.parametrize("size", [(80, 24), (120, 36), (160, 48)])
async def test_actions_are_visible_and_keyboard_cancel_does_not_exit_app(environment, size):
    paths, _ = environment
    app = DayboardApp(paths=paths)
    async with app.run_test(size=size) as pilot:
        screen = await open_settings(app, pilot)
        for identifier in ("settings-save", "settings-cancel"):
            region = screen.query_one(f"#{identifier}", Button).region
            assert region.y >= 0 and region.bottom <= size[1]
        await pilot.press("tab", "tab", "escape")
        await pilot.pause()
        assert not isinstance(app.screen, SettingsScreen)
        assert app.scheduler.running_count == 1
        await pilot.press("q")


async def test_saved_values_load_and_text_q_is_not_an_app_quit(environment):
    paths, store = environment
    config = Config(
        onboarding_complete=True,
        github=GitHubSettings(True, ("owner/saved",)),
        weather=WeatherSettings(True, 1, 2, "Saved", "Europe/Stockholm"),
        calendar=CalendarSettings(False, ("existing-filter",)),
    )
    store.save(config)
    app = DayboardApp(paths=paths)
    async with app.run_test() as pilot:
        screen = await open_settings(app, pilot)
        assert screen.query_one("#weather-name", Input).value == "Saved"
        assert screen.query_one("#weather-timezone", Input).value == "Europe/Stockholm"
        assert screen.query_one("#github-repos", Input).value == "owner/saved"
        field = screen.query_one("#github-repos", Input)
        field.focus()
        field.value = ""
        await pilot.press("q")
        assert field.value == "q"
        assert isinstance(app.screen, SettingsScreen)
        await pilot.press("escape")
        await pilot.press("q")


async def test_save_applies_without_exiting_and_replaces_collectors_and_old_scope(environment):
    paths, store = environment
    store.save(Config(onboarding_complete=True, github=GitHubSettings(True, ("owner/old",))))
    app = DayboardApp(paths=paths)
    async with app.run_test() as pilot:
        await pilot.pause()
        old_scheduler, old_store = app.scheduler, app.store
        now = datetime.now(UTC)
        old_store.publish(
            Snapshot(ProviderKey("github"), now, now, (Metric("old", "Old", "OLD_SCOPE"),))
        )
        app.query_one("#card-github", Static).update("OLD_SCOPE")
        screen = await open_settings(app, pilot)
        screen.query_one("#github-enabled", Switch).value = False
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert not isinstance(app.screen, SettingsScreen)
        assert app.scheduler is not old_scheduler and old_scheduler.running_count == 0
        assert app.store is not old_store
        assert not app.store.history(ProviderKey("github"))
        assert "OLD_SCOPE" not in str(app.query_one("#card-github", Static).render())
        assert not store.load().github.enabled
        assert store.load().github.repositories == ("owner/old",)
        assert app.scheduler.running_count == 1
        await pilot.press("q")


async def test_validation_and_failed_save_leave_form_and_collectors_unchanged(
    environment, monkeypatch
):
    paths, store = environment
    app = DayboardApp(paths=paths)
    async with app.run_test() as pilot:
        original_scheduler = app.scheduler
        screen = await open_settings(app, pilot)
        screen.query_one("#weather-enabled", Switch).value = True
        screen.query_one("#weather-latitude", Input).value = "SECRET_INPUT"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, SettingsScreen)
        message = str(screen.query_one("#settings-message", Static).render())
        assert "Weather" in message and "SECRET_INPUT" not in message
        screen.query_one("#weather-enabled", Switch).value = False

        def fail(*args):
            raise ConfigError("Cannot save protected configuration.")

        monkeypatch.setattr("dayboard.ui.settings.save_draft", fail)
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert "Cannot save" in str(screen.query_one("#settings-message", Static).render())
        assert app.scheduler is original_scheduler
        await pilot.press("escape")
        await pilot.press("q")


async def test_external_integration_edit_is_not_overwritten(environment):
    paths, store = environment
    app = DayboardApp(paths=paths)
    async with app.run_test() as pilot:
        screen = await open_settings(app, pilot)
        external = replace(store.load(), github=GitHubSettings(True, ("other/repo",)))
        store.save(external)
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert "changed" in str(screen.query_one("#settings-message", Static).render())
        assert store.load() == external
        await pilot.press("escape")
        screen = await open_settings(app, pilot)
        assert screen.query_one("#github-repos", Input).value == "other/repo"
        await pilot.press("escape")
        await pilot.press("q")


async def test_search_never_auto_selects_or_saves_and_choice_populates_fields(
    environment, monkeypatch
):
    paths, store = environment
    calls = []
    choices = (
        LocationChoice("Example, North", 1, 2, "UTC"),
        LocationChoice("Example, South", 3, 4, "Europe/Stockholm"),
    )

    async def search(self, query):
        calls.append(query)
        return choices

    monkeypatch.setattr("dayboard.ui.settings.Geocoder.search", search)
    app = DayboardApp(paths=paths)
    async with app.run_test() as pilot:
        screen = await open_settings(app, pilot)
        assert calls == []
        screen.query_one("#location-query", Input).value = "Example"
        screen.query_one("#location-search", Button).press()
        await pilot.pause()
        selected = screen.query_one("#location-choice", Select)
        assert calls == ["Example"]
        assert not isinstance(selected.value, int)
        assert screen.query_one("#weather-name", Input).value == ""
        assert not store.load().weather.enabled
        selected.value = 1
        await pilot.pause()
        assert screen.query_one("#weather-name", Input).value == "Example, South"
        assert screen.query_one("#weather-latitude", Input).value == "3"
        assert screen.query_one("#weather-timezone", Input).value == "Europe/Stockholm"
        assert screen.query_one("#weather-enabled", Switch).value
        await pilot.press("escape")
        assert not store.load().weather.enabled
        await pilot.press("q")


async def test_calendar_only_authorizes_on_explicit_button_and_requires_save(
    environment, monkeypatch
):
    paths, store = environment
    calls = []

    async def authorize(paths):
        calls.append("authorize")
        return {"schema_version": 1, "status": "authorized"}

    monkeypatch.setattr("dayboard.ui.settings.authorize_calendar", authorize)
    app = DayboardApp(paths=paths)
    async with app.run_test() as pilot:
        screen = await open_settings(app, pilot)
        assert calls == []
        screen.query_one("#calendar-authorize", Button).press()
        await pilot.pause()
        assert calls == ["authorize"]
        assert not store.load().calendar.enabled
        assert screen.query_one("#calendar-enabled", Switch).value
        assert "Save" in str(screen.query_one("#calendar-status", Static).render())
        await pilot.press("escape")
        assert not store.load().calendar.enabled
        await pilot.press("q")


async def test_cancel_cancels_owned_authorization_worker(environment, monkeypatch):
    paths, _ = environment
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def authorize(paths):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr("dayboard.ui.settings.authorize_calendar", authorize)
    app = DayboardApp(paths=paths)
    async with app.run_test() as pilot:
        screen = await open_settings(app, pilot)
        screen.query_one("#calendar-authorize", Button).press()
        await asyncio.wait_for(started.wait(), 1)
        await pilot.press("escape")
        await asyncio.wait_for(cancelled.wait(), 1)
        assert not isinstance(app.screen, SettingsScreen)
        await pilot.press("q")


async def test_duplicate_save_and_cancel_during_save_do_not_race(environment, monkeypatch):
    paths, _ = environment
    import dayboard.ui.settings as module

    original = module.save_draft
    started, release = threading.Event(), threading.Event()
    calls = []

    def blocked(*args):
        calls.append("save")
        started.set()
        assert release.wait(3)
        return original(*args)

    monkeypatch.setattr(module, "save_draft", blocked)
    app = DayboardApp(paths=paths)
    async with app.run_test() as pilot:
        screen = await open_settings(app, pilot)
        try:
            await pilot.press("ctrl+s")
            assert await asyncio.to_thread(started.wait, 1)
            await pilot.press("ctrl+s", "escape")
            assert app.screen is screen
            assert screen.query_one("#settings-cancel", Button).disabled
            assert calls == ["save"]
        finally:
            release.set()
        await pilot.pause()
        assert not isinstance(app.screen, SettingsScreen)
        await pilot.press("q")


async def test_first_run_configure_cancel_and_save_navigation(environment):
    paths, store = environment
    store.paths.config_file.unlink()
    app = DayboardApp(paths=paths)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, WelcomeScreen)
        assert await pilot.click("#configure")
        await pilot.pause()
        assert isinstance(app.screen, SettingsScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, WelcomeScreen)
        assert store.load() is None
        assert await pilot.click("#configure")
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert not isinstance(app.screen, (SettingsScreen, WelcomeScreen))
        assert store.load().onboarding_complete
        await pilot.press("q")


async def test_demo_settings_never_accesses_state_network_or_permissions(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Demo settings must not access real state.")

    async def async_forbidden(*args, **kwargs):
        raise AssertionError("Demo settings must not query or authorize.")

    monkeypatch.setattr(ConfigStore, "load", forbidden)
    monkeypatch.setattr(ConfigStore, "save", forbidden)
    monkeypatch.setattr("dayboard.ui.settings.calendar_status", async_forbidden)
    monkeypatch.setattr("dayboard.ui.settings.authorize_calendar", async_forbidden)
    monkeypatch.setattr("dayboard.ui.settings.Geocoder.search", async_forbidden)
    paths = AppPaths(tmp_path / "data", tmp_path / "cache")
    app = DayboardApp(demo=True, paths=paths)
    async with app.run_test() as pilot:
        screen = await open_settings(app, pilot)
        assert screen.query_one("#settings-save", Button).disabled
        assert screen.query_one("#location-search", Button).disabled
        assert screen.query_one("#calendar-authorize", Button).disabled
        await pilot.press("ctrl+s", "escape")
        assert not isinstance(app.screen, SettingsScreen)
        assert not paths.data_dir.exists()
        await pilot.press("q")


async def test_superseded_search_cannot_replace_newer_location_choices(environment, monkeypatch):
    paths, _ = environment
    started = asyncio.Event()

    async def search(self, query):
        if query == "Old":
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return (LocationChoice("Old result", 1, 2, "UTC"),)
        return (LocationChoice("New result", 3, 4, "UTC"),)

    monkeypatch.setattr("dayboard.ui.settings.Geocoder.search", search)
    app = DayboardApp(paths=paths)
    async with app.run_test() as pilot:
        screen = await open_settings(app, pilot)
        screen.query_one("#location-query", Input).value = "Old"
        screen.query_one("#location-search", Button).press()
        await asyncio.wait_for(started.wait(), 1)
        screen.query_one("#location-query", Input).value = "New"
        screen.query_one("#location-search", Button).press()
        await pilot.pause()
        screen.query_one("#location-choice", Select).value = 0
        await pilot.pause()
        assert screen.query_one("#weather-name", Input).value == "New result"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert app.config.weather.label == "New result"
        assert app.config.weather.enabled
        await pilot.press("q")


async def test_cancel_cancels_search_and_does_not_save_location(environment, monkeypatch):
    paths, store = environment
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def search(self, query):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr("dayboard.ui.settings.Geocoder.search", search)
    app = DayboardApp(paths=paths)
    async with app.run_test() as pilot:
        screen = await open_settings(app, pilot)
        screen.query_one("#location-query", Input).value = "Example"
        screen.query_one("#location-search", Button).press()
        await asyncio.wait_for(started.wait(), 1)
        await pilot.press("escape")
        await asyncio.wait_for(cancelled.wait(), 1)
        assert not store.load().weather.enabled
        await pilot.press("q")


@pytest.mark.parametrize("operation", ["search", "authorize", "save"])
async def test_unexpected_service_errors_are_safe_and_do_not_lock_ui(
    environment,
    monkeypatch,
    operation,
):
    paths, _ = environment

    async def broken(*args):
        raise RuntimeError("SYNTHETIC_PRIVATE_VALUE")

    def broken_save(*args):
        raise RuntimeError("SYNTHETIC_PRIVATE_VALUE")

    monkeypatch.setattr("dayboard.ui.settings.Geocoder.search", broken)
    monkeypatch.setattr("dayboard.ui.settings.authorize_calendar", broken)
    monkeypatch.setattr("dayboard.ui.settings.save_draft", broken_save)
    app = DayboardApp(paths=paths)
    async with app.run_test() as pilot:
        screen = await open_settings(app, pilot)
        if operation == "search":
            screen.query_one("#location-query", Input).value = "Example"
            screen.query_one("#location-search", Button).press()
            target = "#location-status"
        elif operation == "authorize":
            screen.query_one("#calendar-authorize", Button).press()
            target = "#calendar-status"
        else:
            await pilot.press("ctrl+s")
            target = "#settings-message"
        await pilot.pause()
        message = str(screen.query_one(target, Static).render())
        assert "SYNTHETIC_PRIVATE_VALUE" not in message
        assert not any(word in message.lower() for word in ("searching…", "building", "saving and"))
        assert not screen.query_one("#settings-cancel", Button).disabled
        await pilot.press("escape")
        await pilot.press("q")


async def test_denied_calendar_permission_does_not_enable_or_save(environment, monkeypatch):
    paths, store = environment

    async def denied(paths):
        return {"schema_version": 1, "status": "denied"}

    monkeypatch.setattr("dayboard.ui.settings.authorize_calendar", denied)
    app = DayboardApp(paths=paths)
    async with app.run_test() as pilot:
        screen = await open_settings(app, pilot)
        screen.query_one("#calendar-authorize", Button).press()
        await pilot.pause()
        assert not screen.query_one("#calendar-enabled", Switch).value
        assert not store.load().calendar.enabled
        await pilot.press("escape")
        await pilot.press("q")


async def test_runtime_reconfiguration_failure_stops_old_sources_without_false_success(
    environment,
    monkeypatch,
):
    paths, _ = environment
    app = DayboardApp(paths=paths)
    notices = []

    def broken(*args):
        raise RuntimeError("SYNTHETIC_PRIVATE_VALUE")

    async with app.run_test() as pilot:
        old_scheduler = app.scheduler
        await open_settings(app, pilot)
        monkeypatch.setattr("dayboard.app.build_live_registry", broken)
        monkeypatch.setattr(app, "notify", lambda message, **kwargs: notices.append(message))
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert not isinstance(app.screen, SettingsScreen)
        assert old_scheduler.running_count == 0
        assert not app.registry.all()
        assert app.scheduler.running_count == 0
        assert any("could not start" in message for message in notices)
        assert not any(message.startswith("Settings applied") for message in notices)
        assert "SYNTHETIC_PRIVATE_VALUE" not in str(notices)
        await pilot.press("q")


async def test_keyboard_can_reach_authorize_and_save_without_mouse(environment, monkeypatch):
    paths, store = environment
    calls = []

    async def authorize(paths):
        calls.append("authorize")
        return {"schema_version": 1, "status": "authorized"}

    monkeypatch.setattr("dayboard.ui.settings.authorize_calendar", authorize)
    app = DayboardApp(paths=paths)
    async with app.run_test(size=(80, 24)) as pilot:
        await open_settings(app, pilot)
        for _ in range(30):
            if app.focused is not None and app.focused.id == "calendar-authorize":
                break
            await pilot.press("tab")
        assert app.focused is not None and app.focused.id == "calendar-authorize"
        assert calls == []
        await pilot.press("enter")
        await pilot.pause()
        assert calls == ["authorize"]
        assert not store.load().calendar.enabled
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert not isinstance(app.screen, SettingsScreen)
        assert store.load().calendar.enabled
        await pilot.press("q")
