"""Run inside an isolated environment containing only a built Dayboard package."""

import argparse
import asyncio
from importlib.metadata import version
from importlib.resources import files

from textual.widgets import Button, Static, TabbedContent

from dayboard.app import DayboardApp
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
        for key, pane in (("2", "schedule"), ("3", "mac"), ("4", "github")):
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
        await pilot.press("escape")
        await pilot.pause()
        if isinstance(app.screen, SettingsScreen):
            raise RuntimeError("Cancel did not return to the installed overview.")
        if app.query_one(TabbedContent).active != "github":
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
