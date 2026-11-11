"""Run inside an isolated environment containing only a built Dayboard package."""

import argparse
import asyncio
from importlib.metadata import version
from importlib.resources import files

from textual.widgets import Static

from dayboard.app import DayboardApp


async def smoke(expected_version: str) -> None:
    actual = version("dayboard")
    if actual != expected_version:
        raise RuntimeError("Installed distribution has the wrong version.")
    if not files("dayboard").joinpath("ui/styles/shell.tcss").is_file():
        raise RuntimeError("Installed distribution is missing its stylesheet.")
    app = DayboardApp(demo=True)
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        if actual not in str(app.query_one("#release", Static).render()):
            raise RuntimeError("Installed UI reports the wrong version.")
        if "DEMO" not in str(app.query_one("#mode", Static).render()):
            raise RuntimeError("Isolated smoke test did not start in demo mode.")
        if not app.store.history(app.registry.all()[0].spec.key):
            raise RuntimeError("Synthetic collector did not produce a reading.")
        await pilot.press("q")
    if app.scheduler.running_count:
        raise RuntimeError("Collectors remained alive after exit.")
    print(f"Installed package smoke test passed: dayboard {actual}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, dest="expected_version")
    args = parser.parse_args()
    asyncio.run(smoke(args.expected_version))
