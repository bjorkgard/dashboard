"""Memory-only Kiro collection; source offsets and derived state commit together."""

import asyncio
import threading
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from dayboard.core.ai_observations import CreditLedger
from dayboard.core.models import Coverage, Metric, ProviderKey, Quality, Snapshot
from dayboard.core.providers import Capability, ProviderSpec
from dayboard.integrations.ai.kiro.profiles import KiroObservation, identity, parse_observation
from dayboard.integrations.ai.reader import LocalReader, ScanBatch, ScanCancelled
from dayboard.settings import KiroSettings

_LIMIT = 100_000


@dataclass(frozen=True)
class _State:
    ledger: CreditLedger
    revisions: dict[str, datetime]
    originals: dict[str, datetime]
    sessions: frozenset[str] = frozenset()
    profiles: frozenset[str] = frozenset()
    sessions_capped: bool = False


def _accept(path: tuple[str, ...]) -> bool:
    return path[-1] == "messages.jsonl" or (
        len(path) == 2 and path[0] == "cli" and path[-1].endswith(".jsonl")
    )


class KiroProvider:
    def __init__(
        self,
        settings: KiroSettings,
        *,
        reader: LocalReader[KiroObservation] | None = None,
    ) -> None:
        if not settings.enabled:
            raise ValueError("Kiro collection requires explicit enablement.")
        self.settings = settings
        source_id = identity("kiro-selected-source", settings.source_root)
        self.reader = reader or LocalReader(
            settings.source_root,
            parser=lambda data, path, offset: parse_observation(
                data, path, offset, source_id=source_id
            ),
            accept_file=_accept,
        )
        self._state = _State(CreditLedger(timezone=settings.timezone), {}, {})
        self._lock = asyncio.Lock()
        self.spec = ProviderSpec(
            ProviderKey("kiro-local"),
            "Kiro selected local sessions",
            (Capability.AI,),
            interval_seconds=5,
            timeout_seconds=10,
        )

    @property
    def ledger(self) -> CreditLedger:
        return self._state.ledger

    def _collect(
        self, stop: threading.Event
    ) -> tuple[ScanBatch[KiroObservation], _State, Snapshot]:
        batch = self.reader.scan(stop)
        try:
            return self._aggregate(batch, stop)
        except BaseException:
            batch.close()
            raise

    def _aggregate(
        self, batch: ScanBatch[KiroObservation], stop: threading.Event
    ) -> tuple[ScanBatch[KiroObservation], _State, Snapshot]:
        now = datetime.now(UTC)
        previous = self._state
        ledger = CreditLedger(timezone=self.settings.timezone, limit=previous.ledger.limit)
        ledger.records = previous.ledger.records.copy()
        ledger.capped = previous.ledger.capped
        ledger.prune(now)
        revisions = previous.revisions.copy()
        originals = previous.originals.copy()
        sessions = set(previous.sessions)
        profiles = set(previous.profiles)
        capped = previous.sessions_capped
        issues = set(batch.issues)
        for observation in batch.records:
            if stop.is_set():
                raise ScanCancelled()
            profiles.add(observation.profile)
            if observation.session_id is not None:
                if len(sessions) < _LIMIT or observation.session_id in sessions:
                    sessions.add(observation.session_id)
                else:
                    capped = True
            record = observation.credit
            if record is None:
                continue
            if record.timestamp > now + timedelta(minutes=5):
                issues.add("future-dated credit skipped")
                continue
            reported_at = record.timestamp
            if record.id in originals:
                if reported_at < revisions[record.id]:
                    continue
                # A revised summary replaces the amount, not its original event date.
                record = replace(record, timestamp=originals[record.id])
            elif len(originals) < _LIMIT:
                # Retain bounded identity dates even after the rolling ledger expires,
                # otherwise a later revision could masquerade as a new execution.
                originals[record.id] = record.timestamp
            else:
                capped = True
                continue
            revisions[record.id] = reported_at
            ledger.add(record, now)
        if ledger.capped or capped:
            issues.add("metadata capacity reached")
        state = _State(
            ledger, revisions, originals, frozenset(sessions), frozenset(profiles), capped
        )
        detected = (
            "Observed V3 recorded-credit profile"
            if "v3-credit" in profiles
            else "Observed V3 session-only profile; usage unavailable"
            if "v3-session-only" in profiles
            else "Unsupported or unobserved local format; usage unavailable"
        )
        if "unsupported" in profiles:
            detected += "; unsupported records also observed"
        metrics = [
            Metric(
                "coverage",
                "Coverage",
                "Kiro · selected local sessions · partial local-only coverage",
            ),
            Metric(
                "compatibility",
                "Format compatibility",
                detected
                + " · CLI 2.31.1 / website 3.x version numbers do not establish format support",
            ),
            Metric(
                "index",
                "Source indexing",
                ("Indexing / observed so far" if batch.pending else "Indexed available files")
                + f" · {len(batch.cursors)}/{len(batch.files)} files · {self.settings.timezone}"
                + " · recent-first mtime scheduling is not event time or complete history",
            ),
            Metric("diagnostics", "Safe diagnostics", "; ".join(sorted(issues)) or "None"),
            Metric("observed-sessions", "Observed V3 sessions (bounded)", len(sessions)),
            Metric("tokens", "Tokens", None),
            Metric("model", "Model attribution", None),
            Metric("account", "Account / quotas / billing", "Unavailable · no account access"),
        ]
        for days, prefix in ((1, "today"), (7, "week"), (30, "month")):
            if stop.is_set():
                raise ScanCancelled()
            summary = ledger.summary(now, days)
            # Absence of an event is never proof of zero in these partial local windows.
            metrics.extend(
                (
                    Metric(
                        f"{prefix}-credits",
                        f"{days}d recorded Kiro credits (observed so far)",
                        str(summary.amount) if summary.events else None,
                        "Kiro credits",
                    ),
                    Metric(
                        f"{prefix}-events",
                        f"{days}d observed credit executions",
                        summary.events if summary.events else None,
                    ),
                )
            )
        latest = max((record.timestamp for record in ledger.records.values()), default=None)
        metrics.append(
            Metric(
                "activity",
                "Latest original credit event (not source freshness)",
                latest.astimezone(ledger.zone).isoformat() if latest else None,
            )
        )
        if stop.is_set():
            raise ScanCancelled()
        return (
            batch,
            state,
            Snapshot(self.spec.key, now, now, tuple(metrics), Quality.REPORTED, Coverage.PARTIAL),
        )

    async def collect(self) -> Snapshot:
        async with self._lock:
            stop = threading.Event()
            worker = asyncio.create_task(asyncio.to_thread(self._collect, stop))
            try:
                batch, state, snapshot = await asyncio.shield(worker)
            except asyncio.CancelledError:
                stop.set()
                with suppress(Exception):
                    batch, _, _ = await asyncio.shield(worker)
                    batch.close()
                self.reader.close()
                raise
            self.reader.commit(batch)
            self._state = state
            return snapshot

    def close(self) -> None:
        self.reader.close()
