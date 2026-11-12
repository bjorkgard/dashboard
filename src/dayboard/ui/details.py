"""Expanded views of existing snapshots; no provider I/O or independent history."""

from rich.text import Text

from dayboard.core.models import Metric, Snapshot
from dayboard.security.redaction import safe_text
from dayboard.ui.card import SnapshotCard


class DetailCard(SnapshotCard):
    def visible_metrics(self, snapshot: Snapshot) -> tuple[Metric, ...]:
        return tuple(metric for metric in snapshot.metrics if metric.id != "scope")

    def append_metrics(self, content: Text, snapshot: Snapshot) -> None:
        previous_group = ""
        for metric in self.visible_metrics(snapshot):
            group = (
                "Pull requests"
                if metric.id.startswith("pr-") or metric.id == "no-prs"
                else "Recent default-branch commits"
                if metric.id.startswith("commit-") or metric.id == "no-commits"
                else "Agenda"
                if metric.id.startswith("event-")
                else "Source summary"
            )
            if group != previous_group:
                content.append(f"{group}\n", style="bold #88c0d0")
                previous_group = group
            value = "Unknown" if metric.value is None else str(metric.value)
            content.append(safe_text(metric.label) + "\n", style="bold #d8dee9")
            content.append(f"{safe_text(value)} {safe_text(metric.unit)}\n\n", style="#eceff4")
        content.append(
            f"Coverage: {snapshot.coverage.value} · "
            f"collected {snapshot.collected_at.astimezone():%d %b %H:%M:%S %Z}\n",
            style="#8b95a7",
        )
