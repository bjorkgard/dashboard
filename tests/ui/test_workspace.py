from datetime import UTC, datetime, timedelta

import pytest
from textual.containers import Grid, VerticalScroll
from textual.widgets import Input, Static, Switch, TabbedContent, TabPane, Tabs

from dayboard.app import DayboardApp
from dayboard.config import Config, ConfigStore
from dayboard.core.models import ErrorCode, Metric, ProviderKey, Snapshot
from dayboard.core.providers import Capability, ProviderRegistry, ProviderSpec
from dayboard.core.store import SnapshotStore
from dayboard.paths import AppPaths
from dayboard.settings import CalendarSettings
from dayboard.ui.card import SnapshotCard
from dayboard.ui.details import DetailCard
from dayboard.ui.settings import SettingsScreen


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    paths = AppPaths(tmp_path / "data", tmp_path / "cache")
    ConfigStore(paths).save(Config(onboarding_complete=True, calendar=CalendarSettings(True)))
    registry = ProviderRegistry()
    calls = []

    class Calendar:
        spec = ProviderSpec(
            ProviderKey("apple-calendar"), "Calendar", (Capability.CALENDAR,), interval_seconds=3600
        )

        async def collect(self):
            calls.append("collect")
            now = datetime.now(UTC)
            return Snapshot(
                self.spec.key,
                now,
                now,
                (Metric("window", "Next 7 days", "12 occurrences"),)
                + tuple(
                    Metric(f"event-{i}", f"Day {i}", f"[bold]Meeting {i}\x1b[31m · Work")
                    for i in range(12)
                ),
            )

    registry.register(Calendar())
    monkeypatch.setattr(
        "dayboard.app.build_live_registry",
        lambda config, paths: registry if config.calendar.enabled else ProviderRegistry(),
    )

    async def status(paths):
        return {"schema_version": 1, "status": "not_built"}

    monkeypatch.setattr("dayboard.ui.settings.calendar_status", status)
    return paths, calls


@pytest.mark.parametrize("size", [(80, 24), (120, 36), (160, 48)])
async def test_tabs_switch_without_restarting_collectors_and_remain_visible(workspace, size):
    paths, calls = workspace
    app = DayboardApp(paths=paths)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        tabs = app.query_one("#workspace-tabs", TabbedContent)
        scheduler, store, registry = app.scheduler, app.store, app.registry
        for key, pane in (("2", "schedule"), ("3", "mac"), ("4", "github"), ("1", "overview")):
            await pilot.press(key)
            await pilot.pause()
            assert tabs.active == pane
            region = app.query_one(f"#{pane}", TabPane).region
            assert region.width > 0 and region.bottom <= size[1] - 1
        assert app.scheduler is scheduler and app.store is store and app.registry is registry
        assert calls == ["collect"]
        assert app.query_one("#settings-open").region.bottom <= size[1]
        tab_bar = app.query_one(Tabs)
        tab_bar.focus()
        await pilot.press("right")
        await pilot.pause()
        assert tabs.active == "schedule"
        assert await pilot.click("#--content-tab-github")
        assert tabs.active == "github"
        await pilot.press("q")
    assert app.scheduler.running_count == 0


async def test_github_detail_groups_all_bounded_records_but_overview_keeps_five_each():
    now = datetime.now(UTC)
    snapshot = Snapshot(
        ProviderKey("github"),
        now,
        now,
        (
            Metric("scope", "Preview scope", "OVERVIEW_ONLY"),
            Metric("detail-scope", "Bounded detail", "50 of 200 open PRs"),
            Metric("unavailable", "Repositories unavailable", "1 access issue"),
        )
        + tuple(Metric(f"pr-{i}", f"PR {i}", f"[bold]TITLE_{i}\x1b[31m") for i in range(50))
        + tuple(Metric(f"commit-{i}", f"Commit {i}", f"COMMIT_{i}") for i in range(25)),
    )
    app = DayboardApp(demo=True)
    async with app.run_test() as pilot:
        overview = app.query_one("#card-github", SnapshotCard)
        detail = app.query_one("#card-github-detail", DetailCard)
        overview.show_snapshot(snapshot)
        detail.show_snapshot(snapshot)
        compact, expanded = str(overview.render()), str(detail.render())
        assert "PR 4" in compact and "PR 5" not in compact
        assert "Commit 4" in compact and "Commit 5" not in compact
        assert "OVERVIEW_ONLY" in compact and "OVERVIEW_ONLY" not in expanded
        assert "50 of 200" in expanded and "1 access issue" in expanded
        assert "Pull requests" in expanded and "Recent default-branch commits" in expanded
        assert "[bold]TITLE_49" in expanded and "COMMIT_24" in expanded and "\x1b" not in expanded
        await pilot.press("q")


async def test_mac_detail_uses_same_history_and_reset_clears_both_views(tmp_path, monkeypatch):
    paths = AppPaths(tmp_path / "data", tmp_path / "cache")
    ConfigStore(paths).save(Config(onboarding_complete=True))
    registry = ProviderRegistry()

    class Mac:
        spec = ProviderSpec(
            ProviderKey("mac-telemetry"), "Mac", (Capability.SYSTEM,), interval_seconds=3600
        )

        async def collect(self):
            now = datetime.now(UTC)
            return Snapshot(self.spec.key, now, now, (Metric("cpu_percent", "CPU", 25, "%"),))

    registry.register(Mac())
    monkeypatch.setattr("dayboard.app.build_live_registry", lambda config, paths: registry)
    app = DayboardApp(paths=paths)
    async with app.run_test() as pilot:
        await pilot.pause()
        before = app.store.history(ProviderKey("mac-telemetry"))
        await pilot.press("3")
        app._refresh_view()
        assert app.store.history(ProviderKey("mac-telemetry")) == before
        for identifier in ("mac", "mac-detail"):
            rendered = str(app.query_one(f"#card-{identifier}", Static).render())
            assert "CPU  25.0%" in rendered and "▂" in rendered
        assert "CPU" in str(app.query_one("#card-mac-metrics", Static).render())
        app.registry = ProviderRegistry()
        app.store = SnapshotStore()
        app._refresh_view()
        for identifier in ("mac", "mac-detail", "mac-metrics"):
            assert "Not connected" in str(app.query_one(f"#card-{identifier}", Static).render())
        await pilot.press("q")


async def test_live_resize_adjusts_overview_columns_without_losing_selected_tab(workspace):
    paths, _ = workspace
    app = DayboardApp(paths=paths)
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.press("4")
        await pilot.resize_terminal(80, 24)
        await pilot.pause()
        assert app.query_one(TabbedContent).active == "github"
        assert app.query_one("#cards", Grid).styles.grid_size_columns == 1
        await pilot.press("1")
        assert app.query_one("#card-mac").region.y < app.query_one("#card-schedule").region.y
        await pilot.resize_terminal(160, 48)
        await pilot.pause()
        assert app.query_one("#cards", Grid).styles.grid_size_columns == 2
        assert app.query_one("#card-mac").region.y == app.query_one("#card-schedule").region.y
        await pilot.press("q")


async def test_onboarding_numbers_cannot_navigate_underneath_welcome(tmp_path):
    app = DayboardApp(paths=AppPaths(tmp_path / "data", tmp_path / "cache"))
    async with app.run_test() as pilot:
        base = app.screen_stack[0]
        await pilot.press("2", "3", "4")
        assert base.query_one(TabbedContent).active == "overview"
        await pilot.press("q")
    assert app.scheduler.running_count == 0


async def test_schedule_detail_expands_preview_and_preserves_safe_stale_data(workspace):
    paths, _ = workspace
    app = DayboardApp(paths=paths)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        overview = str(app.query_one("#card-schedule", Static).render())
        assert "Meeting 5" in overview and "Meeting 6" not in overview
        await pilot.press("2")
        detail = str(app.query_one("#card-schedule-detail", Static).render())
        assert "[bold]Meeting 11" in detail and "\x1b" not in detail
        assert "local machine" in detail
        scroll = app.query_one("#schedule-scroll", VerticalScroll)
        scroll.focus()
        await pilot.press("pagedown")
        await pilot.pause()
        assert scroll.scroll_y > 0
        key = ProviderKey("apple-calendar")
        app.store.fail(key, ErrorCode.TIMEOUT)
        app._refresh_view()
        detail = str(app.query_one("#card-schedule-detail", Static).render())
        assert "STALE" in detail and "Meeting 11" in detail and "timed out" in detail
        assert detail.index("STALE") < detail.index("Meeting 0")
        assert detail.index("timed out") < detail.index("Meeting 0")
        await pilot.press("q")


async def test_settings_preserves_tab_and_does_not_intercept_digits_then_clears_old_scope(
    workspace,
):
    paths, _ = workspace
    app = DayboardApp(paths=paths)
    async with app.run_test() as pilot:
        await pilot.press("2", "s")
        await pilot.pause()
        assert isinstance(app.screen, SettingsScreen)
        field = app.screen.query_one("#github-repos", Input)
        field.focus()
        field.value = ""
        await pilot.press("1", "2", "3", "4")
        assert field.value == "1234"
        base = app.screen_stack[0]
        assert base.query_one(TabbedContent).active == "schedule"
        await pilot.press("escape")
        assert app.query_one(TabbedContent).active == "schedule"
        await pilot.press("s")
        app.screen.query_one("#calendar-enabled", Switch).value = False
        old_scheduler = app.scheduler
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert app.query_one(TabbedContent).active == "schedule"
        assert app.scheduler is not old_scheduler and old_scheduler.running_count == 0
        for identifier in ("schedule", "schedule-detail"):
            rendered = str(app.query_one(f"#card-{identifier}", Static).render())
            assert "Meeting" not in rendered and "Not connected" in rendered
        await pilot.press("q")


async def test_demo_details_are_isolated_and_explicitly_synthetic(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Demo cannot load real state or construct live collectors")

    monkeypatch.setattr(ConfigStore, "load", forbidden)
    monkeypatch.setattr("dayboard.app.build_live_registry", forbidden)
    paths = AppPaths(tmp_path / "data", tmp_path / "cache")
    app = DayboardApp(demo=True, paths=paths)
    async with app.run_test() as pilot:
        for key, identifier in (
            ("2", "schedule-detail"),
            ("3", "mac-detail"),
            ("4", "github-detail"),
        ):
            await pilot.press(key)
            assert "SYNTHETIC" in str(app.query_one(f"#card-{identifier}", Static).render())
        await pilot.press("q")
    assert not paths.data_dir.exists() and not paths.cache_dir.exists()


async def test_details_show_loading_errors_and_age_staleness_without_invented_data(workspace):
    paths, _ = workspace
    app = DayboardApp(paths=paths)
    async with app.run_test() as pilot:
        await pilot.pause()
        provider = app.registry.all()[0]
        await app.scheduler.close()
        app.store = SnapshotStore()
        app._refresh_view()
        assert "Loading" in str(app.query_one("#card-schedule-detail", Static).render())
        app.store.fail(provider.spec.key, ErrorCode.PERMISSION_DENIED)
        app._refresh_view()
        rendered = str(app.query_one("#card-schedule-detail", Static).render())
        assert "denied" in rendered and "Meeting" not in rendered
        old = datetime.now(UTC) - timedelta(hours=3)
        app.store.publish(
            Snapshot(provider.spec.key, old, old, (Metric("old", "Past", "Retained"),))
        )
        app._refresh_view()
        assert "STALE" in str(app.query_one("#card-schedule-detail", Static).render())
        await pilot.press("q")
