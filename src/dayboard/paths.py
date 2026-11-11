"""Owner-only state with no-follow reads and atomic, directory-relative writes."""

from __future__ import annotations

import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_cache_path, user_data_path

MAX_STATE_BYTES = 1_048_576


class PathSecurityError(OSError):
    """State is unsafe to access; never include file contents in the error."""


def _directory(path: Path, *, create: bool) -> int:
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.getuid():
            raise PathSecurityError("State directory is not owned by the current user.")
        os.fchmod(fd, 0o700)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _check_file(fd: int) -> None:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
        raise PathSecurityError("State must be an owned regular file without hard links.")
    os.fchmod(fd, 0o600)
    if info.st_size > MAX_STATE_BYTES:
        raise PathSecurityError("State exceeds the size limit.")


def private_read(path: Path) -> bytes:
    directory = _directory(path.parent, create=False)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            _check_file(fd)
            with os.fdopen(fd, "rb", closefd=False) as stream:
                data = stream.read(MAX_STATE_BYTES + 1)
            if len(data) > MAX_STATE_BYTES:
                raise PathSecurityError("State exceeds the size limit.")
            return data
        finally:
            os.close(fd)
    finally:
        os.close(directory)


def private_write(path: Path, data: bytes) -> None:
    if len(data) > MAX_STATE_BYTES:
        raise PathSecurityError("State exceeds the size limit.")
    directory = _directory(path.parent, create=True)
    temporary = f".{path.name}.{secrets.token_hex(12)}.tmp"
    try:
        try:
            existing = os.open(
                path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
            )
        except FileNotFoundError:
            pass
        else:
            try:
                _check_file(existing)
            finally:
                os.close(existing)
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.close(directory)


@dataclass(frozen=True)
class AppPaths:
    data_dir: Path
    cache_dir: Path

    @classmethod
    def default(cls) -> AppPaths:
        return cls(user_data_path("Dayboard", appauthor=False), user_cache_path("Dayboard"))

    @property
    def config_file(self) -> Path:
        return self.data_dir / "config.json"

    def ensure(self) -> None:
        try:
            for path in (self.data_dir, self.cache_dir):
                os.close(_directory(path, create=True))
        except OSError:
            raise PathSecurityError("Cannot prepare protected application directories.") from None
