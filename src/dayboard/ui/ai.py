"""Bounded metadata presentations; no filesystem access from widgets."""

from dayboard.core.models import Metric, Snapshot
from dayboard.ui.card import SnapshotCard
from dayboard.ui.details import DetailCard


class AICard(SnapshotCard):
    def visible_metrics(self, snapshot: Snapshot) -> tuple[Metric, ...]:
        return tuple(
            metric
            for metric in snapshot.metrics
            if metric.id.startswith("today-")
            or metric.id
            in {
                "coverage",
                "index",
                "diagnostics",
                "activity",
                "cost",
            }
        )


class AIDetailCard(DetailCard):
    """All provider metrics are already bounded aggregates, never per-request records."""
