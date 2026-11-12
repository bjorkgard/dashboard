"""Read-only descriptor traversal and transactional, bounded JSONL tail batches.

Raw lines exist only inside a worker pass. Cursors never retain conversation bytes.
"""

import hashlib
import json
import os
import stat
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from dayboard.core.ai_usage import UsageRecord
from dayboard.core.models import ErrorCode
from dayboard.core.providers import ProviderUnavailable
from dayboard.integrations.ai.claude import parse_usage

_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _owned(fd: int, *, directory: bool) -> os.stat_result:
    info = os.fstat(fd)
    if info.st_uid != os.getuid() or (
        not stat.S_ISDIR(info.st_mode)
        if directory
        else not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
    ):
        raise OSError("Unsafe source.")
    return info


def _projects(root: str) -> int:
    path = Path(root)
    if not path.is_absolute() or ".." in path.parts:
        raise OSError("Unsafe source.")
    fd = os.open("/", _DIR)
    try:
        for part in path.parts[1:]:
            child = os.open(part, _DIR, dir_fd=fd)
            os.close(fd)
            fd = child
        _owned(fd, directory=True)
        child = os.open("projects", _DIR, dir_fd=fd)
        try:
            _owned(child, directory=True)
        except BaseException:
            os.close(child)
            raise
        return child
    finally:
        os.close(fd)


def validate_source(root: str) -> None:
    try:
        os.close(_projects(root))
    except PermissionError:
        raise ProviderUnavailable(ErrorCode.AI_PERMISSION_DENIED) from None
    except OSError:
        raise ProviderUnavailable(ErrorCode.AI_SOURCE_UNAVAILABLE) from None


def _open_relative(root_fd: int, parts: tuple[str, ...], *, directory: bool = False) -> int:
    fd = os.dup(root_fd)
    try:
        for index, part in enumerate(parts):
            flags = (
                _DIR
                if directory or index < len(parts) - 1
                else (os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            )
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
            _owned(fd, directory=directory or index < len(parts) - 1)
        result, fd = fd, -1
        return result
    finally:
        if fd >= 0:
            os.close(fd)


class ScanCancelled(Exception):
    pass


class _BudgetReached(Exception):
    pass


class _DirectoryEntries(Protocol):
    def __next__(self) -> os.DirEntry[str]: ...
    def close(self) -> None: ...


class _Discovery:
    """One bounded discovery generation, owning at most one open directory iterator."""

    def __init__(self, identity: tuple[int, int]) -> None:
        self.identity = identity
        self.queue: list[tuple[str, ...]] = [()]
        self.current: tuple[str, ...] = ()
        self.iterator: _DirectoryEntries | None = None
        self.files: list[tuple[str, ...]] = []
        self.entries = 0
        self.complete = False

    def close(self) -> None:
        if self.iterator is not None:
            self.iterator.close()
            self.iterator = None

    def __del__(self) -> None:
        self.close()

    def advance(
        self,
        root_fd: int,
        reader: "ClaudeReader",
        check: Callable[[], None],
        issues: set[str],
    ) -> None:
        while self.queue or self.iterator is not None:
            # Check before consuming work: never lose a popped directory or fetched entry.
            check()
            if self.iterator is None:
                self.current = self.queue.pop()
                fd = -1
                try:
                    fd = _open_relative(root_fd, self.current, directory=True)
                    self.iterator = os.scandir(fd)
                except OSError:
                    issues.add("unreadable directory skipped")
                    continue
                finally:
                    if fd >= 0:
                        os.close(fd)
            try:
                entry = next(self.iterator)
            except StopIteration:
                self.close()
                continue
            except OSError:
                issues.add("unreadable directory skipped")
                self.close()
                continue
            self.entries += 1
            if self.entries > reader.max_entries:
                issues.add("discovery cap")
                self.queue.clear()
                self.close()
                break
            relative = (*self.current, entry.name)
            try:
                if entry.is_symlink():
                    issues.add("unsafe entry skipped")
                elif entry.is_dir(follow_symlinks=False):
                    if len(relative) < reader.max_depth:
                        self.queue.append(relative)
                    else:
                        issues.add("depth cap")
                elif entry.name.endswith(".jsonl"):
                    if len(self.files) < reader.max_files:
                        self.files.append(relative)
                    else:
                        issues.add("file cap")
            except OSError:
                issues.add("unreadable entry skipped")
        self.complete = True


@dataclass(frozen=True)
class Cursor:
    identity: tuple[int, int]
    offset: int
    head: bytes
    tail: bytes
    skipping: bool = False


@dataclass(frozen=True)
class ScanBatch:
    records: tuple[UsageRecord, ...]
    files: tuple[tuple[str, ...], ...]
    cursors: dict[tuple[str, ...], Cursor]
    issues: frozenset[str]
    pending: bool
    discovered_at: float
    next_file: int
    discovery: _Discovery | None = None

    def close(self) -> None:
        if self.discovery is not None:
            self.discovery.close()


class ClaudeReader:
    def __init__(
        self,
        root: str,
        *,
        max_files: int = 5000,
        max_depth: int = 8,
        max_entries: int = 20_000,
        byte_budget: int = 8 * 1024 * 1024,
        max_record: int = 1024 * 1024,
        seconds: float = 2,
    ) -> None:
        if not (
            1 <= max_files <= 5000
            and 1 <= max_depth <= 8
            and 1 <= max_entries <= 20_000
            and 512 <= byte_budget <= 8 * 1024 * 1024
            and 128 <= max_record <= 1024 * 1024
            and byte_budget >= max_record + 256
            and 0 < seconds <= 2
        ):
            raise ValueError("Invalid Claude reader budgets.")
        self.root = root
        self.max_files, self.max_depth, self.max_entries = max_files, max_depth, max_entries
        self.byte_budget, self.max_record, self.seconds = byte_budget, max_record, seconds
        self.files: tuple[tuple[str, ...], ...] = ()
        self.cursors: dict[tuple[str, ...], Cursor] = {}
        self.issues: frozenset[str] = frozenset()
        self.discovered_at = float("-inf")
        self.next_file = 0
        self._discovery: _Discovery | None = None

    def close(self) -> None:
        if self._discovery is not None:
            self._discovery.close()
            self._discovery = None

    def commit(self, batch: ScanBatch) -> None:
        self.close()
        self.files, self.cursors, self.issues = batch.files, batch.cursors, batch.issues
        self.discovered_at, self.next_file = batch.discovered_at, batch.next_file
        self._discovery = batch.discovery

    def scan(self, stop: threading.Event) -> ScanBatch:
        deadline = time.monotonic() + self.seconds
        remaining = self.byte_budget
        issues = set(self.issues)
        records: list[UsageRecord] = []

        def check() -> None:
            if stop.is_set():
                raise ScanCancelled()
            if time.monotonic() >= deadline:
                raise _BudgetReached()

        def read(fd: int, size: int, offset: int) -> bytes:
            nonlocal remaining
            check()
            # Reserve one bounded fingerprint read for a transactional cursor checkpoint.
            if remaining <= 64:
                raise _BudgetReached()
            data = os.pread(fd, min(size, remaining - 64), offset)
            remaining -= len(data)
            return data

        try:
            root_fd = _projects(self.root)
        except PermissionError:
            self.close()
            raise ProviderUnavailable(ErrorCode.AI_PERMISSION_DENIED) from None
        except OSError:
            self.close()
            raise ProviderUnavailable(ErrorCode.AI_SOURCE_UNAVAILABLE) from None
        # Transfer unfinished work to this worker/batch; aborts close it instead of publishing it.
        discovery, self._discovery = self._discovery, None
        try:
            files = list(self.files)
            discovered_at = self.discovered_at
            pending = False
            root_info = os.fstat(root_fd)
            identity = (root_info.st_dev, root_info.st_ino)
            if discovery is not None and discovery.identity != identity:
                discovery.close()
                discovery = None
                discovered_at = float("-inf")
            if discovery is not None or time.monotonic() - discovered_at >= 30:
                discovery = discovery or _Discovery(identity)
                try:
                    discovery.advance(root_fd, self, check, issues)
                except _BudgetReached:
                    pending = True
                if discovery.complete:
                    files = discovery.files
                    discovered_at = time.monotonic()
                    discovery = None
                else:
                    # Keep known files/cursors usable while the next generation is incomplete.
                    files = list(dict.fromkeys((*files, *discovery.files)))[: self.max_files]
            file_set = set(files)
            cursors = {key: value for key, value in self.cursors.items() if key in file_set}
            next_file = self.next_file % max(1, len(files))
            for count in range(len(files)):
                index = (next_file + count) % len(files)
                parts = files[index]
                fd = -1
                head = None
                info = None
                offset = start = 0
                skipping = False
                buffer = bytearray()
                try:
                    check()
                    fd = _open_relative(root_fd, parts)
                    info = _owned(fd, directory=False)
                    old = cursors.get(parts)
                    head = hashlib.sha256(read(fd, 64, 0)).digest()
                    offset, skipping = 0, False
                    if old is not None and old.identity == (info.st_dev, info.st_ino):
                        tail = hashlib.sha256(
                            read(fd, min(64, old.offset), max(0, old.offset - 64))
                        )
                        if (
                            old.offset <= info.st_size
                            and head == old.head
                            and tail.digest() == old.tail
                        ):
                            offset, skipping = old.offset, old.skipping
                    start = offset
                    while offset < info.st_size:
                        chunk = read(fd, min(65536, info.st_size - offset), offset)
                        if not chunk:
                            break
                        position = 0
                        while position < len(chunk):
                            check()
                            end = chunk.find(b"\n", position)
                            segment = chunk[position:] if end < 0 else chunk[position:end]
                            if not skipping:
                                if len(buffer) + len(segment) > self.max_record:
                                    buffer.clear()
                                    skipping = True
                                    issues.add("oversized record skipped")
                                else:
                                    buffer.extend(segment)
                            used = len(segment) + (end >= 0)
                            offset += used
                            position += used
                            if end >= 0:
                                if not skipping and buffer:
                                    try:
                                        item = parse_usage(json.loads(buffer))
                                        if item is not None:
                                            records.append(item)
                                    except (ValueError, UnicodeError, RecursionError):
                                        issues.add("unsupported or malformed record skipped")
                                buffer.clear()
                                skipping = False
                                start = offset
                    # Re-read partial records next pass; never retain raw partial content.
                    if buffer and not skipping:
                        offset = start
                        pending = True
                    tail_hash = hashlib.sha256(
                        read(fd, min(64, offset), max(0, offset - 64))
                    ).digest()
                    cursors[parts] = Cursor(
                        (info.st_dev, info.st_ino), offset, head, tail_hash, skipping
                    )
                except _BudgetReached:
                    pending = True
                    if fd >= 0 and head is not None and info is not None:
                        if buffer and not skipping:
                            offset = start
                        tail_hash = hashlib.sha256(
                            os.pread(fd, min(64, offset), max(0, offset - 64))
                        ).digest()
                        cursors[parts] = Cursor(
                            (info.st_dev, info.st_ino), offset, head, tail_hash, skipping
                        )
                    next_file = index
                    break
                except OSError:
                    issues.add("unsafe or unreadable file skipped")
                    cursors.pop(parts, None)
                finally:
                    if fd >= 0:
                        os.close(fd)
            return ScanBatch(
                tuple(records),
                tuple(files),
                cursors,
                frozenset(issues),
                pending,
                discovered_at,
                next_file,
                discovery,
            )
        except BaseException:
            if discovery is not None:
                discovery.close()
            raise
        finally:
            os.close(root_fd)
