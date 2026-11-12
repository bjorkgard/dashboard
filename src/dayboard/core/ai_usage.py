"""Private metadata only, scoped to one selected source; never persisted."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

type Counts = tuple[int | None, ...]


@dataclass(frozen=True)
class UsageRecord:
    message_id: str
    session_id: str
    request_id: str | None
    timestamp: datetime
    model: str
    version: str | None
    tokens: Counts
    cache_buckets: Counts = (None, None)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.timestamp, datetime)
            or self.timestamp.utcoffset() is None
            or not isinstance(self.tokens, tuple)
            or len(self.tokens) != 4
            or not isinstance(self.cache_buckets, tuple)
            or len(self.cache_buckets) != 2
            or any(
                value is not None and (type(value) is not int or not 0 <= value <= 10**12)
                for value in self.tokens + self.cache_buckets
            )
        ):
            raise ValueError("Invalid normalized usage metadata.")


@dataclass(frozen=True)
class UsageSummary:
    tokens: Counts
    requests: int
    sessions: int


def _dominates(new: Counts, old: Counts) -> bool:
    return all(b is None or (a is not None and a >= b) for a, b in zip(new, old, strict=True))


class UsageLedger:
    def __init__(self, *, timezone: str = "UTC", limit: int = 100_000) -> None:
        if type(limit) is not int or not 1 <= limit <= 100_000:
            raise ValueError("Invalid usage capacity.")
        self.zone = ZoneInfo(timezone)
        self.limit = limit
        self.records: dict[str, UsageRecord] = {}
        self.originals: dict[str, datetime] = {}
        self.historical_originals: dict[str, datetime] = {}
        self.quarantined: dict[str, datetime] = {}
        self.capped = False

    @property
    def conflicts(self) -> int:
        return len(self.quarantined)

    def prune(self, now: datetime) -> None:
        cutoff = now.astimezone(self.zone).date() - timedelta(days=29)
        for key, stamp in tuple(self.originals.items()):
            if stamp.astimezone(self.zone).date() < cutoff:
                self._remember_historical(key, stamp)
                del self.originals[key]
        self.records = {
            key: item
            for key, item in self.records.items()
            if item.timestamp.astimezone(self.zone).date() >= cutoff
        }
        self.quarantined = {
            key: stamp
            for key, stamp in self.quarantined.items()
            if stamp.astimezone(self.zone).date() >= cutoff
        }

    def add(self, item: UsageRecord, now: datetime) -> bool:
        today = now.astimezone(self.zone).date()
        key = item.message_id
        known = self.originals.get(key, self.historical_originals.get(key, item.timestamp))
        original = min(known, item.timestamp)
        item = replace(item, timestamp=original)
        if not today - timedelta(days=29) <= original.astimezone(self.zone).date() <= today:
            self.records.pop(key, None)
            self.originals.pop(key, None)
            self._remember_historical(key, original)
            return False
        if key not in self.originals and len(self.originals) >= self.limit:
            self.capped = True
            return False
        self.originals[key] = original
        if key in self.quarantined:
            return False
        old = self.records.get(key)
        if old is None:
            if len(self.records) + len(self.quarantined) >= self.limit:
                self.capped = True
                return False
            self.records[key] = item
            return True
        new_counts = item.tokens + item.cache_buckets
        old_counts = old.tokens + old.cache_buckets
        identity_conflict = item.model != old.model or (
            item.request_id is not None
            and old.request_id is not None
            and item.request_id != old.request_id
        )
        if identity_conflict or not (
            _dominates(new_counts, old_counts) or _dominates(old_counts, new_counts)
        ):
            del self.records[key]
            self.quarantined[key] = old.timestamp
            return False
        # A replay of an earlier partial is harmless. Never synthesize component-wise maxima.
        winner = item if _dominates(new_counts, old_counts) else old
        self.records[key] = replace(
            winner,
            timestamp=min(old.timestamp, item.timestamp),
            session_id=old.session_id,
            request_id=old.request_id or item.request_id,
        )
        return True

    def _remember_historical(self, key: str, stamp: datetime) -> None:
        if key in self.historical_originals:
            self.historical_originals[key] = min(self.historical_originals[key], stamp)
        elif len(self.historical_originals) < self.limit:
            self.historical_originals[key] = stamp
        else:
            self.capped = True

    def summary(
        self,
        now: datetime,
        days: int,
        *,
        model: str | None = None,
        excluded_models: frozenset[str] = frozenset(),
    ) -> UsageSummary:
        if days not in (1, 7, 30):
            raise ValueError("Unsupported usage window.")
        today = now.astimezone(self.zone).date()
        items = [
            item
            for item in self.records.values()
            if today - timedelta(days=days - 1)
            <= item.timestamp.astimezone(self.zone).date()
            <= today
            and (model is None or item.model == model)
            and item.model not in excluded_models
        ]
        totals = tuple(
            None
            if any(item.tokens[index] is None for item in items)
            else sum(item.tokens[index] or 0 for item in items)
            for index in range(4)
        )
        return UsageSummary(totals, len(items), len({item.session_id for item in items}))
