import asyncio
import json
import os
import socket
import stat
import subprocess
import threading
from datetime import UTC, datetime

import pytest

from dayboard.core.models import ErrorCode
from dayboard.core.providers import ProviderUnavailable
from dayboard.integrations.ai.provider import ClaudeProvider
from dayboard.integrations.ai.reader import ClaudeReader, validate_source
from dayboard.settings import ClaudeSettings


def line(identifier="msg-one", output=2):
    return (
        json.dumps(
            {
                "type": "assistant",
                "timestamp": datetime.now(UTC).isoformat(),
                "sessionId": "session-one",
                "version": "2.1.96",
                "message": {
                    "id": identifier,
                    "model": "claude-example",
                    "content": [{"text": "PRIVATE_PROMPT_CANARY"}],
                    "usage": {"input_tokens": 10, "output_tokens": output},
                },
            }
        ).encode()
        + b"\n"
    )


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "claude"
    project = root / "projects" / "example"
    project.mkdir(parents=True)
    path = project / "session.jsonl"
    path.write_bytes(line())
    return root, path


async def test_provider_tails_partial_lines_and_revision_replay_without_writes(source):
    root, path = source
    provider = ClaudeProvider(ClaudeSettings(True, str(root)))
    before = path.stat()
    snapshot = await provider.collect()
    assert next(m.value for m in snapshot.metrics if m.id == "today-output") == 2
    assert "CANARY" not in repr(snapshot) + repr(provider.ledger.records)
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    assert stat.S_IMODE(path.stat().st_mode) == stat.S_IMODE(before.st_mode)
    update = line(output=50)
    with path.open("ab") as stream:
        stream.write(update[:-3])
    assert next(m.value for m in (await provider.collect()).metrics if m.id == "today-output") == 2
    with path.open("ab") as stream:
        stream.write(update[-3:])
    assert next(m.value for m in (await provider.collect()).metrics if m.id == "today-output") == 50
    path.write_bytes(line(output=50))
    assert next(m.value for m in (await provider.collect()).metrics if m.id == "today-output") == 50


@pytest.mark.parametrize(
    "kind", ["symlink-root", "symlink-projects", "symlink-file", "hardlink", "fifo"]
)
async def test_unsafe_sources_are_rejected_without_touching_targets(tmp_path, kind):
    root = tmp_path / "claude"
    root.mkdir()
    projects = root / "projects"
    projects.mkdir()
    target = tmp_path / "outside"
    target.write_bytes(line())
    before = target.stat()
    if kind == "symlink-root":
        link = tmp_path / "link"
        link.symlink_to(root, target_is_directory=True)
        with pytest.raises(ProviderUnavailable):
            validate_source(str(link))
        return
    if kind == "symlink-projects":
        projects.rmdir()
        projects.symlink_to(tmp_path, target_is_directory=True)
        with pytest.raises(ProviderUnavailable):
            validate_source(str(root))
        return
    path = projects / "bad.jsonl"
    if kind == "symlink-file":
        path.symlink_to(target)
    elif kind == "hardlink":
        os.link(target, path)
    else:
        os.mkfifo(path)
    provider = ClaudeProvider(ClaudeSettings(True, str(root)))
    snapshot = await provider.collect()
    assert next(m.value for m in snapshot.metrics if m.id == "diagnostics") != "None"
    assert not provider.ledger.records
    assert target.stat().st_mtime_ns == before.st_mtime_ns
    assert target.stat().st_mode == before.st_mode


async def test_large_backlog_makes_bounded_progress_without_retaining_raw_lines(
    source, monkeypatch
):
    root, path = source
    path.write_bytes(b"".join(line(f"msg-{index}") for index in range(60)))
    reader = ClaudeReader(str(root), max_record=400, byte_budget=1024)
    provider = ClaudeProvider(ClaudeSettings(True, str(root)), reader=reader)
    original = os.pread
    read_bytes = 0

    def counted(fd, length, offset):
        nonlocal read_bytes
        data = original(fd, length, offset)
        read_bytes += len(data)
        return data

    monkeypatch.setattr(os, "pread", counted)
    for _ in range(100):
        read_bytes = 0
        result = await provider.collect()
        assert read_bytes <= 1024
        assert "PRIVATE_PROMPT_CANARY" not in repr(reader.cursors)
        if len(provider.ledger.records) == 60:
            break
    assert len(provider.ledger.records) == 60
    assert len(result.metrics) < 64
    assert "Indexing" not in str(next(m.value for m in result.metrics if m.id == "index"))


async def test_unsupported_format_is_partial_unknown_not_zero_and_credentials_are_not_read(source):
    root, path = source
    (root / ".credentials.json").write_text("PRIVATE_CREDENTIAL_CANARY")
    path.write_text('{"type":"assistant","message":{"usage":{"future_counter":10}}}\n')
    result = await ClaudeProvider(ClaudeSettings(True, str(root))).collect()
    assert next(m.value for m in result.metrics if m.id == "today-input") is None
    assert "unsupported" in str(next(m.value for m in result.metrics if m.id == "diagnostics"))
    assert "CANARY" not in repr(result)


def test_source_validation_does_not_change_modes_and_rejects_unowned_root(source, monkeypatch):
    root, path = source
    root.chmod(0o755)
    path.chmod(0o644)
    before = root.stat().st_mode, path.stat().st_mode
    validate_source(str(root))
    assert (root.stat().st_mode, path.stat().st_mode) == before
    monkeypatch.setattr(os, "getuid", lambda: -1)
    with pytest.raises(ProviderUnavailable):
        validate_source(str(root))


async def test_discovery_depth_and_entry_caps_do_not_claim_an_empty_complete_scan(source):
    root, _ = source
    for reader in (
        ClaudeReader(str(root), max_depth=1),
        ClaudeReader(str(root), max_entries=1),
    ):
        result = await ClaudeProvider(ClaudeSettings(True, str(root)), reader=reader).collect()
        assert "Partial" in str(next(m.value for m in result.metrics if m.id == "index"))
        assert next(m.value for m in result.metrics if m.id == "today-input") is None


async def test_empty_supported_source_is_zero_and_source_check_time_is_fresh(source):
    root, path = source
    path.write_text('{"type":"user","message":"PRIVATE_PROMPT_CANARY"}\n')
    result = await ClaudeProvider(ClaudeSettings(True, str(root))).collect()
    assert next(m.value for m in result.metrics if m.id == "today-input") == 0
    assert next(m.value for m in result.metrics if m.id == "activity") is None
    assert abs((datetime.now(UTC) - result.observed_at).total_seconds()) < 5


async def test_collection_never_opens_credentials_network_or_subprocesses(source, monkeypatch):
    root, _ = source
    original_open = os.open

    def safe_open(path, *args, **kwargs):
        assert str(path) not in (".credentials.json", "settings.json")
        return original_open(path, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("Collection must not authenticate, execute or connect")

    monkeypatch.setattr(os, "open", safe_open)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    result = await ClaudeProvider(ClaudeSettings(True, str(root))).collect()
    assert next(m.value for m in result.metrics if m.id == "today-input") == 10


async def test_missing_and_removed_sources_are_not_empty_usage(source):
    root, path = source
    provider = ClaudeProvider(ClaudeSettings(True, str(root)))
    await provider.collect()
    path.unlink()
    (root / "projects" / "example").rmdir()
    (root / "projects").rmdir()
    with pytest.raises(ProviderUnavailable) as error:
        await provider.collect()
    assert error.value.code == ErrorCode.AI_SOURCE_UNAVAILABLE
    assert provider.ledger.records  # last-good retained by provider/store, not erased


async def test_caps_and_oversized_lines_disclose_partial_coverage(source):
    root, path = source
    path.write_bytes(b'{"private":"' + b"x" * 500 + b'"}\n' + line("msg-next"))
    reader = ClaudeReader(str(root), max_record=400, byte_budget=1024)
    provider = ClaudeProvider(ClaudeSettings(True, str(root)), reader=reader)
    for _ in range(8):
        snapshot = await provider.collect()
    assert len(provider.ledger.records) == 1
    assert "partial" in str(next(m.value for m in snapshot.metrics if m.id == "index")).lower()
    assert "CANARY" not in repr(snapshot)


async def test_file_depth_and_entry_caps_are_visible(source):
    root, path = source
    path.with_name("two.jsonl").write_bytes(line("msg-two"))
    provider = ClaudeProvider(
        ClaudeSettings(True, str(root)), reader=ClaudeReader(str(root), max_files=1)
    )
    result = await provider.collect()
    assert "partial" in str(next(m.value for m in result.metrics if m.id == "index")).lower()


async def test_cancellation_awaits_worker_and_does_not_commit_a_batch(source, monkeypatch):
    root, _ = source
    provider = ClaudeProvider(ClaudeSettings(True, str(root)))
    entered, finished = threading.Event(), threading.Event()

    def blocked_scan(stop):
        entered.set()
        stop.wait(2)
        finished.set()
        return None

    monkeypatch.setattr(provider.reader, "scan", blocked_scan)
    task = asyncio.create_task(provider.collect())
    for _ in range(100):
        if entered.is_set():
            break
        await asyncio.sleep(0.01)
    assert entered.is_set()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()
    assert not provider.ledger.records
