"""Run inside an isolated environment containing only a built Dayboard package."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory

from textual.widgets import Button, Static, TabbedContent

from dayboard.app import DayboardApp
from dayboard.integrations.ai.provider import ClaudeProvider
from dayboard.settings import ClaudeSettings
from dayboard.ui.settings import SettingsScreen


async def smoke(expected_version: str) -> None:
    actual = version("dayboard")
    if actual != expected_version:
        raise RuntimeError("Installed distribution has the wrong version.")
    if not files("dayboard").joinpath("ui/styles/shell.tcss").is_file():
        raise RuntimeError("Installed distribution is missing its stylesheet.")
    for name in ("CalendarBridge.swift", "Info.plist"):
        if not files("dayboard").joinpath("native", name).is_file():
            raise RuntimeError("Installed distribution is missing native calendar sources.")
    with TemporaryDirectory(prefix="dayboard-claude-smoke-") as directory:
        # Resolve only our temporary fixture; source traversal itself never follows links.
        root = await asyncio.to_thread(Path(directory).resolve)
        projects = root / "projects"
        projects.mkdir()
        source = projects / "synthetic.jsonl"
        source.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "sessionId": "synthetic-session",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "version": "2.1.96",
                    "message": {
                        "id": "synthetic-message",
                        "model": "claude-example",
                        "content": [{"text": "PRIVATE_SMOKE_CANARY"}],
                        "usage": {"input_tokens": 101, "output_tokens": 12},
                    },
                }
            )
            + "\n"
        )
        before = source.read_bytes(), source.stat().st_mode, source.stat().st_mtime_ns
        provider = ClaudeProvider(ClaudeSettings(True, str(root)))
        snapshot = await provider.collect()
        if next(metric.value for metric in snapshot.metrics if metric.id == "today-input") != 101:
            raise RuntimeError("Installed Claude reader did not collect synthetic usage.")
        if "CANARY" in repr(snapshot) + repr(provider.ledger.records):
            raise RuntimeError("Installed Claude reader retained unallowlisted content.")
        if (source.read_bytes(), source.stat().st_mode, source.stat().st_mtime_ns) != before:
            raise RuntimeError("Installed Claude reader modified its source.")
    app = DayboardApp(demo=True)
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        if actual not in str(app.query_one("#release", Static).render()):
            raise RuntimeError("Installed UI reports the wrong version.")
        if "DEMO" not in str(app.query_one("#mode", Static).render()):
            raise RuntimeError("Isolated smoke test did not start in demo mode.")
        if not app.store.history(app.registry.all()[0].spec.key):
            raise RuntimeError("Synthetic collector did not produce a reading.")
        scheduler = app.scheduler
        for key, pane in (("2", "schedule"), ("3", "mac"), ("4", "github"), ("5", "ai")):
            await pilot.press(key)
            if app.query_one(TabbedContent).active != pane:
                raise RuntimeError("Installed workspace navigation did not switch tabs.")
            if "SYNTHETIC" not in str(app.query_one(f"#card-{pane}-detail", Static).render()):
                raise RuntimeError("Installed detail view did not show synthetic data.")
        if app.scheduler is not scheduler:
            raise RuntimeError("Tab navigation must not replace collectors.")
        await pilot.press("s")
        await pilot.pause()
        if not isinstance(app.screen, SettingsScreen):
            raise RuntimeError("Installed Settings screen did not open.")
        if not app.screen.query_one("#settings-save", Button).disabled:
            raise RuntimeError("Demo Settings must not allow saving real preferences.")
        if not app.screen.query_one("#claude-source").disabled:
            raise RuntimeError("Demo Claude Settings must be read-only.")
        await pilot.press("escape")
        await pilot.pause()
        if isinstance(app.screen, SettingsScreen):
            raise RuntimeError("Cancel did not return to the installed overview.")
        if app.query_one(TabbedContent).active != "ai":
            raise RuntimeError("Settings Cancel did not preserve the selected workspace tab.")
        await pilot.press("q")
    if app.scheduler.running_count:
        raise RuntimeError("Collectors remained alive after exit.")
    print(f"Installed package smoke test passed: dayboard {actual}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, dest="expected_version")
    args = parser.parse_args()
    asyncio.run(smoke(args.expected_version))
