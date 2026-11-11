"""Read-only, occurrence-based calendar data; event contents stay in memory."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from dayboard.core.models import Coverage, ErrorCode, Metric, ProviderKey, Quality, Snapshot
from dayboard.core.providers import Capability, ProviderSpec, ProviderUnavailable
from dayboard.security.redaction import safe_text
from dayboard.settings import CalendarSettings


class CalendarReader(Protocol):
    async def call(self, operation: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class CalendarEvent:
    id: str
    title: str
    start: datetime
    end: datetime
    all_day: bool
    calendar_id: str
    calendar_name: str


def _date(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("Invalid calendar date.")
    result = datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise ValueError("Calendar dates must be timezone-aware.")
    return result


def parse_events(
    data: Any,
    now: datetime,
    settings: CalendarSettings,
) -> tuple[CalendarEvent, ...]:
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int:
        raise ValueError("Invalid calendar bridge response.")
    if data["schema_version"] != 1:
        raise ValueError("Unsupported calendar bridge version.")
    status = data.get("status")
    if status == "not_determined":
        raise ProviderUnavailable(ErrorCode.PERMISSION_REQUIRED)
    if status in ("denied", "restricted"):
        raise ProviderUnavailable(ErrorCode.PERMISSION_DENIED)
    if status != "authorized":
        raise ProviderUnavailable(ErrorCode.UNAVAILABLE)
    if data.get("selection_missing"):
        raise ProviderUnavailable(ErrorCode.UNAVAILABLE)
    raw = data.get("events")
    if not isinstance(raw, list) or len(raw) > 100:
        raise ValueError("Invalid calendar event list.")
    result: dict[tuple[str, str, datetime], CalendarEvent] = {}
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Invalid calendar event.")
        try:
            for field in ("id", "title", "calendar_id", "calendar_name"):
                if not isinstance(item[field], str) or len(item[field]) > 4096:
                    raise ValueError("Invalid calendar event metadata.")
            start, end = _date(item["start"]), _date(item["end"])
            if type(item["all_day"]) is not bool or end <= start:
                raise ValueError("Invalid calendar event range or all-day flag.")
            if end <= now or start >= now + timedelta(days=7):
                continue
            if settings.calendar_ids and item["calendar_id"] not in settings.calendar_ids:
                continue
            event = CalendarEvent(
                item["id"],
                safe_text(item["title"])[:400] or "Untitled event",
                start,
                end,
                item["all_day"],
                item["calendar_id"],
                safe_text(item["calendar_name"])[:100],
            )
            result[event.calendar_id, event.id, event.start] = event
        except KeyError:
            raise ValueError("Missing calendar event metadata.") from None
    return tuple(sorted(result.values(), key=lambda event: (event.start, event.title)))


class CalendarProvider:
    def __init__(
        self,
        settings: CalendarSettings,
        bridge: CalendarReader,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not settings.enabled:
            raise ValueError("Calendar must be explicitly enabled.")
        self.settings, self.bridge, self.clock = settings, bridge, clock
        self.spec = ProviderSpec(
            ProviderKey("apple-calendar"),
            "macOS Calendar",
            (Capability.CALENDAR,),
            interval_seconds=60,
            timeout_seconds=12,
        )

    async def collect(self) -> Snapshot:
        data = await self.bridge.call("events")
        now = self.clock()
        events = parse_events(data, now, self.settings)
        metrics = [
            Metric(
                "window",
                "Next 7 days",
                "No upcoming events"
                if not events
                else f"{len(events)} occurrences"
                + (" · truncated" if data.get("truncated") else ""),
            )
        ]
        for index, event in enumerate(events[:6]):
            label = event.start.astimezone().strftime("%a %d %b")
            label += " · all day" if event.all_day else event.start.astimezone().strftime(" %H:%M")
            metrics.append(
                Metric(
                    f"event-{index}",
                    label,
                    f"{event.title} · {event.calendar_name}",
                )
            )
        return Snapshot(
            self.spec.key,
            now,
            now,
            tuple(metrics),
            Quality.REPORTED,
            Coverage.LOCAL,
        )
