"""Bounded native bridge calls. Compile/authorize only from explicit setup commands."""

import json
import os
import platform
import secrets
import stat
import sys
from importlib.resources import files
from pathlib import Path
from typing import Any

from dayboard.core.models import ErrorCode
from dayboard.core.processes import run_bounded as run_bounded
from dayboard.core.providers import ProviderUnavailable
from dayboard.paths import AppPaths
from dayboard.settings import CalendarSettings


def bridge_path(paths: AppPaths) -> Path:
    return paths.data_dir / "helpers" / "calendar-bridge"


def _check_executable(path: Path) -> None:
    try:
        # Check the containing application-owned directory as well as the executable leaf.
        for item in (path.parent, path):
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid():
                raise OSError
            if info.st_mode & 0o022:
                raise OSError
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not os.access(path, os.X_OK):
            raise OSError
    except OSError:
        raise ProviderUnavailable(ErrorCode.UNAVAILABLE) from None


async def build_bridge(paths: AppPaths) -> Path:
    if sys.platform != "darwin":
        raise ProviderUnavailable(ErrorCode.UNAVAILABLE)
    paths.ensure()
    target = bridge_path(paths)
    target.parent.mkdir(mode=0o700, exist_ok=True)
    fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if os.fstat(fd).st_uid != os.getuid():
            raise ProviderUnavailable(ErrorCode.UNAVAILABLE)
        os.fchmod(fd, 0o700)
    finally:
        os.close(fd)
    temporary = target.with_name(f".calendar-bridge-{secrets.token_hex(12)}")
    resource = files("dayboard").joinpath("native")
    try:
        await run_bounded(
            [
                "/usr/bin/xcrun",
                "swiftc",
                "-parse-as-library",
                "-target",
                f"{platform.machine()}-apple-macos14.0",
                str(resource.joinpath("CalendarBridge.swift")),
                "-o",
                str(temporary),
                "-Xlinker",
                "-sectcreate",
                "-Xlinker",
                "__TEXT",
                "-Xlinker",
                "__info_plist",
                "-Xlinker",
                str(resource.joinpath("Info.plist")),
            ],
            timeout=180,
        )
        temporary.chmod(0o700)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


class CalendarBridge:
    def __init__(self, executable: Path, *, calendar_ids: tuple[str, ...] = ()) -> None:
        self.executable = executable
        self.calendar_ids = CalendarSettings(True, calendar_ids).calendar_ids

    async def call(self, operation: str) -> dict[str, Any]:
        if operation not in ("status", "authorize", "events"):
            raise ValueError("Unsupported read-only bridge operation.")
        _check_executable(self.executable)
        command = [str(self.executable), operation]
        if operation == "events":
            command.extend(self.calendar_ids)
        output = await run_bounded(command, timeout=60 if operation == "authorize" else 10)
        try:
            result = json.loads(output)
            if not isinstance(result, dict) or type(result.get("schema_version")) is not int:
                raise ValueError
            if result["schema_version"] != 1:
                raise ValueError
            return result
        except (ValueError, TypeError, RecursionError):
            raise ValueError("Invalid native bridge protocol response.") from None
