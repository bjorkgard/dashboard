"""Read-only descriptor traversal and transactional, bounded JSONL tail batches.

Raw lines exist only inside a worker pass. Cursors never retain conversation bytes.
"""

import hashlib
import json
import os
import stat
import threading
import time
from dataclasses import dataclass
from pathlib import Path

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

    def commit(self, batch: ScanBatch) -> None:
        self.files, self.cursors, self.issues = batch.files, batch.cursors, batch.issues
        self.discovered_at, self.next_file = batch.discovered_at, batch.next_file

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
            raise ProviderUnavailable(ErrorCode.AI_PERMISSION_DENIED) from None
        except OSError:
            raise ProviderUnavailable(ErrorCode.AI_SOURCE_UNAVAILABLE) from None
        try:
            files = list(self.files)
            discovered_at = self.discovered_at
            pending = False
            if time.monotonic() - discovered_at >= 30:
                files = []
                queue: list[tuple[str, ...]] = [()]
                entries = 0
                try:
                    while queue:
                        check()
                        parts = queue.pop()
                        fd = _open_relative(root_fd, parts, directory=True)
                        try:
                            with os.scandir(fd) as directory:
                                for entry in directory:
                                    check()
                                    entries += 1
                                    if entries > self.max_entries:
                                        issues.add("discovery cap")
                                        queue.clear()
                                        break
                                    relative = (*parts, entry.name)
                                    if entry.is_symlink():
                                        issues.add("unsafe entry skipped")
                                    elif entry.is_dir(follow_symlinks=False):
                                        if len(relative) < self.max_depth:
                                            queue.append(relative)
                                        else:
                                            issues.add("depth cap")
                                    elif entry.name.endswith(".jsonl"):
                                        if len(files) < self.max_files:
                                            files.append(relative)
                                        else:
                                            issues.add("file cap")
                        finally:
                            os.close(fd)
                except _BudgetReached:
                    pending = True
                    issues.add("discovery budget")
                except OSError:
                    issues.add("unreadable directory skipped")
                discovered_at = time.monotonic()
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
            )
        finally:
            os.close(root_fd)
