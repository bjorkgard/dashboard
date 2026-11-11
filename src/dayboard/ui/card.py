from rich.text import Text
from textual.widgets import Static

from dayboard.core.models import Snapshot
from dayboard.security.redaction import safe_text


class SnapshotCard(Static):
    def __init__(self, panel: str, title: str) -> None:
        super().__init__(id=f"card-{panel}", classes="card", markup=False)
        self.panel_title = title
        self.show_snapshot(None)

    def show_snapshot(self, snapshot: Snapshot | None) -> None:
        content = Text()
        content.append(self.panel_title + "\n\n", style="bold #88c0d0")
        if snapshot is None:
            content.append("Not connected\n", style="#d8dee9")
            content.append("Live integration arrives in a later milestone.", style="#8b95a7")
        else:
            for metric in snapshot.metrics:
                value = "Unknown" if metric.value is None else str(metric.value)
                content.append(f"{safe_text(metric.label)}  ", style="#8b95a7")
                content.append(f"{safe_text(value)} {safe_text(metric.unit)}\n", style="#eceff4")
            provenance = "SYNTHETIC" if snapshot.synthetic else snapshot.quality.value.upper()
            content.append(
                f"\n{provenance} · observed " + snapshot.observed_at.strftime("%H:%M:%S %Z"),
                style="#d7b875",
            )
        self.update(content)
