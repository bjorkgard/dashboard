import asyncio
import os
import sys
from datetime import UTC, datetime

import pytest

from dayboard.core.models import ErrorCode
from dayboard.core.providers import ProviderUnavailable
from dayboard.integrations.calendar import CalendarProvider, parse_events
from dayboard.integrations.native import CalendarBridge, run_bounded
from dayboard.settings import CalendarSettings

NOW = datetime(2026, 11, 11, 12, tzinfo=UTC)


def event(start="2026-11-11T13:00:00Z", end="2026-11-11T14:00:00Z", **extra):
    return {
        "id": "series-id",
        "title": "Synthetic meeting",
        "start": start,
        "end": end,
        "all_day": False,
        "calendar_id": "work",
        "calendar_name": "Work",
        **extra,
    }


def test_recurring_occurrences_are_distinct_and_sort_with_source_labels():
    data = {
        "schema_version": 1,
        "status": "authorized",
        "events": [
            event("2026-11-12T13:00:00Z", "2026-11-12T14:00:00Z"),
            event(),
            event(),  # duplicate same occurrence
            event("2026-11-11T00:00:00Z", "2026-11-12T00:00:00Z", all_day=True, id="all-day"),
            event("2026-11-10T13:00:00Z", "2026-11-10T14:00:00Z", id="past"),
        ],
    }
    events = parse_events(data, NOW, CalendarSettings(True))
    assert len(events) == 3
    assert events[0].all_day
    assert events[1].start < events[2].start
    assert events[1].calendar_name == "Work"


def test_calendar_filter_does_not_mix_sources():
    data = {
        "schema_version": 1,
        "status": "authorized",
        "events": [
            event(),
            event(calendar_id="personal"),
        ],
    }
    assert len(parse_events(data, NOW, CalendarSettings(True, ("work",)))) == 1


@pytest.mark.parametrize(
    "state,code",
    [
        ("not_determined", ErrorCode.PERMISSION_REQUIRED),
        ("denied", ErrorCode.PERMISSION_DENIED),
        ("restricted", ErrorCode.PERMISSION_DENIED),
    ],
)
def test_permission_states_are_safe_and_never_prompt_from_parser(state, code):
    with pytest.raises(ProviderUnavailable) as error:
        parse_events({"schema_version": 1, "status": state}, NOW, CalendarSettings(True))
    assert error.value.code == code


@pytest.mark.parametrize(
    "events",
    [
        [event(start="2026-11-11T13:00:00")],
        [event(end="2026-11-11T12:00:00Z")],
        [event(all_day="yes")],
        [event(title="x" * 5000)],
        [event()] * 101,
    ],
)
def test_invalid_dates_flags_or_oversized_calendar_payloads_are_rejected(events):
    with pytest.raises(ValueError):
        parse_events(
            {"schema_version": 1, "status": "authorized", "events": events},
            NOW,
            CalendarSettings(True),
        )


async def test_provider_only_requests_events_not_authorization():
    class Reader:
        async def call(self, operation):
            assert operation == "events"
            return {"schema_version": 1, "status": "authorized", "events": [event()]}

    reading = await CalendarProvider(CalendarSettings(True), Reader(), clock=lambda: NOW).collect()
    assert any("Synthetic meeting" in str(m.value) for m in reading.metrics)
    assert reading.key.provider == "apple-calendar"


async def test_bridge_subprocess_protocol_and_operation_allowlist(tmp_path):
    tool = tmp_path / "bridge"
    tool.write_text(
        f"#!{sys.executable}\nimport json\n"
        'print(json.dumps({"schema_version":1,"status":"not_determined"}))\n'
    )
    tool.chmod(0o700)
    bridge = CalendarBridge(tool)
    assert (await bridge.call("status"))["status"] == "not_determined"
    with pytest.raises(ValueError):
        await bridge.call("delete")


async def test_bounded_child_timeout_kills_the_owned_process(tmp_path):
    pidfile = tmp_path / "pid"
    script = f"import os,time; open({str(pidfile)!r},'w').write(str(os.getpid())); time.sleep(20)"
    with pytest.raises(TimeoutError):
        await run_bounded([sys.executable, "-c", script], timeout=0.3)
    pid = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_child_cancellation_and_output_limit(tmp_path):
    with pytest.raises(ValueError):
        await run_bounded([sys.executable, "-c", "print('x'*300000)"], timeout=2)
    pidfile = tmp_path / "pid"
    script = f"import os,time; open({str(pidfile)!r},'w').write(str(os.getpid())); time.sleep(20)"
    task = asyncio.create_task(run_bounded([sys.executable, "-c", script], timeout=10))
    for _ in range(100):
        if pidfile.exists():
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)


async def test_bridge_passes_source_filter_before_native_event_limit(tmp_path):
    tool = tmp_path / "bridge"
    tool.write_text(
        f"#!{sys.executable}\nimport json,sys\n"
        'print(json.dumps({"schema_version":1,"status":"authorized","ids":sys.argv[2:]}))\n'
    )
    tool.chmod(0o700)
    bridge = CalendarBridge(tool, calendar_ids=("work calendar",))
    result = await bridge.call("events")
    assert result["ids"] == ["work calendar"]
