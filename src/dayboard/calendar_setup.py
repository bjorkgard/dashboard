"""Permission inspection never builds or prompts; authorization is a separate operation."""

from typing import Any

from dayboard.integrations.native import CalendarBridge, bridge_path, build_bridge
from dayboard.paths import AppPaths


def _status_only(result: dict[str, Any]) -> dict[str, Any]:
    status = result.get("status")
    if status not in ("authorized", "not_determined", "denied", "restricted"):
        status = "unavailable"
    return {"schema_version": 1, "status": status}


async def calendar_status(paths: AppPaths) -> dict[str, Any]:
    executable = bridge_path(paths)
    if not executable.exists():
        return {"schema_version": 1, "status": "not_built"}
    return _status_only(await CalendarBridge(executable).call("status"))


async def authorize_calendar(paths: AppPaths) -> dict[str, Any]:
    executable = await build_bridge(paths)
    return _status_only(await CalendarBridge(executable).call("authorize"))
