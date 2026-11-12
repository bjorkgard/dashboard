"""Memory-only Codex local accounting, committed together with reader cursors."""

import asyncio
import threading
from collections import defaultdict
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from dayboard.core.ai_usage import UsageLedger, UsageRecord
from dayboard.core.models import Coverage, ErrorCode, Metric, ProviderKey, Quality, Snapshot
from dayboard.core.providers import Capability, ProviderSpec, ProviderUnavailable
from dayboard.integrations.ai.codex.parser import Counters, RolloutEvent, digest, parse_rollout
from dayboard.integrations.ai.reader import LocalReader, ScanBatch, ScanCancelled
from dayboard.settings import CodexSettings

_CAP = 50_000
_IDS = ("input", "output", "cache-write", "cache-read")
_LABELS = ("Uncached input", "Output (includes reasoning)", "Cache writes", "Cache reads")


@dataclass(frozen=True)
class _Context:
    session: str | None = None
    model: str = "unknown"
    offset: int = -1


@dataclass(frozen=True)
class _Checkpoint:
    session: str
    timestamp: datetime
    offset: int
    model: str
    counts: Counters
    first_call: bool


class CodexProvider:
    def __init__(
        self,
        settings: CodexSettings,
        *,
        readers: tuple[LocalReader[RolloutEvent], ...] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not settings.enabled:
            raise ValueError("Codex local collection requires explicit enablement.")
        self.settings, self.clock = settings, clock
        self.readers = (
            readers
            if readers is not None
            else tuple(
                LocalReader(
                    settings.source_root,
                    parser=parse_rollout,
                    subtree=(subtree,),
                    accept_file=lambda path: (
                        path[-1].startswith("rollout-") and path[-1].endswith(".jsonl")
                    ),
                )
                for subtree in ("sessions", "archived_sessions")
            )
        )
        self.ledger = UsageLedger(timezone=settings.timezone)
        self._contexts: dict[tuple[int, str], _Context] = {}
        self._checkpoints: dict[str, _Checkpoint] = {}
        self._issues: frozenset[str] = frozenset()
        self._lock = asyncio.Lock()
        self.spec = ProviderSpec(
            ProviderKey("codex-local"),
            "Codex selected local source",
            (Capability.AI,),
            interval_seconds=5,
            timeout_seconds=10,
        )

    def _collect(
        self, stop: threading.Event
    ) -> tuple[
        list[tuple[int, ScanBatch[RolloutEvent]]],
        dict[tuple[int, str], _Context],
        dict[str, _Checkpoint],
        UsageLedger,
        frozenset[str],
        Snapshot,
    ]:
        batches: list[tuple[int, ScanBatch[RolloutEvent]]] = []
        contexts, checkpoints = self._contexts.copy(), self._checkpoints.copy()
        issues = set(self._issues)
        now = self.clock()
        try:
            for index, reader in enumerate(self.readers):
                if stop.is_set():
                    raise ScanCancelled()
                try:
                    batch = reader.scan(stop)
                except ProviderUnavailable:
                    issues.add("selected subtree unavailable")
                    continue
                batches.append((index, batch))
                issues.update(batch.issues)
                for event in batch.records:
                    if stop.is_set():
                        raise ScanCancelled()
                    key = index, event.file_id
                    context = contexts.get(key, _Context())
                    if event.offset <= context.offset:
                        # A rewritten/rotated file starts fresh metadata, not stale attribution.
                        context = _Context()
                    if event.kind == "session":
                        context = _Context(event.session_id, "unknown", event.offset)
                    elif event.kind == "model":
                        context = _Context(context.session, event.model or "unknown", event.offset)
                    else:
                        context = _Context(context.session, context.model, event.offset)
                        if event.counts is not None:
                            if context.session is None:
                                issues.add("checkpoint without validated session skipped")
                            elif event.timestamp > now + timedelta(minutes=5):
                                issues.add("future-dated checkpoint skipped")
                            else:
                                identity = digest(
                                    f"{context.session}|{event.timestamp.isoformat()}|{event.counts}"
                                )
                                if identity not in checkpoints and len(checkpoints) >= _CAP:
                                    issues.add("checkpoint capacity reached")
                                else:
                                    candidate = _Checkpoint(
                                        context.session,
                                        event.timestamp,
                                        event.offset,
                                        context.model,
                                        event.counts,
                                        event.last == event.counts,
                                    )
                                    old = checkpoints.get(identity)
                                    if old is None or old.model == "unknown":
                                        checkpoints[identity] = candidate
                    if key in contexts or len(contexts) < 10_000:
                        contexts[key] = context
                    else:
                        issues.add("session capacity reached")
            if not batches:
                raise ProviderUnavailable(ErrorCode.AI_SOURCE_UNAVAILABLE)
            ledger = UsageLedger(timezone=self.settings.timezone)
            grouped: dict[str, list[tuple[str, _Checkpoint]]] = defaultdict(list)
            for identity, point in checkpoints.items():
                grouped[point.session].append((identity, point))
            for session, points in grouped.items():
                previous: Counters | None = None
                segment = "initial"
                for identity, point in sorted(
                    points, key=lambda pair: (pair[1].timestamp, pair[1].offset, pair[0])
                ):
                    if stop.is_set():
                        raise ScanCancelled()
                    counts = point.counts
                    if previous is None:
                        if point.first_call or counts == (0, 0, 0, 0):
                            previous = (0, 0, 0, 0)
                        elif counts != (0, 0, 0, 0):
                            issues.add("missing initial baseline; observed deltas only")
                    if previous is not None:
                        delta = tuple(a - b for a, b in zip(counts, previous, strict=True))
                        if any(v < 0 for v in delta):
                            segment = identity
                            issues.add("counter reset; new segment baseline")
                        elif delta[1] > delta[0] or delta[3] > delta[2]:
                            issues.add("inconsistent counter subsets skipped")
                        elif any(delta) or counts == (0, 0, 0, 0):
                            ledger.add(
                                UsageRecord(
                                    digest(f"codex|{session}|{segment}|{identity}"),
                                    session,
                                    None,
                                    point.timestamp,
                                    point.model,
                                    "rollout-v1",
                                    (delta[0] - delta[1], delta[2], None, delta[1]),
                                ),
                                now,
                            )
                    previous = counts
            pending = any(batch.pending for _, batch in batches)
            metrics = [
                Metric(
                    "coverage", "Coverage", "Codex · selected local source · observed deltas only"
                ),
                Metric(
                    "index",
                    "Source indexing",
                    "Indexing / observed so far" if pending else "Bounded available rollout files",
                ),
                Metric("diagnostics", "Safe diagnostics", "; ".join(sorted(issues)) or "None"),
            ]
            for days, prefix in ((1, "today"), (7, "week"), (30, "month")):
                summary = ledger.summary(now, days)
                for identifier, label, value in zip(_IDS, _LABELS, summary.tokens, strict=True):
                    metrics.append(
                        Metric(
                            f"{prefix}-{identifier}",
                            f"{days}d {label}",
                            value if summary.requests else None,
                            "tokens",
                        )
                    )
                metrics.append(
                    Metric(
                        f"{prefix}-sessions",
                        f"{days}d observed sessions",
                        summary.sessions if summary.requests else None,
                    )
                )
            models = sorted({r.model for r in ledger.records.values()})
            for index, model in enumerate(models[:10]):
                values = ledger.summary(now, 30, model=model).tokens
                metrics.append(
                    Metric(
                        f"model-{index}",
                        f"30d {model}",
                        " / ".join("Unknown" if v is None else str(v) for v in values),
                    )
                )
            metrics.append(
                Metric("models-other", "Other observed models", max(0, len(models) - 10))
            )
            if stop.is_set():
                raise ScanCancelled()
            snapshot = Snapshot(
                self.spec.key,
                now,
                now,
                tuple(metrics),
                Quality.DERIVED,
                Coverage.PARTIAL,
            )
            return batches, contexts, checkpoints, ledger, frozenset(issues), snapshot
        except BaseException:
            for _, batch in batches:
                batch.close()
            raise

    async def collect(self) -> Snapshot:
        async with self._lock:
            stop = threading.Event()
            worker = asyncio.create_task(asyncio.to_thread(self._collect, stop))
            try:
                result = await asyncio.shield(worker)
            except asyncio.CancelledError:
                stop.set()
                with suppress(Exception):
                    result = await asyncio.shield(worker)
                    for _, batch in result[0]:
                        batch.close()
                self.close()
                raise
            batches, contexts, checkpoints, ledger, issues, snapshot = result
            for index, batch in batches:
                self.readers[index].commit(batch)
            self._contexts, self._checkpoints = contexts, checkpoints
            self.ledger, self._issues = ledger, issues
            return snapshot

    def close(self) -> None:
        for reader in self.readers:
            reader.close()
