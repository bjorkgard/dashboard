import asyncio
import json
import signal
from datetime import UTC, datetime

import pytest

from dayboard.core.models import ErrorCode
from dayboard.core.providers import Capability, ProviderUnavailable
from dayboard.integrations.ai.codex import CodexLimitsProvider
from dayboard.integrations.ai.codex.limits import parse_limits
from dayboard.integrations.ai.codex.transport import CodexAppServer
from dayboard.settings import CodexSettings

NOW = datetime(2026, 11, 12, 12, tzinfo=UTC)


def payload(used=23):
    return {
        "rateLimits": {
            "limitId": "codex",
            "primary": {"usedPercent": used, "windowDurationMins": 300, "resetsAt": 1794490000},
            "secondary": {"usedPercent": None, "windowDurationMins": None, "resetsAt": None},
        },
        "rateLimitsByLimitId": {
            "codex": {
                "limitId": "codex",
                "primary": {
                    "usedPercent": used,
                    "windowDurationMins": 300,
                    "resetsAt": 1794490000,
                },
                "secondary": {"usedPercent": 5, "windowDurationMins": 10080, "resetsAt": None},
            },
            "codex_other": {
                "limitId": "codex_other",
                "primary": {"usedPercent": 10, "resetsAt": None},
            },
        },
        "token": "SECRET_CANARY",
    }


def test_normalize_all_windows_without_summing_snapshots():
    windows = parse_limits(payload(), NOW)
    assert len(windows) == 3
    assert sum(w.limit_id == "codex" for w in windows) == 2
    assert windows[1].reset_at is None
    assert "SECRET_CANARY" not in repr(windows)


def test_unknown_limit_and_account_identifiers_are_hashed():
    data = payload()
    data["accountId"] = "SECRET_CANARY"
    data["rateLimitsByLimitId"]["SECRET_CANARY"] = {
        "limitId": "SECRET_CANARY",
        "primary": {"usedPercent": 10},
    }
    windows = parse_limits(data, NOW)
    assert len(windows) == 4
    assert "SECRET_CANARY" not in repr(windows)


@pytest.mark.parametrize("used", [-1, 101, float("inf"), True, "SECRET_CANARY"])
def test_invalid_limit_envelope_is_safe(used):
    with pytest.raises(ProviderUnavailable) as caught:
        parse_limits(payload(used), NOW)
    assert caught.value.code == ErrorCode.AI_UNSUPPORTED
    assert "SECRET_CANARY" not in str(caught.value)


@pytest.mark.asyncio
async def test_limits_flag_independent_and_newest_replaces(tmp_path):
    class Transport:
        def __init__(self):
            self.used = 23

        async def read_limits(self):
            return payload(self.used)

    transport = Transport()
    p = CodexLimitsProvider(
        CodexSettings(False, str(tmp_path), limits_enabled=True),
        transport=transport,
        clock=lambda: NOW,
    )
    assert p.spec.key.provider == "codex-limits"
    assert p.spec.capabilities == (Capability.AI,)
    assert p.spec.interval_seconds == 300
    await p.collect()
    transport.used = 40
    snapshot = await p.collect()
    assert p.windows[0].used_percent == 40
    assert len(p.windows) == 3
    assert "SECRET_CANARY" not in repr(snapshot)
    with pytest.raises(ValueError):
        CodexLimitsProvider(CodexSettings(True, str(tmp_path)))


class FakeWriter:
    def __init__(self):
        self.messages = []

    def write(self, data):
        self.messages.append(json.loads(data))

    async def drain(self):
        pass

    def close(self):
        pass


class FakeProcess:
    pid = 12345
    returncode = None

    def __init__(self, rows, *, hang=False):
        self.stdin = FakeWriter()
        self.stdout = asyncio.StreamReader()
        for row in rows:
            self.stdout.feed_data(
                row if isinstance(row, bytes) else json.dumps(row).encode() + b"\n"
            )
        if not hang:
            self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_eof()

    async def wait(self):
        self.returncode = -15
        return self.returncode


def good_rows():
    return [
        {"id": 0, "result": {"userAgent": "codex/0.167.2"}},
        {"method": "account/rateLimits/updated", "params": {"token": "SECRET_CANARY"}},
        {"id": 1, "result": payload()},
    ]


def install_fake(monkeypatch, proc):
    calls, kills = [], []

    async def spawn(*args, **kwargs):
        calls.append((args, kwargs))
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr("os.killpg", lambda pid, sig: kills.append((pid, sig)))
    return calls, kills


@pytest.mark.asyncio
async def test_owned_stdio_handshake_exact_read_only_allowlist(monkeypatch, tmp_path):
    proc = FakeProcess(good_rows())
    calls, kills = install_fake(monkeypatch, proc)
    result = await CodexAppServer(str(tmp_path)).read_limits()
    assert result["rateLimits"]["limitId"] == "codex"
    assert [m["method"] for m in proc.stdin.messages] == [
        "initialize",
        "initialized",
        "account/rateLimits/read",
    ]
    args, options = calls[0]
    assert args == ("codex", "app-server", "--listen", "stdio://")
    assert options["start_new_session"] is True
    assert options["env"]["CODEX_HOME"] == str(tmp_path)
    assert options["cwd"] != str(tmp_path)
    assert kills == [(proc.pid, signal.SIGTERM), (proc.pid, signal.SIGKILL)]


@pytest.mark.asyncio
async def test_failed_limit_refresh_retains_last_good_windows(tmp_path):
    class Transport:
        fail = False

        async def read_limits(self):
            if self.fail:
                return {"rateLimits": "SECRET_CANARY"}
            return payload()

    transport = Transport()
    p = CodexLimitsProvider(
        CodexSettings(False, str(tmp_path), limits_enabled=True),
        transport=transport,
        clock=lambda: NOW,
    )
    await p.collect()
    before = p.windows
    transport.fail = True
    with pytest.raises(ProviderUnavailable):
        await p.collect()
    assert p.windows == before


@pytest.mark.asyncio
async def test_stderr_flood_is_bounded_without_exposing_diagnostics(monkeypatch, tmp_path):
    proc = FakeProcess([], hang=True)
    proc.stderr = asyncio.StreamReader()
    proc.stderr.feed_data(b"SECRET_CANARY" * 10000)
    _, kills = install_fake(monkeypatch, proc)
    with pytest.raises(ProviderUnavailable) as caught:
        await CodexAppServer(str(tmp_path)).read_limits()
    assert "SECRET_CANARY" not in str(caught.value)
    assert kills


@pytest.mark.asyncio
async def test_broken_stdin_cleanup_still_terminates_owned_group(monkeypatch, tmp_path):
    proc = FakeProcess(good_rows())

    def broken_close():
        raise OSError("SECRET_CANARY")

    proc.stdin.close = broken_close
    _, kills = install_fake(monkeypatch, proc)
    await CodexAppServer(str(tmp_path)).read_limits()
    assert kills


@pytest.mark.parametrize(
    "rows",
    [
        [b"SECRET_CANARY\n"],
        [{"id": 0, "error": {"message": "SECRET_CANARY"}}],
        [{"id": 0, "method": "dangerous/request", "params": {"text": "SECRET_CANARY"}}],
        [b"x" * (256 * 1024 + 1) + b"\n"],
    ],
)
@pytest.mark.asyncio
async def test_invalid_server_output_is_capped_sanitized_and_cleaned(monkeypatch, tmp_path, rows):
    proc = FakeProcess(rows)
    _, kills = install_fake(monkeypatch, proc)
    with pytest.raises(ProviderUnavailable) as caught:
        await CodexAppServer(str(tmp_path)).read_limits()
    assert "SECRET_CANARY" not in str(caught.value)
    assert kills


@pytest.mark.asyncio
async def test_timeout_and_cancellation_clean_owned_group(monkeypatch, tmp_path):
    proc = FakeProcess([], hang=True)
    _, kills = install_fake(monkeypatch, proc)
    with pytest.raises(ProviderUnavailable) as caught:
        await CodexAppServer(str(tmp_path), timeout=0.01).read_limits()
    assert caught.value.code == ErrorCode.TIMEOUT
    assert kills
    proc = FakeProcess([], hang=True)
    _, kills = install_fake(monkeypatch, proc)
    task = asyncio.create_task(CodexAppServer(str(tmp_path)).read_limits())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert kills
