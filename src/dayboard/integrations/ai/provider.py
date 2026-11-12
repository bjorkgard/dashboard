"""One cooperative worker per enabled local source; no credentials or network."""

import asyncio
import threading
from collections import Counter
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from dayboard.core.ai_usage import UsageLedger
from dayboard.core.models import Coverage, Metric, ProviderKey, Quality, Snapshot
from dayboard.core.providers import Capability, ProviderSpec
from dayboard.integrations.ai.reader import ClaudeReader, ScanBatch, ScanCancelled
from dayboard.settings import ClaudeSettings

LABELS = ("Uncached input", "Output", "Cache writes", "Cache reads")
IDS = ("input", "output", "cache-write", "cache-read")


class ClaudeProvider:
    def __init__(self, settings: ClaudeSettings, *, reader: ClaudeReader | None = None) -> None:
        if not settings.enabled:
            raise ValueError("Claude collection requires explicit enablement.")
        self.settings = settings
        self.reader = reader or ClaudeReader(settings.source_root)
        self.ledger = UsageLedger(timezone=settings.timezone)
        self.spec = ProviderSpec(
            ProviderKey("claude-local"),
            "Claude Code selected local source",
            (Capability.AI,),
            interval_seconds=5,
            timeout_seconds=10,
        )

    def _collect(self, stop: threading.Event) -> tuple[ScanBatch, UsageLedger, Snapshot]:
        batch = self.reader.scan(stop)
        try:
            return self._aggregate(batch, stop)
        except BaseException:
            batch.close()
            raise

    def _aggregate(
        self,
        batch: ScanBatch,
        stop: threading.Event,
    ) -> tuple[ScanBatch, UsageLedger, Snapshot]:
        now = datetime.now(UTC)
        ledger = UsageLedger(timezone=self.settings.timezone, limit=self.ledger.limit)
        ledger.records = self.ledger.records.copy()
        ledger.quarantined = self.ledger.quarantined.copy()
        ledger.capped = self.ledger.capped
        ledger.prune(now)
        extra_issues: set[str] = set()
        for record in batch.records:
            if stop.is_set():
                raise ScanCancelled()
            if record.timestamp > now + timedelta(minutes=5):
                extra_issues.add("future-dated usage skipped")
                continue
            ledger.add(record, now)
        if extra_issues:
            batch = replace(batch, issues=batch.issues | extra_issues)
        partial = bool(batch.issues or ledger.conflicts or ledger.capped)
        incomplete = batch.pending or partial
        metrics = [
            Metric(
                "coverage",
                "Coverage",
                "Claude Code · selected local source · partial account coverage",
            ),
            Metric(
                "index",
                "Source indexing",
                (
                    "Indexing / observed so far"
                    if batch.pending
                    else "Partial scan"
                    if partial
                    else "Indexed available files (not proof of complete history)"
                )
                + f" · {len(batch.cursors)}/{len(batch.files)} files · {self.settings.timezone}",
            ),
            Metric(
                "diagnostics",
                "Safe diagnostics",
                "; ".join(sorted(batch.issues))
                + (
                    f"; {ledger.conflicts} conflicting identities quarantined"
                    if ledger.conflicts
                    else ""
                )
                + ("; ledger capacity reached" if ledger.capped else "")
                or "None",
            ),
        ]
        for days, prefix in ((1, "today"), (7, "week"), (30, "month")):
            if stop.is_set():
                raise ScanCancelled()
            summary = ledger.summary(now, days)
            for identifier, label, value in zip(IDS, LABELS, summary.tokens, strict=True):
                metrics.append(
                    Metric(
                        f"{prefix}-{identifier}",
                        f"{days}d {label}",
                        None if not summary.requests and incomplete else value,
                        "tokens",
                    )
                )
            metrics.append(
                Metric(
                    f"{prefix}-sessions",
                    f"{days}d observed sessions",
                    None if not summary.requests and incomplete else summary.sessions,
                )
            )
        latest = max((record.timestamp for record in ledger.records.values()), default=None)
        metrics.append(
            Metric(
                "activity",
                "Latest observed usage (not source freshness)",
                latest.astimezone(ledger.zone).isoformat() if latest else None,
            )
        )
        model_counts = Counter(record.model for record in ledger.records.values())
        top_models = model_counts.most_common(10)
        for index, (model, _) in enumerate(top_models):
            if stop.is_set():
                raise ScanCancelled()
            totals = ledger.summary(now, 30, model=model).tokens
            metrics.append(
                Metric(
                    f"model-{index}",
                    f"30d {model}",
                    " / ".join("Unknown" if value is None else str(value) for value in totals)
                    + " · input / output / cache write / cache read",
                )
            )
        other = ledger.summary(now, 30, excluded_models=frozenset(model for model, _ in top_models))
        metrics.append(
            Metric(
                "model-remainder",
                "30d remaining models (input / output / cache write / cache read)",
                " / ".join("Unknown" if value is None else str(value) for value in other.tokens),
            )
        )
        metrics.extend(
            (
                Metric("models-other", "Other models", max(0, len(model_counts) - 10)),
                Metric("cost", "Cost / account limits", "Unavailable · no billing or quota access"),
            )
        )
        if stop.is_set():
            raise ScanCancelled()
        return (
            batch,
            ledger,
            Snapshot(
                self.spec.key,
                now,
                now,
                tuple(metrics),
                Quality.DERIVED,
                Coverage.PARTIAL,
            ),
        )

    async def collect(self) -> Snapshot:
        stop = threading.Event()
        worker = asyncio.create_task(asyncio.to_thread(self._collect, stop))
        try:
            batch, ledger, snapshot = await asyncio.shield(worker)
        except asyncio.CancelledError:
            stop.set()
            # Await the actual worker: cancelling to_thread alone does not stop filesystem work.
            with suppress(Exception):
                batch, _, _ = await asyncio.shield(worker)
                batch.close()
            self.reader.close()
            raise
        self.reader.commit(batch)
        self.ledger = ledger
        return snapshot

    def close(self) -> None:
        self.reader.close()
