import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from textual.widgets import Input, Static, Switch, TabbedContent

from dayboard.app import DayboardApp
from dayboard.config import Config, ConfigError, ConfigStore
from dayboard.integrations.ai.reader import ClaudeReader
from dayboard.paths import AppPaths
from dayboard.settings import ClaudeSettings
from dayboard.settings_editor import SettingsDraft, save_draft
from dayboard.ui.settings import SettingsScreen


@pytest.fixture
def environment(tmp_path):
    paths = AppPaths(tmp_path / "data", tmp_path / "cache")
    store = ConfigStore(paths)
    store.save(Config(onboarding_complete=True))
    root = tmp_path / "claude"
    projects = root / "projects"
    projects.mkdir(parents=True)
    (projects / "session.jsonl").write_text(
        json.dumps(
            {
                "type": "assistant",
                "sessionId": "example-session",
                "timestamp": datetime.now(UTC).isoformat(),
                "version": "2.1.96",
                "message": {
                    "id": "msg-example",
                    "model": "claude-example",
                    "usage": {"input_tokens": 10, "output_tokens": 20},
                    "content": [{"text": "PRIVATE_CANARY"}],
                },
            }
        )
        + "\n"
    )
    return paths, store, root


@pytest.mark.parametrize("version", [0, 1, 2, 3])
def test_old_config_migrates_only_in_memory_with_claude_disabled(environment, version):
    paths, store, _ = environment
    before = json.dumps({"schema_version": version})
    paths.config_file.write_text(before)
    config = store.load()
    assert config.schema_version == 5 and not config.claude.enabled
    assert paths.config_file.read_text() == before


def test_claude_roundtrip_preservation_conflicts_and_failed_validation(environment):
    paths, store, root = environment
    initial = store.load()
    draft = SettingsDraft(claude_enabled=True, claude_source=str(root), claude_timezone="UTC")
    saved = save_draft(store, initial, draft)
    assert saved.claude == ClaudeSettings(True, str(root), "UTC")
    with pytest.raises(ConfigError, match="changed"):
        save_draft(store, initial, draft)
    disabled = save_draft(store, saved, SettingsDraft(claude_source="unfinished"))
    assert disabled.claude == replace(saved.claude, enabled=False)
    with pytest.raises(ConfigError):
        save_draft(
            store,
            disabled,
            SettingsDraft(claude_enabled=True, claude_source=str(root / "missing")),
        )
    assert store.load() == disabled


@pytest.mark.parametrize("size", [(80, 24), (120, 36), (160, 48)])
async def test_enable_claude_live_save_ai_navigation_and_disable(environment, size):
    paths, store, root = environment
    app = DayboardApp(paths=paths)
    async with app.run_test(size=size) as pilot:
        await pilot.press("5", "s")
        await pilot.pause()
        assert isinstance(app.screen, SettingsScreen)
        screen = app.screen
        screen.query_one("#claude-source", Input).value = str(root)
        screen.query_one("#claude-enabled", Switch).value = True
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert not isinstance(app.screen, SettingsScreen)
        assert app.query_one(TabbedContent).active == "ai"
        rendered = str(app.query_one("#card-ai-detail", Static).render())
        assert "20" in rendered and "partial account coverage" in rendered
        assert "PRIVATE_CANARY" not in rendered
        assert store.load().claude.enabled
        await pilot.press("s")
        app.screen.query_one("#claude-enabled", Switch).value = False
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert "Not connected" in str(app.query_one("#card-ai-detail", Static).render())
        assert app.query_one(TabbedContent).active == "ai"
        assert store.load().claude.source_root == str(root)
        await pilot.press("q")
    assert app.scheduler.running_count == 0


async def test_open_cancel_and_demo_never_scan_claude_sources(environment, monkeypatch):
    paths, store, root = environment

    def forbidden(*args, **kwargs):
        raise AssertionError("No Claude source access expected")

    monkeypatch.setattr(ClaudeReader, "scan", forbidden)
    store.save(replace(store.load(), claude=ClaudeSettings(False, str(root))))
    app = DayboardApp(paths=paths)
    async with app.run_test() as pilot:
        await pilot.press("5", "s")
        field = app.screen.query_one("#claude-source", Input)
        field.focus()
        field.value = ""
        await pilot.press("5")
        assert field.value == "5"
        await pilot.press("escape")
        assert app.query_one(TabbedContent).active == "ai"
        await pilot.press("q")
    app = DayboardApp(demo=True)
    async with app.run_test() as pilot:
        await pilot.press("5")
        assert "SYNTHETIC" in str(app.query_one("#card-ai-detail", Static).render())
        await pilot.press("s", "escape", "q")


async def test_source_timezone_change_replaces_old_scope_and_failed_save_keeps_it(environment):
    paths, store, root = environment
    store.save(replace(store.load(), claude=ClaudeSettings(True, str(root))))
    other = root.with_name("other-claude")
    (other / "projects").mkdir(parents=True)
    app = DayboardApp(paths=paths)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("5", "s")
        previous = app.scheduler
        screen = app.screen
        screen.query_one("#claude-source", Input).value = str(other / "missing")
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, SettingsScreen) and app.scheduler is previous
        assert "Claude" in str(screen.query_one("#settings-message", Static).render())
        screen.query_one("#claude-source", Input).value = str(other)
        screen.query_one("#claude-timezone", Input).value = "Europe/Stockholm"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert app.scheduler is not previous and previous.running_count == 0
        assert app.query_one(TabbedContent).active == "ai"
        rendered = str(app.query_one("#card-ai-detail", Static).render())
        assert "claude-example" not in rendered
        assert "Europe/Stockholm" in rendered
        assert store.load().claude.source_root == str(other)
        await pilot.press("q")
