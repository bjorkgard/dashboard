"""Credits and quota observations are not token ledger entries or account bills."""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo


def _stamp(value: datetime) -> bool:
    return isinstance(value, datetime) and value.utcoffset() is not None


def _identifier(value: str) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 256
        and not any(ord(c) < 32 or 127 <= ord(c) < 160 for c in value)
    )


@dataclass(frozen=True)
class CreditRecord:
    id: str
    session_id: str
    timestamp: datetime
    amount: Decimal
    model: str | None = None

    def __post_init__(self) -> None:
        if (
            not _identifier(self.id)
            or not _identifier(self.session_id)
            or not _stamp(self.timestamp)
            or not isinstance(self.amount, Decimal)
            or not self.amount.is_finite()
            or not 0 <= self.amount <= 10**9
            or (self.model is not None and not _identifier(self.model))
        ):
            raise ValueError("Invalid recorded-credit metadata.")


@dataclass(frozen=True)
class CreditSummary:
    amount: Decimal
    events: int
    sessions: int


class CreditLedger:
    def __init__(self, *, timezone: str = "UTC", limit: int = 100_000) -> None:
        if type(limit) is not int or not 1 <= limit <= 100_000:
            raise ValueError("Invalid credit capacity.")
        self.zone = ZoneInfo(timezone)
        self.limit = limit
        self.records: dict[str, CreditRecord] = {}
        self.capped = False

    def prune(self, now: datetime) -> None:
        cutoff = now.astimezone(self.zone).date() - timedelta(days=29)
        self.records = {
            key: value
            for key, value in self.records.items()
            if value.timestamp.astimezone(self.zone).date() >= cutoff
        }

    def add(self, record: CreditRecord, now: datetime) -> bool:
        today = now.astimezone(self.zone).date()
        if not today - timedelta(days=29) <= record.timestamp.astimezone(self.zone).date() <= today:
            return False
        previous = self.records.get(record.id)
        if previous is not None:
            # Repeated per-turn credit reports are replacements, never additive snapshots.
            if record.timestamp >= previous.timestamp:
                self.records[record.id] = record
            return True
        if len(self.records) >= self.limit:
            self.capped = True
            return False
        self.records[record.id] = record
        return True

    def summary(self, now: datetime, days: int) -> CreditSummary:
        if days not in (1, 7, 30):
            raise ValueError("Unsupported credit window.")
        today = now.astimezone(self.zone).date()
        items = [
            r
            for r in self.records.values()
            if today - timedelta(days=days - 1) <= r.timestamp.astimezone(self.zone).date() <= today
        ]
        return CreditSummary(
            sum((r.amount for r in items), Decimal(0)),
            len(items),
            len({r.session_id for r in items}),
        )


@dataclass(frozen=True)
class QuotaWindow:
    limit_id: str
    window: str
    used_percent: float | None
    observed_at: datetime
    reset_at: datetime | None = None
    duration_minutes: int | None = None
    account_id: str | None = None

    def __post_init__(self) -> None:
        if (
            not _identifier(self.limit_id)
            or not _identifier(self.window)
            or not _stamp(self.observed_at)
            or (self.reset_at is not None and not _stamp(self.reset_at))
            or (self.account_id is not None and not _identifier(self.account_id))
            or (
                self.used_percent is not None
                and (
                    type(self.used_percent) not in (int, float)
                    or not math.isfinite(self.used_percent)
                    or not 0 <= self.used_percent <= 100
                )
            )
            or (
                self.duration_minutes is not None
                and (
                    type(self.duration_minutes) is not int
                    or not 0 < self.duration_minutes <= 525600
                )
            )
        ):
            raise ValueError("Invalid quota observation.")
