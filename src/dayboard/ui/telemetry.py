"""Telemetry presentation uses the bounded store history, never a second sampling loop."""

import math

from rich.text import Text

from dayboard.core.models import Snapshot, SourceStatus
from dayboard.security.redaction import safe_text
from dayboard.ui.card import SnapshotCard


def format_bytes(value: float | None) -> str:
    if value is None:
        return "Unknown"
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    for unit in units:
        if abs(value) < 1024 or unit == units[-1]:
            return f"{value:.3g} {unit}"
        value /= 1024
    return "Unknown"


def sparkline(
    values: list[float | None],
    *,
    ceiling: float | None = None,
    width: int = 32,
) -> str:
    values = values[-max(1, width) :]
    valid = [value for value in values if value is not None and math.isfinite(value)]
    scale = ceiling if ceiling is not None else max(valid, default=1)
    scale = max(1, scale)
    bars = "▁▂▃▄▅▆▇█"
    return "".join(
        "·"
        if value is None or not math.isfinite(value)
        else bars[max(0, min(7, int(7 * value / scale)))]
        for value in values
    )


def _number(snapshot: Snapshot, identifier: str) -> float | None:
    metric = next((metric for metric in snapshot.metrics if metric.id == identifier), None)
    return (
        float(metric.value)
        if metric is not None
        and isinstance(metric.value, (int, float))
        and not isinstance(metric.value, bool)
        else None
    )


def _percent(value: float | None) -> str:
    return "Unknown" if value is None else f"{value:.1f}%"


def _rate(value: float | None) -> str:
    return "Unknown" if value is None else format_bytes(value) + "/s"


class TelemetryCard(SnapshotCard):
    def show_telemetry(self, status: SourceStatus, history: tuple[Snapshot, ...]) -> None:
        snapshot = status.snapshot
        if snapshot is None:
            self.show_snapshot(
                None,
                state=status.state.value,
                error=status.error,
                loading=status.attempted_at is None,
            )
            return
        width = max(8, min(32, self.size.width - 6))
        text = Text(self.panel_title + "\n\n", style="bold #88c0d0")
        for identifier, label, value, ceiling in (
            ("cpu_percent", "CPU", _percent(_number(snapshot, "cpu_percent")), 100),
            ("memory_percent", "RAM", _percent(_number(snapshot, "memory_percent")), 100),
            ("network_receive", "DOWN", _rate(_number(snapshot, "network_receive")), None),
            ("network_send", "UP", _rate(_number(snapshot, "network_send")), None),
        ):
            text.append(f"{label}  {value}\n", style="#eceff4")
            series = [_number(reading, identifier) for reading in history[-width:]]
            text.append(sparkline(series, ceiling=ceiling, width=width) + "\n", style="#88c0d0")
            if identifier == "memory_percent":
                used = format_bytes(_number(snapshot, "memory_used"))
                total = format_bytes(_number(snapshot, "memory_total"))
                available = format_bytes(_number(snapshot, "memory_available"))
                text.append(f"{used} / {total} · {available} available\n", style="#8b95a7")
        text.append("Recent samples · network scale adapts\n", style="#8b95a7")
        text.append("Active non-loopback NICs; VPN may duplicate\n", style="#8b95a7")
        sampling = next((m.value for m in snapshot.metrics if m.id == "sample_status"), None)
        if sampling and sampling != "Ready":
            text.append(safe_text(str(sampling)) + "\n", style="#d7b875")
        text.append(
            status.state.value.upper()
            + " · local OS counters · "
            + snapshot.observed_at.astimezone().strftime("%H:%M:%S"),
            style="#d7b875",
        )
        if status.error is not None:
            text.append("\n" + status.error.value, style="#d7b875")
        self.update(text)
