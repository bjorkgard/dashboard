import asyncio
import os
import threading
from types import SimpleNamespace

import pytest

import dayboard.integrations.ai.reader as module
from dayboard.core.providers import ProviderRegistry
from dayboard.core.scheduler import Scheduler
from dayboard.core.store import SnapshotStore
from dayboard.integrations.ai.provider import ClaudeProvider
from dayboard.integrations.ai.reader import ClaudeReader, ScanCancelled
from dayboard.settings import ClaudeSettings


class Clock:
    value = 1000.0

    def __call__(self):
        return self.value


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "claude"
    for name in ("first", "second", "third"):
        project = root / "projects" / name
        project.mkdir(parents=True)
        (project / "session.jsonl").write_text('{"type":"user"}\n')
    return root


def test_discovery_resumes_queued_projects_even_after_rediscovery_interval(tree, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=clock))
    original = module._open_relative

    def slow_directory(root_fd, parts, *, directory=False):
        fd = original(root_fd, parts, directory=directory)
        if directory and parts:
            clock.value += 1.1
        return fd

    monkeypatch.setattr(module, "_open_relative", slow_directory)
    reader = ClaudeReader(str(tree), seconds=1)
    found = set()
    for _ in range(12):
        batch = reader.scan(threading.Event())
        reader.commit(batch)
        found.update(batch.files)
        # A paused traversal must resume even if more than 30 seconds have elapsed.
        clock.value += 31
        if len(batch.files) == 3 and not batch.pending:
            break
    assert found == {(name, "session.jsonl") for name in ("first", "second", "third")}
    assert len(batch.files) == 3 and not batch.pending
    assert "discovery budget" not in batch.issues


class TimedEntries:
    def __init__(self, iterator, clock):
        self.iterator, self.clock = iterator, clock
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        entry = next(self.iterator)
        self.clock.value += 0.4
        return entry

    def close(self):
        self.iterator.close()
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_discovery_resumes_within_a_directory_without_losing_consumed_entries(
    tmp_path, monkeypatch
):
    root = tmp_path / "claude"
    projects = root / "projects"
    projects.mkdir(parents=True)
    for index in range(8):
        (projects / f"{index}.jsonl").write_text('{"type":"user"}\n')
    clock = Clock()
    original = os.scandir
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=clock))
    monkeypatch.setattr(module.os, "scandir", lambda fd: TimedEntries(original(fd), clock))
    reader = ClaudeReader(str(root), seconds=1)
    for _ in range(12):
        batch = reader.scan(threading.Event())
        reader.commit(batch)
        if len(batch.files) == 8 and not batch.pending:
            break
    assert set(batch.files) == {(f"{index}.jsonl",) for index in range(8)}
    assert not batch.pending


@pytest.mark.parametrize("error", [PermissionError, FileNotFoundError, OSError])
def test_bad_directory_does_not_abandon_readable_siblings(tree, monkeypatch, error):
    original = module._open_relative
    broken = None

    def fail_one(root_fd, parts, *, directory=False):
        nonlocal broken
        if directory and parts and broken is None:
            broken = parts
        if parts == broken:
            raise error("PRIVATE_PATH_CANARY")
        return original(root_fd, parts, directory=directory)

    monkeypatch.setattr(module, "_open_relative", fail_one)
    reader = ClaudeReader(str(tree))
    batch = reader.scan(threading.Event())
    assert len(batch.files) == 2
    assert {parts[0] for parts in batch.files} == {"first", "second", "third"} - {broken[0]}
    assert "unreadable directory skipped" in batch.issues
    assert "PRIVATE_PATH_CANARY" not in repr(batch.issues)


@pytest.fixture
def slow_entries(monkeypatch):
    clock = Clock()
    original = os.scandir
    opened = []

    def tracked(fd):
        iterator = TimedEntries(original(fd), clock)
        opened.append(iterator)
        return iterator

    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=clock))
    monkeypatch.setattr(module.os, "scandir", tracked)
    return clock, opened


def test_discovery_caps_apply_to_the_whole_generation_across_pauses(tree, slow_entries):
    reader = ClaudeReader(str(tree), seconds=1, max_entries=4, max_files=1)
    for _ in range(12):
        batch = reader.scan(threading.Event())
        reader.commit(batch)
        if not batch.pending:
            break
    assert not batch.pending
    assert "discovery cap" in batch.issues
    assert len(batch.files) <= 1
    assert all(iterator.closed for iterator in slow_entries[1])


def test_cancelled_scan_closes_resumed_iterator_without_committing_progress(tree, slow_entries):
    reader = ClaudeReader(str(tree), seconds=1)
    batch = reader.scan(threading.Event())
    reader.commit(batch)
    assert batch.pending
    assert sum(not iterator.closed for iterator in slow_entries[1]) == 1
    before = reader.files, reader.cursors.copy()
    stop = threading.Event()
    stop.set()
    with pytest.raises(ScanCancelled):
        reader.scan(stop)
    assert all(iterator.closed for iterator in slow_entries[1])
    assert (reader.files, reader.cursors) == before


async def test_idle_scheduler_shutdown_closes_pending_discovery(tree, slow_entries):
    reader = ClaudeReader(str(tree), seconds=1)
    provider = ClaudeProvider(ClaudeSettings(True, str(tree)), reader=reader)
    registry = ProviderRegistry()
    registry.register(provider)
    scheduler = Scheduler(registry, SnapshotStore())
    assert await scheduler.refresh(provider.spec.key)
    assert sum(not iterator.closed for iterator in slow_entries[1]) == 1
    await scheduler.close()
    await scheduler.close()  # cleanup is idempotent, including after on_unmount
    assert all(iterator.closed for iterator in slow_entries[1])


async def test_provider_cancellation_after_discovery_closes_uncommitted_batch(
    tree,
    slow_entries,
    monkeypatch,
):
    reader = ClaudeReader(str(tree), seconds=1)
    provider = ClaudeProvider(ClaudeSettings(True, str(tree)), reader=reader)
    entered = threading.Event()

    def cancelled_aggregation(batch, stop):
        entered.set()
        stop.wait(2)
        raise ScanCancelled()

    monkeypatch.setattr(provider, "_aggregate", cancelled_aggregation)
    task = asyncio.create_task(provider.collect())
    for _ in range(100):
        if entered.is_set():
            break
        await asyncio.sleep(0.01)
    assert entered.is_set()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert all(iterator.closed for iterator in slow_entries[1])
    assert not reader.files and not reader.cursors and not provider.ledger.records
