"""Owned process transport. Only explicit authorize builds or can prompt."""

import json
import os
import platform
import stat
import sys
import tempfile
from importlib.resources import files
from pathlib import Path
from typing import Any

from dayboard.core.models import ErrorCode
from dayboard.core.processes import run_bounded
from dayboard.core.providers import ProviderUnavailable
from dayboard.paths import AppPaths
from dayboard.settings import MailboxSelection


def bridge_path(paths: AppPaths) -> Path:
    return paths.data_dir / "helpers" / "mail-bridge"


def _check(path: Path, *, executable: bool = False) -> None:
    info = path.lstat()
    if info.st_uid != os.getuid() or stat.S_ISLNK(info.st_mode) or info.st_mode & 0o022:
        raise OSError
    if executable and (
        not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not os.access(path, os.X_OK)
    ):
        raise OSError
    if not executable and not stat.S_ISDIR(info.st_mode):
        raise OSError


async def compile_bridge(target: Path) -> None:
    """Compile only; never execute the resulting binary. Target must be app-owned/temp."""
    if sys.platform != "darwin":
        raise ProviderUnavailable(ErrorCode.MAIL_UNAVAILABLE)
    resource = files("dayboard").joinpath("native")
    try:
        await run_bounded(
            [
                "/usr/bin/xcrun",
                "swiftc",
                "-parse-as-library",
                "-target",
                f"{platform.machine()}-apple-macos14.0",
                str(resource.joinpath("MailBridge.swift")),
                "-o",
                str(target),
                "-Xlinker",
                "-sectcreate",
                "-Xlinker",
                "__TEXT",
                "-Xlinker",
                "__info_plist",
                "-Xlinker",
                str(resource.joinpath("MailInfo.plist")),
            ],
            timeout=180,
        )
    except (OSError, ValueError, ProviderUnavailable):
        raise ProviderUnavailable(ErrorCode.MAIL_SETUP_REQUIRED) from None


async def _build(paths: AppPaths) -> None:
    target = bridge_path(paths)
    try:
        paths.ensure()
        target.parent.mkdir(mode=0o700, exist_ok=True)
        _check(paths.data_dir)
        _check(target.parent)
        # The temporary directory is owned and private; no existing file is overwritten in place.
        with tempfile.TemporaryDirectory(prefix=".mail-build-", dir=target.parent) as directory:
            temporary = Path(directory) / "mail-bridge"
            await compile_bridge(temporary)
            _check(temporary, executable=True)
            os.replace(temporary, target)
    except OSError:
        raise ProviderUnavailable(ErrorCode.MAIL_SETUP_REQUIRED) from None


class MailBridge:
    def __init__(self, paths: AppPaths, mailboxes: tuple[MailboxSelection, ...] = ()) -> None:
        self.paths, self.mailboxes = paths, mailboxes

    async def call(self, operation: str) -> dict[str, Any]:
        if operation not in {"status", "authorize", "discover", "headers"}:
            raise ValueError("Unsupported read-only Mail operation.")
        if sys.platform != "darwin":
            raise ProviderUnavailable(ErrorCode.MAIL_UNAVAILABLE)
        target = bridge_path(self.paths)
        try:
            _check(self.paths.data_dir)
            _check(target.parent)
            _check(target, executable=True)
            command = [str(target), operation]
            if operation == "headers":
                command.append(json.dumps([box.encode() for box in self.mailboxes]))
            output = await run_bounded(command, timeout=60 if operation == "authorize" else 12)
            result = json.loads(output)
            if (
                not isinstance(result, dict)
                or type(result.get("schema_version")) is not int
                or result["schema_version"] != 1
            ):
                raise ValueError
            status = result.get("status")
            if status not in {
                "authorized",
                "not_determined",
                "denied",
                "restricted",
                "not_running",
                "unavailable",
            }:
                raise ValueError
            clean: dict[str, Any] = {"schema_version": 1, "status": status}
            if operation in {"status", "authorize"} or status != "authorized":
                return clean
            for key in (
                ("headers", "selection_missing", "truncated")
                if operation == "headers"
                else ("mailboxes", "truncated")
            ):
                if key in result:
                    clean[key] = result[key]
            return clean
        except (OSError, ValueError, TypeError, RecursionError, ProviderUnavailable):
            raise ProviderUnavailable(ErrorCode.MAIL_UNAVAILABLE) from None


async def mail_status(paths: AppPaths) -> dict[str, Any]:
    if sys.platform != "darwin":
        return {"schema_version": 1, "status": "unsupported"}
    try:
        return await MailBridge(paths).call("status")
    except ProviderUnavailable:
        return {"schema_version": 1, "status": "setup_required"}


async def authorize_mail(paths: AppPaths) -> dict[str, Any]:
    if sys.platform != "darwin":
        return {"schema_version": 1, "status": "unsupported"}
    await _build(paths)
    return await MailBridge(paths).call("authorize")
