import asyncio
import threading
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from textual.widgets import Button, Input, SelectionList, Static, Switch

from dayboard.app import DayboardApp
from dayboard.config import Config, ConfigStore
from dayboard.core.models import Metric, ProviderKey, Snapshot
from dayboard.core.providers import Capability, ProviderRegistry, ProviderSpec
from dayboard.paths import AppPaths
from dayboard.security.keychain import Keychain
from dayboard.settings import MailboxSelection, MailSettings, WarpSettings
from dayboard.ui.settings import SettingsScreen
from dayboard.ui.warp_confirm import ConfirmWarpDisconnect


class Backend:
    def __init__(self):
        self.values = {}

    def get_password(self, service, username):
        return self.values.get((service, username))

    def set_password(self, service, username, password):
        self.values[service, username] = password

    def delete_password(self, service, username):
        self.values.pop((service, username), None)


@pytest.fixture
def environment(tmp_path, monkeypatch):
    paths = AppPaths(tmp_path / "data", tmp_path / "cache")
    store = ConfigStore(paths)
    store.save(Config(onboarding_complete=True))
    calls = []
    backend = Backend()
    keys = Keychain(backend=backend)
    monkeypatch.setattr("dayboard.security.warp_credentials.Keychain", lambda: keys)

    class Provider:
        spec = ProviderSpec(ProviderKey("mac-telemetry"), "Test local", (Capability.SYSTEM,))

        async def collect(self):
            now = datetime.now(UTC)
            return Snapshot(self.spec.key, now, now, (Metric("cpu_percent", "CPU", 1),))

    def registry(config, paths):
        result = ProviderRegistry()
        result.register(Provider())
        return result

    monkeypatch.setattr("dayboard.app.build_live_registry", registry)

    async def calendar(paths):
        return {"schema_version": 1, "status": "not_built"}

    async def authorize(paths):
        calls.append("authorize")
        return {"schema_version": 1, "status": "authorized"}

    async def discover(paths):
        calls.append("discover")
        return (MailboxSelection("account", ("Inbox",), "Example"),)

    monkeypatch.setattr("dayboard.ui.settings.calendar_status", calendar)
    monkeypatch.setattr("dayboard.ui.settings.authorize_mail", authorize)
    monkeypatch.setattr("dayboard.ui.settings.discover_mailboxes", discover)
    return paths, store, calls, keys


async def test_mail_opening_does_not_authorize_and_explicit_selection_requires_save(environment):
    paths, store, calls, _ = environment
    before = store.load()
    app = DayboardApp(paths=paths)
    async with app.run_test() as pilot:
        await pilot.press("6", "s")
        screen = app.screen
        assert isinstance(screen, SettingsScreen) and not calls
        screen._start_mail("authorize")
        await pilot.pause()
        screen._start_mail("discover")
        await pilot.pause()
        assert calls == ["authorize", "discover"]
        assert store.load() == before
        screen.query_one("#mail-boxes", SelectionList).select(0)
        screen.query_one("#mail-enabled", Switch).value = True
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert store.load().mail.enabled
        assert store.load().mail.mailboxes == (MailboxSelection("account", ("Inbox",), "Example"),)
        await pilot.press("q")


async def test_warp_test_is_explicit_masked_and_does_not_save(environment, monkeypatch):
    paths, store, calls, keys = environment

    class Warp:
        def __init__(self, settings, credential=None):
            self.credential = credential

        async def collect(self):
            assert self.credential() == "FAKE_SECRET_CANARY"
            calls.append("warp-test")

    monkeypatch.setattr("dayboard.ui.settings.WarpProvider", Warp)
    app = DayboardApp(paths=paths)
    async with app.run_test() as pilot:
        await pilot.press("s")
        screen = app.screen
        field = screen.query_one("#warp-key", Input)
        assert field.password
        field.value = "FAKE_SECRET_CANARY"
        assert "FAKE_SECRET_CANARY" not in app.export_screenshot()
        screen._start_warp_test()
        await pilot.pause()
        assert calls == ["warp-test"]
        assert "succeeded" in str(screen.query_one("#warp-status", Static).render())
        assert not store.load().warp.enabled
        assert keys.get("warp", "default") is None
        screen.query_one("#warp-enabled", Switch).value = True
        await pilot.press("ctrl+s")
        await pilot.pause()
        saved = store.load()
        assert saved.warp.enabled and saved.warp.credential_id != "default"
        assert keys.get("warp", saved.warp.credential_id) == "FAKE_SECRET_CANARY"
        assert "FAKE_SECRET_CANARY" not in paths.config_file.read_text()
        await pilot.press("q")


@pytest.mark.parametrize("size", [(80, 24), (120, 36), (160, 48)])
async def test_warp_disconnect_requires_confirmation_and_cleans_only_dayboard_keys(
    environment, size
):
    paths, store, calls, keys = environment
    initial = replace(store.load(), warp=WarpSettings(True, "saved-id"))
    store.save(initial)
    keys.set("warp", "saved-id", "FAKE_SECRET_CANARY")
    app = DayboardApp(paths=paths)
    async with app.run_test(size=size) as pilot:
        await pilot.press("s")
        screen = app.screen
        screen._start_warp_disconnect()
        await pilot.pause()
        assert isinstance(app.screen, ConfirmWarpDisconnect)
        assert app.screen.query_one("#warp-confirm-no", Button).has_focus
        await pilot.press("escape")
        assert app.screen is screen and store.load() == initial
        screen._start_warp_disconnect()
        await pilot.pause()
        await pilot.click("#warp-confirm-yes")
        await pilot.pause()
        assert not isinstance(app.screen, SettingsScreen)
        assert not store.load().warp.enabled
        assert keys.get("warp", "saved-id") is None
        await pilot.press("q")


async def test_discovery_preserves_selection_edits_made_while_request_is_in_flight(
    environment, monkeypatch
):
    paths, store, _, _ = environment
    box = MailboxSelection("account", ("Inbox",), "Example")
    store.save(replace(store.load(), mail=MailSettings(False, (box,))))
    entered, release = asyncio.Event(), asyncio.Event()

    async def gated(paths):
        entered.set()
        await release.wait()
        return (box,)

    monkeypatch.setattr("dayboard.ui.settings.discover_mailboxes", gated)
    app = DayboardApp(paths=paths)
    async with app.run_test() as pilot:
        await pilot.press("s")
        screen = app.screen
        screen._start_mail("discover")
        await entered.wait()
        screen.query_one("#mail-boxes", SelectionList).deselect(0)
        release.set()
        await pilot.pause()
        assert screen.query_one("#mail-boxes", SelectionList).selected == []
        await pilot.press("escape", "q")


async def test_warp_connection_test_does_not_certify_a_changed_draft(environment, monkeypatch):
    paths, _, _, _ = environment
    entered, release = asyncio.Event(), asyncio.Event()

    class Warp:
        def __init__(self, settings, credential=None):
            pass

        async def collect(self):
            entered.set()
            await release.wait()

    monkeypatch.setattr("dayboard.ui.settings.WarpProvider", Warp)
    app = DayboardApp(paths=paths)
    async with app.run_test() as pilot:
        await pilot.press("s")
        screen = app.screen
        screen.query_one("#warp-key", Input).value = "FIRST_FAKE"
        screen._start_warp_test()
        await entered.wait()
        screen.query_one("#warp-key", Input).value = "SECOND_FAKE"
        release.set()
        await pilot.pause()
        assert "discarded" in str(screen.query_one("#warp-status", Static).render())
        await pilot.press("escape", "q")


async def test_warp_poller_stops_before_slow_keychain_cleanup(environment, monkeypatch):
    import dayboard.app as app_module
    import dayboard.ui.settings as settings_module

    paths, store, _, keys = environment
    store.save(replace(store.load(), warp=WarpSettings(True, "saved-id")))
    keys.set("warp", "saved-id", "FAKE_SECRET_CANARY")
    original_factory = app_module.build_live_registry
    original_disconnect = settings_module.disconnect_warp
    entered, release = threading.Event(), threading.Event()

    class Warp:
        spec = ProviderSpec(ProviderKey("warp-runs"), "Fake Warp", (Capability.AI,))

        async def collect(self):
            now = datetime.now(UTC)
            return Snapshot(self.spec.key, now, now, (Metric("scope", "Scope", "Preview only"),))

    def registry(config, paths):
        result = original_factory(config, paths)
        if config.warp.enabled:
            result.register(Warp())
        return result

    def gated_disconnect(store, config):
        entered.set()
        release.wait(3)
        return original_disconnect(store, config)

    monkeypatch.setattr(app_module, "build_live_registry", registry)
    monkeypatch.setattr(settings_module, "disconnect_warp", gated_disconnect)
    app = DayboardApp(paths=paths)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.scheduler.running_count == 2
        await pilot.press("s")
        app.screen._start_warp_disconnect()
        await pilot.pause()
        await pilot.click("#warp-confirm-yes")
        try:
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(0.01)
            assert entered.is_set()
            assert not store.load().warp.enabled
            assert app.scheduler.running_count == 1
        finally:
            release.set()
        await pilot.pause()
        await pilot.press("q")
