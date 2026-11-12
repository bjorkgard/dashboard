from datetime import UTC, datetime

import pytest
from textual.widgets import Input, Static, TabbedContent

from dayboard.app import DayboardApp
from dayboard.config import Config, ConfigStore
from dayboard.core.models import ErrorCode, Metric, ProviderKey, Snapshot
from dayboard.core.providers import Capability, ProviderRegistry, ProviderSpec, ProviderUnavailable
from dayboard.paths import AppPaths
from dayboard.ui.settings import SettingsScreen


@pytest.fixture
def environment(tmp_path, monkeypatch):
    paths = AppPaths(tmp_path / "data", tmp_path / "cache")
    ConfigStore(paths).save(Config(onboarding_complete=True))
    registry = ProviderRegistry()

    class Provider:
        def __init__(self, key, capability, value):
            self.spec = ProviderSpec(ProviderKey(key), key, (capability,))
            self.value = value

        async def collect(self):
            if self.value == "error":
                raise ProviderUnavailable(ErrorCode.AI_UNSUPPORTED)
            now = datetime.now(UTC)
            return Snapshot(
                self.spec.key, now, now, (Metric("today-output", "Observed", self.value),)
            )

    for key, value in (
        ("claude-local", 11),
        ("codex-local", 22),
        ("kiro-local", "error"),
        ("warp-runs", 44),
    ):
        registry.register(Provider(key, Capability.AI, value))
    registry.register(Provider("apple-mail", Capability.MAIL, "SYNTHETIC_HEADER_PREVIEW"))
    monkeypatch.setattr("dayboard.app.build_live_registry", lambda config, paths: registry)
    return paths


@pytest.mark.parametrize("size", [(80, 24), (120, 36), (160, 48)])
async def test_all_ai_sources_render_independently_and_mail_is_reachable(environment, size):
    app = DayboardApp(paths=environment)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        await pilot.press("5")
        assert "11" in str(app.query_one("#card-ai-detail", Static).render())
        assert "22" in str(app.query_one("#card-ai-codex", Static).render())
        assert "unsupported" in str(app.query_one("#card-ai-kiro", Static).render())
        assert "44" in str(app.query_one("#card-ai-warp", Static).render())
        overview = str(app.query_one("#card-ai", Static).render())
        assert all(name in overview for name in ("Claude", "Codex", "Kiro", "Warp"))
        original = app.scheduler
        await pilot.press("6")
        assert app.query_one("#workspace-tabs", TabbedContent).active == "inbox"
        assert "SYNTHETIC_HEADER_PREVIEW" in str(
            app.query_one("#card-inbox-detail", Static).render()
        )
        await pilot.press("s")
        assert isinstance(app.screen, SettingsScreen)
        app.screen.query_one("#codex-source", Input).focus()
        app.screen.query_one("#codex-source", Input).value = ""
        await pilot.press("6")
        assert app.screen.query_one("#codex-source", Input).value == "6"
        await pilot.press("escape")
        assert app.query_one("#workspace-tabs", TabbedContent).active == "inbox"
        assert app.scheduler is original
        await pilot.press("q")


async def test_ai_detail_preserves_warp_overlap_and_account_scope_warning():
    app = DayboardApp(demo=True)
    async with app.run_test() as pilot:
        now = datetime.now(UTC)
        snapshot = Snapshot(
            ProviderKey("demo-warp"),
            now,
            now,
            (
                Metric(
                    "scope", "Scope", "Parent/child and resumed usage overlaps; not account totals"
                ),
                Metric("run-0-credits", "Run credits", "inference 1.25", "Warp credits"),
            ),
            synthetic=True,
        )
        card = app.query_one("#card-ai-warp", Static)
        card.show_snapshot(snapshot)
        rendered = str(card.render())
        assert "resumed usage overlaps" in rendered and "not account totals" in rendered
        assert rendered.index("resumed usage overlaps") < rendered.index("Run credits")
        await pilot.press("q")
    assert app.scheduler.running_count == 0


async def test_new_demo_sources_remain_isolated():
    app = DayboardApp(demo=True)
    async with app.run_test() as pilot:
        await pilot.press("5")
        for identifier in ("ai-detail", "ai-codex", "ai-codex-limits", "ai-kiro", "ai-warp"):
            assert "SYNTHETIC" in str(app.query_one(f"#card-{identifier}", Static).render())
        await pilot.press("6")
        assert "SYNTHETIC" in str(app.query_one("#card-inbox-detail", Static).render())
        await pilot.press("q")
