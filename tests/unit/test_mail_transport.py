import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock

import pytest

from dayboard.core.providers import ProviderUnavailable
from dayboard.integrations.mail import authorize_mail, discover_mailboxes, mail_status
from dayboard.integrations.mail import bridge as module
from dayboard.paths import AppPaths


@pytest.fixture
def paths(tmp_path):
    return AppPaths(tmp_path / "data", tmp_path / "cache")


async def test_status_does_not_compile_or_prompt(paths, monkeypatch):
    compile_mock = AsyncMock(side_effect=AssertionError("must not build"))
    runner = AsyncMock(return_value=b'{"schema_version":1,"status":"not_determined"}')
    monkeypatch.setattr(module, "compile_bridge", compile_mock)
    monkeypatch.setattr(module, "_check", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "run_bounded", runner)
    monkeypatch.setattr(module.sys, "platform", "darwin")
    assert (await mail_status(paths))["status"] == "not_determined"
    assert runner.call_args.args[0][-1] == "status"
    compile_mock.assert_not_called()
    assert not paths.data_dir.exists()


async def test_status_response_projects_safe_fields_only(paths, monkeypatch):
    runner = AsyncMock(
        return_value=json.dumps(
            {
                "schema_version": 1,
                "status": "authorized",
                "body": "PRIVATE_CANARY",
                "headers": [{"subject": "PRIVATE_CANARY"}],
            }
        ).encode()
    )
    monkeypatch.setattr(module, "_check", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "run_bounded", runner)
    monkeypatch.setattr(module.sys, "platform", "darwin")
    assert await mail_status(paths) == {"schema_version": 1, "status": "authorized"}


async def test_status_timeout_returns_safe_unavailable_status(paths, monkeypatch):
    monkeypatch.setattr(module, "_check", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "run_bounded", AsyncMock(side_effect=TimeoutError))
    monkeypatch.setattr(module.sys, "platform", "darwin")
    result = await mail_status(paths)
    assert result["status"] in {"setup_required", "unavailable"}


async def test_oversized_discovery_is_rejected(paths, monkeypatch):
    runner = AsyncMock(
        return_value=json.dumps(
            {
                "schema_version": 1,
                "status": "authorized",
                "mailboxes": [{"account_id": "fixture", "mailbox_path": ["Inbox"]}] * 201,
            }
        ).encode()
    )
    monkeypatch.setattr(module, "_check", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "run_bounded", runner)
    monkeypatch.setattr(module.sys, "platform", "darwin")
    with pytest.raises(ValueError, match="Invalid Mail"):
        await discover_mailboxes(paths)


async def test_discovery_projects_only_mailbox_selections(paths, monkeypatch):
    runner = AsyncMock(
        return_value=json.dumps(
            {
                "schema_version": 1,
                "status": "authorized",
                "mailboxes": [
                    {"account_id": "fixture", "account_name": "Test", "mailbox_path": ["Inbox"]}
                ],
                "body": "PRIVATE_CANARY",
            }
        ).encode()
    )
    monkeypatch.setattr(module, "_check", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "run_bounded", runner)
    monkeypatch.setattr(module.sys, "platform", "darwin")
    result = await discover_mailboxes(paths)
    assert result[0].mailbox_path == ("Inbox",)
    assert "PRIVATE_CANARY" not in repr(result)
    assert runner.call_args.args[0][-1] == "discover"


async def test_only_explicit_authorize_builds(paths, monkeypatch):
    build = AsyncMock()
    call = AsyncMock(return_value={"schema_version": 1, "status": "denied"})
    monkeypatch.setattr(module, "_build", build)
    monkeypatch.setattr(module.MailBridge, "call", call)
    monkeypatch.setattr(module.sys, "platform", "darwin")
    assert (await authorize_mail(paths))["status"] == "denied"
    build.assert_awaited_once_with(paths)
    call.assert_awaited_once_with("authorize")


@pytest.mark.parametrize(
    "output",
    [
        b"PRIVATE_CANARY",
        b'{"schema_version":true,"body":"PRIVATE_CANARY"}',
        b'["PRIVATE_CANARY"]',
    ],
)
async def test_bad_transport_response_has_safe_error(paths, monkeypatch, output):
    monkeypatch.setattr(module, "_check", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "run_bounded", AsyncMock(return_value=output))
    monkeypatch.setattr(module.sys, "platform", "darwin")
    with pytest.raises(ProviderUnavailable) as error:
        await module.MailBridge(paths).call("headers")
    assert "PRIVATE_CANARY" not in str(error.value)


async def test_unknown_operation_is_rejected_before_process(paths, monkeypatch):
    runner = AsyncMock()
    monkeypatch.setattr(module, "run_bounded", runner)
    with pytest.raises(ValueError):
        await module.MailBridge(paths).call("send")
    runner.assert_not_called()


async def test_unsafe_helper_fails_without_mode_or_byte_changes(paths, monkeypatch):
    paths.data_dir.mkdir()
    helper_dir = paths.data_dir / "helpers"
    helper_dir.mkdir()
    target = helper_dir / "mail-bridge"
    # A symlink must not be followed, read, executed or chmod'ed.
    source = paths.data_dir / "untrusted"
    source.write_bytes(b"SOURCE_PRIVATE_CANARY")
    target.symlink_to(source)
    before = source.stat()
    runner = AsyncMock()
    monkeypatch.setattr(module, "run_bounded", runner)
    monkeypatch.setattr(module.sys, "platform", "darwin")
    with pytest.raises(ProviderUnavailable):
        await module.MailBridge(paths).call("headers")
    after = source.stat()
    assert (before.st_mode, before.st_mtime_ns, before.st_size) == (
        after.st_mode,
        after.st_mtime_ns,
        after.st_size,
    )
    assert source.read_bytes() == b"SOURCE_PRIVATE_CANARY"
    runner.assert_not_called()


async def test_nonmac_fails_closed_without_process(paths, monkeypatch):
    runner = AsyncMock()
    monkeypatch.setattr(module, "run_bounded", runner)
    monkeypatch.setattr(module.sys, "platform", "linux")
    assert (await mail_status(paths))["status"] == "unsupported"
    assert (await authorize_mail(paths))["status"] == "unsupported"
    with pytest.raises(ProviderUnavailable):
        await discover_mailboxes(paths)
    runner.assert_not_called()


@pytest.mark.skipif(os.name != "posix", reason="Owned process groups require POSIX")
async def test_cancellation_cleans_owned_descendant(tmp_path):
    # Exercise the exact shared transport used by Mail with a synthetic process only.
    pid_file = tmp_path / "pid"
    script = (
        "import subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        "open(sys.argv[1],'w').write(str(child.pid)); time.sleep(30)"
    )
    task = asyncio.create_task(
        module.run_bounded(
            [sys.executable, "-c", script, str(pid_file)],
            timeout=5,
        )
    )
    for _ in range(100):
        if pid_file.exists():
            break
        await asyncio.sleep(0.01)
    assert pid_file.exists()
    pid = int(pid_file.read_text())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    import psutil

    for _ in range(100):
        if not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("Owned descendant still running")
