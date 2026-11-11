from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime

from dayboard.core.cache import CacheError as CacheError
from dayboard.core.cache import PublicCache as PublicCache
from dayboard.core.models import (
    ConnectionState,
    ErrorCode,
    ProviderKey,
    Snapshot,
    SourceStatus,
)


@dataclass
class _Entry:
    history: deque[Snapshot] = field(default_factory=deque)
    error: ErrorCode | None = None
    attempted_at: datetime | None = None


class SnapshotStore:
    def __init__(self, *, history_limit: int = 120, max_sources: int = 128) -> None:
        if type(history_limit) is not int or type(max_sources) is not int:
            raise ValueError("Store limits must be integers.")
        if not 1 <= history_limit <= 10_000 or not 1 <= max_sources <= 1024:
            raise ValueError("Store limits are out of range.")
        self.history_limit = history_limit
        self.max_sources = max_sources
        self._entries: dict[ProviderKey, _Entry] = {}

    def _entry(self, key: ProviderKey) -> _Entry:
        if key not in self._entries:
            if len(self._entries) >= self.max_sources:
                raise ValueError("Source capacity exceeded.")
            self._entries[key] = _Entry(deque(maxlen=self.history_limit))
        return self._entries[key]

    def publish(self, snapshot: Snapshot) -> None:
        entry = self._entry(snapshot.key)
        if entry.history and snapshot.observed_at < entry.history[-1].observed_at:
            return
        entry.history.append(snapshot)
        entry.error = None
        entry.attempted_at = snapshot.collected_at

    def fail(self, key: ProviderKey, error: ErrorCode, when: datetime | None = None) -> None:
        if not isinstance(error, ErrorCode):
            raise ValueError("Only allowlisted failure codes may enter the store.")
        entry = self._entry(key)
        entry.error = error
        entry.attempted_at = when or datetime.now(UTC)

    def history(self, key: ProviderKey) -> tuple[Snapshot, ...]:
        entry = self._entries.get(key)
        return tuple(entry.history) if entry else ()

    def status(
        self,
        key: ProviderKey,
        *,
        now: datetime | None = None,
        stale_after: float = 120,
    ) -> SourceStatus:
        entry = self._entries.get(key)
        if entry is None:
            return SourceStatus(ConnectionState.DISCONNECTED)
        snapshot = entry.history[-1] if entry.history else None
        if snapshot is None:
            return SourceStatus(
                ConnectionState.ERROR, error=entry.error, attempted_at=entry.attempted_at
            )
        age = ((now or datetime.now(UTC)) - snapshot.observed_at).total_seconds()
        state = ConnectionState.STALE if entry.error or age > stale_after else ConnectionState.READY
        return SourceStatus(state, snapshot, entry.error, entry.attempted_at)
