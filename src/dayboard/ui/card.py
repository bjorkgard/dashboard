from rich.text import Text
from textual.widgets import Static

from dayboard.core.models import ErrorCode, Metric, Snapshot
from dayboard.security.redaction import safe_text


class SnapshotCard(Static):
    def __init__(self, panel: str, title: str) -> None:
        super().__init__(id=f"card-{panel}", classes="card", markup=False)
        self.panel = panel
        self.panel_title = title
        self.show_snapshot(None)

    def visible_metrics(self, snapshot: Snapshot) -> tuple[Metric, ...]:
        limits = {"event-": 6} if self.panel == "schedule" else {}
        if self.panel == "inbox":
            limits = {"mail-": 5}
        if self.panel == "github":
            limits = {"pr-": 5, "commit-": 5}
        counts = dict.fromkeys(limits, 0)
        result = []
        for metric in snapshot.metrics:
            if metric.id == "detail-scope":
                continue
            prefix = next((prefix for prefix in limits if metric.id.startswith(prefix)), None)
            if prefix is not None:
                counts[prefix] += 1
                if counts[prefix] > limits[prefix]:
                    continue
            result.append(metric)
        return tuple(result)

    def append_metrics(self, content: Text, snapshot: Snapshot) -> None:
        for metric in self.visible_metrics(snapshot):
            value = "Unknown" if metric.value is None else str(metric.value)
            content.append(f"{safe_text(metric.label)}  ", style="#8b95a7")
            content.append(f"{safe_text(value)} {safe_text(metric.unit)}\n", style="#eceff4")

    def show_snapshot(
        self,
        snapshot: Snapshot | None,
        *,
        state: str = "",
        error: ErrorCode | None = None,
        loading: bool = False,
    ) -> None:
        content = Text()
        content.append(self.panel_title + "\n\n", style="bold #88c0d0")
        if snapshot is None:
            if error is not None:
                content.append(error.value, style="#d7b875")
            elif loading:
                content.append("Loading live source…", style="#8b95a7")
            else:
                content.append("Not connected\n", style="#d8dee9")
                content.append("Open Settings [s] to configure available sources.", style="#8b95a7")
        else:
            provenance = "SYNTHETIC" if snapshot.synthetic else snapshot.quality.value.upper()
            content.append(
                f"{state.upper()} {provenance} · observed "
                + snapshot.observed_at.astimezone().strftime("%d %b %H:%M:%S %Z")
                + "\n",
                style="#d7b875",
            )
            if error is not None:
                content.append(f"{error.value}\n", style="#d7b875")
            content.append("\n")
            self.append_metrics(content, snapshot)
        self.update(content)
