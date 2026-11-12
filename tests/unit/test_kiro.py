"""Synthetic fixtures derived from the public Zenn V3 example, never private sessions."""

import asyncio
import json
import os
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from dayboard.integrations.ai.kiro import KiroProvider, parse_observation
from dayboard.integrations.ai.reader import LocalReader, ScanCancelled
from dayboard.settings import KiroSettings

SESSION = "sess_5a834751-7391-4978-ab88-c7dfc626eb0a"
PATH = ("02731b6574de8a7e", SESSION, "messages.jsonl")
CANARY = "PRIVATE_BODY_TOOL_SECRET_CANARY"


def event(
    amount: Any = 0.04380804825870647,
    *,
    execution: str = "4fff48e2-88c5-4473-a261-504b4349a800",
    stamp: datetime | None = None,
) -> dict[str, Any]:
    return {
        "id": f"{execution}-usage",
        "timestamp": (stamp or datetime.now(UTC)).isoformat(),
        "payload": {
            "type": "usage_summary",
            "executionId": execution,
            "promptTurnSummaries": [{"unit": "credit", "usage": amount}],
            "content": CANARY,
            "toolArgs": {"secret": CANARY},
        },
        "title": CANARY,
        "workspacePaths": [CANARY],
    }


def write(root: Path, rows: list[dict[str, Any]], path: tuple[str, ...] = PATH) -> Path:
    target = root.joinpath(*path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return target


def values(snapshot: Any) -> dict[str, Any]:
    return {metric.id: metric.value for metric in snapshot.metrics}


def test_parser_credit_profile_is_immutable_and_minimized() -> None:
    observation = parse_observation(event(), PATH, 0, source_id="source-a")
    assert observation is not None
    assert observation.profile == "v3-credit"
    assert observation.credit is not None
    assert observation.credit.amount == Decimal("0.04380804825870647")
    assert observation.credit.model is None
    assert CANARY not in repr(observation)
    assert SESSION not in observation.credit.session_id
    with pytest.raises(AttributeError):
        observation.profile = "bad"  # type: ignore[misc]


@pytest.mark.parametrize("amount", [-1, float("nan"), float("inf"), True, "0.1", None, 10**10])
def test_invalid_credit_never_appears_as_zero(amount: Any) -> None:
    with pytest.raises(ValueError, match="Unsupported or invalid Kiro usage metadata"):
        parse_observation(event(amount), PATH, 0, source_id="test")


def test_rejects_unsupported_units_missing_identity_and_naive_stamp() -> None:
    for mutation in ("unit", "execution", "stamp"):
        row = event()
        if mutation == "unit":
            row["payload"]["promptTurnSummaries"][0]["unit"] = "tokens"
        elif mutation == "execution":
            row["payload"].pop("executionId")
        else:
            row["timestamp"] = "2026-01-01T12:00:00"
        with pytest.raises(ValueError):
            parse_observation(row, PATH, 0, source_id="test")


def test_source_and_session_are_isolated() -> None:
    a = parse_observation(event(), PATH, 0, source_id="a")
    b = parse_observation(event(), PATH, 0, source_id="b")
    c = parse_observation(event(), ("ffffffffffffffff", *PATH[1:]), 0, source_id="a")
    assert a and b and c and a.credit and b.credit and c.credit
    assert len({a.credit.id, b.credit.id, c.credit.id}) == 3


@pytest.mark.asyncio
async def test_provider_revisions_replace_with_original_timestamp(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    first = now - timedelta(minutes=10)
    target = write(tmp_path, [event(0.1, stamp=first), event(0.3, stamp=now)])
    before = target.stat()
    provider = KiroProvider(KiroSettings(True, str(tmp_path)))
    snapshot = await provider.collect()
    assert values(snapshot)["month-credits"] == "0.3"
    assert len(provider.ledger.records) == 1
    assert next(iter(provider.ledger.records.values())).timestamp == first
    assert values(snapshot)["month-events"] == 1
    assert values(snapshot)["tokens"] is None
    assert "2.31.1" in str(values(snapshot)["compatibility"])
    assert CANARY not in repr(snapshot)
    assert CANARY not in repr(provider.ledger.records)
    repeated = await provider.collect()
    assert values(repeated)["month-credits"] == "0.3"
    after = target.stat()
    assert (before.st_mode, before.st_mtime_ns, before.st_size) == (
        after.st_mode,
        after.st_mtime_ns,
        after.st_size,
    )
    provider.close()


@pytest.mark.asyncio
async def test_unknown_cli_and_empty_sources_are_unavailable_not_zero(tmp_path: Path) -> None:
    write(tmp_path, [{"context_usage": 10, "content": CANARY}], ("cli", "synthetic.jsonl"))
    provider = KiroProvider(KiroSettings(True, str(tmp_path)))
    snapshot = await provider.collect()
    assert values(snapshot)["month-credits"] is None
    assert "unsupported" in str(values(snapshot)["compatibility"]).lower()
    assert CANARY not in repr(snapshot)
    provider.close()


@pytest.mark.asyncio
async def test_session_only_v3_keeps_credits_unknown(tmp_path: Path) -> None:
    write(
        tmp_path,
        [
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "payload": {"type": "turn_start", "executionId": "abc"},
            }
        ],
    )
    provider = KiroProvider(KiroSettings(True, str(tmp_path)))
    snapshot = await provider.collect()
    assert values(snapshot)["month-credits"] is None
    assert values(snapshot)["observed-sessions"] == 1
    provider.close()


def test_disabled_provider_never_constructs_reader() -> None:
    with pytest.raises(ValueError, match="explicit enablement"):
        KiroProvider(KiroSettings())


@pytest.mark.asyncio
async def test_cancelled_worker_does_not_commit_or_publish(tmp_path: Path) -> None:
    entered = threading.Event()

    class SlowReader(LocalReader[Any]):
        def scan(self, stop: threading.Event) -> Any:
            entered.set()
            stop.wait(5)
            raise ScanCancelled()

    reader = SlowReader(str(tmp_path), parser=lambda data, path, offset: None)
    provider = KiroProvider(KiroSettings(True, str(tmp_path)), reader=reader)
    task = asyncio.create_task(provider.collect())
    await asyncio.to_thread(entered.wait, 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert reader.cursors == {}
    assert provider.ledger.records == {}


@pytest.mark.asyncio
async def test_symlink_and_malformed_inputs_do_not_leak(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.jsonl"
    secret.write_text(CANARY + "\n")
    root = tmp_path / "sessions"
    root.mkdir()
    (root / "link.jsonl").symlink_to(secret)
    write(root, [event("bad")])
    provider = KiroProvider(KiroSettings(True, str(root)))
    snapshot = await provider.collect()
    assert values(snapshot)["month-credits"] is None
    assert CANARY not in repr(snapshot)
    assert "skipped" in str(values(snapshot)["diagnostics"])
    provider.close()


@pytest.mark.asyncio
async def test_revision_of_expired_execution_does_not_become_current_usage(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    write(tmp_path, [event(0.1, stamp=now - timedelta(days=31)), event(0.4, stamp=now)])
    provider = KiroProvider(KiroSettings(True, str(tmp_path)))
    snapshot = await provider.collect()
    assert values(snapshot)["month-credits"] is None
    assert provider.ledger.records == {}
    provider.close()


@pytest.mark.asyncio
async def test_cancellation_after_read_replays_uncommitted_source(tmp_path: Path) -> None:
    from dayboard.integrations.ai.kiro import KiroObservation
    from dayboard.integrations.ai.reader import ScanBatch

    write(tmp_path, [event(0.25)])
    entered = threading.Event()

    class PausingReader(LocalReader[KiroObservation]):
        pause = True

        def scan(self, stop: threading.Event) -> ScanBatch[KiroObservation]:
            result = super().scan(stop)
            if self.pause:
                entered.set()
                stop.wait(5)
            return result

    reader = PausingReader(
        str(tmp_path),
        parser=lambda data, path, offset: parse_observation(data, path, offset, source_id="test"),
    )
    provider = KiroProvider(KiroSettings(True, str(tmp_path)), reader=reader)
    task = asyncio.create_task(provider.collect())
    assert await asyncio.to_thread(entered.wait, 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert reader.cursors == {}
    assert provider.ledger.records == {}
    reader.pause = False
    assert values(await provider.collect())["month-credits"] == "0.25"
    provider.close()


@pytest.mark.asyncio
async def test_older_revision_cannot_overwrite_latest_amount(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    target = write(tmp_path, [event(0.1, stamp=now), event(0.5, stamp=now + timedelta(seconds=1))])
    provider = KiroProvider(KiroSettings(True, str(tmp_path)))
    assert values(await provider.collect())["month-credits"] == "0.5"
    with target.open("a") as handle:
        handle.write(json.dumps(event(0.2, stamp=now)) + "\n")
    assert values(await provider.collect())["month-credits"] == "0.5"
    provider.close()


@pytest.mark.asyncio
async def test_same_execution_across_sessions_is_distinct(tmp_path: Path) -> None:
    write(tmp_path, [event(0.1)])
    write(tmp_path, [event(0.2)], ("ffffffffffffffff", *PATH[1:]))
    provider = KiroProvider(KiroSettings(True, str(tmp_path)))
    snapshot = values(await provider.collect())
    assert snapshot["month-credits"] == "0.3"
    assert snapshot["month-events"] == 2
    assert snapshot["observed-sessions"] == 2
    provider.close()


@pytest.mark.asyncio
async def test_verified_zero_is_distinct_from_missing_usage(tmp_path: Path) -> None:
    write(tmp_path, [event(0)])
    provider = KiroProvider(KiroSettings(True, str(tmp_path)))
    assert values(await provider.collect())["month-credits"] == "0"
    provider.close()


@pytest.mark.asyncio
async def test_partial_line_and_rotation_do_not_duplicate(tmp_path: Path) -> None:
    target = write(tmp_path, [event(0.1)])
    partial = json.dumps(event(0.4)).encode()
    with target.open("ab") as handle:
        handle.write(partial[: len(partial) // 2])
    provider = KiroProvider(KiroSettings(True, str(tmp_path)))
    assert values(await provider.collect())["month-credits"] == "0.1"
    with target.open("ab") as handle:
        handle.write(partial[len(partial) // 2 :] + b"\n")
    assert values(await provider.collect())["month-credits"] == "0.4"
    replacement = tmp_path / "replacement"
    replacement.write_text(json.dumps(event(0.4)) + "\n")
    replacement.replace(target)
    assert values(await provider.collect())["month-credits"] == "0.4"
    assert len(provider.ledger.records) == 1
    provider.close()


@pytest.mark.asyncio
async def test_recent_first_file_cap_is_disclosed(tmp_path: Path) -> None:
    old = write(tmp_path, [event(0.1)])
    recent = write(tmp_path, [event(0.7)], ("ffffffffffffffff", *PATH[1:]))
    os.utime(old, ns=(1_000_000_000, 1_000_000_000))
    os.utime(recent, ns=(2_000_000_000, 2_000_000_000))
    reader = LocalReader(
        str(tmp_path),
        parser=lambda data, path, offset: parse_observation(data, path, offset, source_id="test"),
        max_files=1,
    )
    provider = KiroProvider(KiroSettings(True, str(tmp_path)), reader=reader)
    snapshot = values(await provider.collect())
    assert snapshot["month-credits"] == "0.7"
    assert "file cap" in snapshot["diagnostics"]
    assert "not event time" in snapshot["index"]
    provider.close()


@pytest.mark.asyncio
async def test_provider_failure_is_safe_and_keeps_committed_state(tmp_path: Path) -> None:
    from dayboard.core.models import ErrorCode
    from dayboard.core.providers import ProviderUnavailable

    root = tmp_path / CANARY
    root.mkdir()
    write(root, [event(0.25)])
    provider = KiroProvider(KiroSettings(True, str(root)))
    await provider.collect()
    root.rename(tmp_path / "moved")
    with pytest.raises(ProviderUnavailable) as failure:
        await provider.collect()
    assert failure.value.code == ErrorCode.AI_SOURCE_UNAVAILABLE
    assert CANARY not in str(failure.value)
    assert len(provider.ledger.records) == 1
    provider.close()


@pytest.mark.asyncio
async def test_identity_capacity_is_bounded_and_disclosed(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("dayboard.integrations.ai.kiro.provider._LIMIT", 2)
    write(tmp_path, [event(0.1, execution=str(index)) for index in range(3)])
    provider = KiroProvider(KiroSettings(True, str(tmp_path)))
    snapshot = values(await provider.collect())
    assert len(provider.ledger.records) == 2
    assert snapshot["month-credits"] == "0.2"
    assert "metadata capacity" in snapshot["diagnostics"]
    provider.close()


@pytest.mark.asyncio
async def test_timezone_uses_original_event_day(tmp_path: Path, monkeypatch: Any) -> None:
    now = datetime(2026, 11, 12, 0, 30, tzinfo=UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return now if tz is None else now.astimezone(tz)

    monkeypatch.setattr("dayboard.integrations.ai.kiro.provider.datetime", Clock)
    write(
        tmp_path,
        [
            event(0.1, execution="first", stamp=now - timedelta(hours=12)),
            event(0.2, execution="second", stamp=now),
        ],
    )
    local = KiroProvider(KiroSettings(True, str(tmp_path), "America/Los_Angeles"))
    utc = KiroProvider(KiroSettings(True, str(tmp_path), "UTC"))
    assert values(await local.collect())["today-credits"] == "0.3"
    assert values(await utc.collect())["today-credits"] == "0.2"
    local.close()
    utc.close()


@pytest.mark.asyncio
async def test_headers_sqlite_auth_and_other_jsonl_are_not_read(tmp_path: Path) -> None:
    for filename in ("session.json", "data.sqlite3", "auth.json", "tool-results.jsonl"):
        (tmp_path / filename).write_text(CANARY)
    provider = KiroProvider(KiroSettings(True, str(tmp_path)))
    snapshot = values(await provider.collect())
    assert provider.reader.cursors == {}
    assert snapshot["month-credits"] is None
    assert snapshot["diagnostics"] == "None"
    assert CANARY not in repr(snapshot)
    provider.close()
