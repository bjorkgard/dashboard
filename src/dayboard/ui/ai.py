"""Bounded metadata presentations; no filesystem access from widgets."""

from rich.text import Text

from dayboard.core.models import Metric, Snapshot, SourceStatus
from dayboard.security.redaction import safe_text
from dayboard.ui.card import SnapshotCard
from dayboard.ui.details import DetailCard

AI_PANELS = (
    ("claude-local", "ai-detail", "Claude Code", "demo-ai"),
    ("codex-local", "ai-codex", "Codex", "demo-codex"),
    ("codex-limits", "ai-codex-limits", "Codex limits", "demo-codex-limits"),
    ("kiro-local", "ai-kiro", "Kiro", "demo-kiro"),
    ("warp-runs", "ai-warp", "Warp / Oz", "demo-warp"),
)


class AICard(SnapshotCard):
    def show_sources(self, sources: list[tuple[str, SourceStatus, bool]]) -> None:
        content = Text(self.panel_title + "\n\n", style="bold #88c0d0")
        for name, status, enabled in sources:
            state = (
                "Not connected"
                if not enabled
                else "Loading"
                if status.snapshot is None and status.error is None
                else status.state.value.upper()
            )
            content.append(f"{name} · {state}", style="#d8dee9")
            if status.snapshot is not None:
                if status.snapshot.synthetic:
                    content.append(" · SYNTHETIC", style="#d7b875")
                metrics = {m.id: m.value for m in status.snapshot.metrics}
                if "Indexing" in str(metrics.get("index", "")):
                    content.append(" · indexing", style="#d7b875")
                if metrics.get("diagnostics") not in (None, "None", ""):
                    content.append(" · partial (see AI)", style="#d7b875")
                if str(metrics.get("compatibility", "")).startswith("Unsupported"):
                    content.append(" · unsupported profile", style="#d7b875")
                for identifier, label in (
                    ("today-output", "output"),
                    ("today-credits", "Kiro credits"),
                ):
                    if identifier in metrics:
                        value = metrics[identifier]
                        content.append(
                            f" · {label}: "
                            + (safe_text(str(value)) if value is not None else "Unknown")
                        )
            if status.error is not None:
                content.append("\n" + status.error.value, style="#d7b875")
            content.append("\n")
        content.append(
            "\nPer-source coverage only; no combined token/credit/account total.", style="#8b95a7"
        )
        self.update(content)

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

    def visible_metrics(self, snapshot: Snapshot) -> tuple[Metric, ...]:
        return snapshot.metrics
