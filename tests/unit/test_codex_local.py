import asyncio
import json
import threading
from datetime import UTC, datetime

import pytest

from dayboard.integrations.ai.codex import CodexProvider
from dayboard.integrations.ai.codex.parser import parse_rollout
from dayboard.settings import CodexSettings

NOW = datetime(2026, 11, 12, 12, tzinfo=UTC)
SID = "71851ec1-bef7-4f75-a284-34470090d309"


def event(kind, payload, stamp="2026-11-12T11:00:00Z"):
    return {"timestamp": stamp, "type": kind, "payload": payload}


def counts(i, c=0, o=0, r=0):
    return {
        "input_tokens": i,
        "cached_input_tokens": c,
        "output_tokens": o,
        "reasoning_output_tokens": r,
        "total_tokens": i + o,
    }


def checkpoint(total, stamp="2026-11-12T11:00:00Z", last=None):
    return event(
        "event_msg",
        {"type": "token_count", "info": {"total_token_usage": total, "last_token_usage": last}},
        stamp,
    )


def write_source(tmp_path, rows, name="rollout-test.jsonl"):
    folder = tmp_path / "sessions"
    folder.mkdir(exist_ok=True)
    path = folder / name
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


def metric(snapshot, key):
    return next(m.value for m in snapshot.metrics if m.id == key)


def metadata():
    return [
        event("session_meta", {"id": SID, "cwd": "PRIVATE_PATH_CANARY"}),
        event("turn_context", {"model": "gpt-5", "instructions": "SECRET_CANARY"}),
    ]


def provider(tmp_path):
    return CodexProvider(CodexSettings(True, str(tmp_path)), clock=lambda: NOW)


def test_parser_is_immutable_allowlist():
    assert parse_rollout(event("response_item", {"text": "SECRET_CANARY"}), ("x",), 0) is None
    result = parse_rollout(checkpoint(counts(100, 30, 40, 20)), ("x",), 42)
    assert result is not None
    assert result.counts == (100, 30, 40, 20)
    assert "SECRET_CANARY" not in repr(parse_rollout(metadata()[1], ("x",), 0))
    with pytest.raises((AttributeError, TypeError)):
        result.model = "bad"


@pytest.mark.parametrize("value", [-1, True, 1.2, 10**13, None])
def test_invalid_counters_are_not_supported(value):
    data = counts(10)
    data["input_tokens"] = value
    assert parse_rollout(checkpoint(data), ("x",), 0) is None


@pytest.mark.asyncio
async def test_zero_baseline_deltas_cached_and_reasoning_subsets(tmp_path):
    write_source(
        tmp_path,
        metadata() + [checkpoint(counts(0)), checkpoint(counts(100, 30, 40, 20))],
    )
    p = provider(tmp_path)
    snapshot = await p.collect()
    assert metric(snapshot, "today-input") == 70
    assert metric(snapshot, "today-cache-read") == 30
    assert metric(snapshot, "today-output") == 40
    assert metric(snapshot, "today-cache-write") is None
    assert metric(await p.collect(), "today-input") == 70
    assert "SECRET_CANARY" not in repr(snapshot) + repr(p.ledger.records)
    assert "PRIVATE_PATH_CANARY" not in repr(snapshot) + repr(p.ledger.records)


@pytest.mark.asyncio
async def test_missing_baseline_reset_model_and_midnight(tmp_path):
    rows = metadata() + [
        checkpoint(counts(1000), "2026-11-11T23:50:00Z"),
        checkpoint(counts(1100, 20, 40), "2026-11-12T00:01:00Z"),
        checkpoint(counts(10), "2026-11-12T01:00:00Z"),
        event("turn_context", {"model": "gpt-5-mini"}),
        checkpoint(counts(30, 0, 5), "2026-11-12T02:00:00Z"),
    ]
    write_source(tmp_path, rows)
    p = provider(tmp_path)
    snapshot = await p.collect()
    assert metric(snapshot, "today-input") == 100
    assert metric(snapshot, "today-output") == 45
    assert {r.model for r in p.ledger.records.values()} == {"gpt-5", "gpt-5-mini"}
    assert "baseline" in metric(snapshot, "diagnostics")


@pytest.mark.asyncio
async def test_first_validated_single_call_can_establish_new_session(tmp_path):
    write_source(tmp_path, metadata() + [checkpoint(counts(20, 5, 8), last=counts(20, 5, 8))])
    snapshot = await provider(tmp_path).collect()
    assert metric(snapshot, "today-input") == 15


@pytest.mark.asyncio
async def test_duplicates_archive_copies_and_out_of_order_files(tmp_path):
    rows = metadata() + [checkpoint(counts(0)), checkpoint(counts(50, 10, 20))]
    write_source(tmp_path, rows)
    archive = tmp_path / "archived_sessions"
    archive.mkdir()
    (archive / "rollout-copy.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    snapshot = await provider(tmp_path).collect()
    assert metric(snapshot, "today-input") == 40
    assert metric(snapshot, "today-output") == 20


@pytest.mark.asyncio
async def test_ignored_roots_non_rollouts_and_source_unchanged(tmp_path):
    path = write_source(tmp_path, metadata() + [checkpoint(counts(0)), checkpoint(counts(5))])
    before = (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns)
    (tmp_path / "auth.json").write_text("SECRET_CANARY")
    write_source(tmp_path, [checkpoint(counts(10**6))], name="not-a-rollout.jsonl")
    snapshot = await provider(tmp_path).collect()
    assert metric(snapshot, "today-input") == 5
    assert before == (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns)


@pytest.mark.asyncio
async def test_cancelled_scan_does_not_commit_state(tmp_path):
    from dayboard.integrations.ai.reader import LocalReader

    entered, released = threading.Event(), threading.Event()

    class SlowReader(LocalReader):
        def scan(self, stop):
            entered.set()
            released.wait(3)
            return super().scan(stop)

    write_source(tmp_path, metadata() + [checkpoint(counts(0)), checkpoint(counts(5))])
    reader = SlowReader(str(tmp_path), parser=parse_rollout, subtree=("sessions",))
    p = CodexProvider(CodexSettings(True, str(tmp_path)), readers=(reader,), clock=lambda: NOW)
    task = asyncio.create_task(p.collect())
    await asyncio.to_thread(entered.wait, 3)
    task.cancel()
    released.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not p.ledger.records
    assert not reader.cursors
    assert metric(await p.collect(), "today-input") == 5


def test_sources_are_independent_explicit_flags(tmp_path):
    with pytest.raises(ValueError):
        CodexProvider(CodexSettings(False, str(tmp_path), limits_enabled=True))


@pytest.mark.asyncio
async def test_true_zero_baseline_is_known_zero_but_empty_source_is_unknown(tmp_path):
    write_source(tmp_path, metadata() + [checkpoint(counts(0))])
    snapshot = await provider(tmp_path).collect()
    assert metric(snapshot, "today-input") == 0
    assert metric(snapshot, "today-cache-write") is None


@pytest.mark.asyncio
async def test_partial_line_resumes_without_loss_or_lifetime_attribution(tmp_path):
    from dayboard.integrations.ai.reader import LocalReader

    path = write_source(tmp_path, metadata() + [checkpoint(counts(1000))])
    with path.open("a") as handle:
        handle.write(json.dumps(checkpoint(counts(1010, 0, 2)))[:-1])
    reader = LocalReader(str(tmp_path), parser=parse_rollout, subtree=("sessions",))
    p = CodexProvider(CodexSettings(True, str(tmp_path)), readers=(reader,), clock=lambda: NOW)
    assert metric(await p.collect(), "today-input") is None
    with path.open("a") as handle:
        handle.write("}\n")
    assert metric(await p.collect(), "today-input") == 10
    assert metric(await p.collect(), "today-output") == 2


@pytest.mark.asyncio
async def test_inconsistent_delta_subsets_cannot_create_negative_tokens(tmp_path):
    write_source(
        tmp_path,
        metadata()
        + [
            checkpoint(counts(100, 0, 50, 0)),
            checkpoint(counts(110, 50, 55, 30)),
            checkpoint(counts(120, 50, 60, 30)),
        ],
    )
    snapshot = await provider(tmp_path).collect()
    assert metric(snapshot, "today-input") == 10
    assert metric(snapshot, "today-output") == 5
    assert "inconsistent" in metric(snapshot, "diagnostics")


@pytest.mark.asyncio
async def test_symlinked_subtree_is_not_followed(tmp_path):
    elsewhere = tmp_path / "private"
    elsewhere.mkdir()
    (tmp_path / "sessions").symlink_to(elsewhere, target_is_directory=True)
    from dayboard.core.providers import ProviderUnavailable

    with pytest.raises(ProviderUnavailable):
        await provider(tmp_path).collect()


def test_unknown_model_metadata_clears_attribution_and_never_retains_text():
    value = parse_rollout(
        event("turn_context", {"model": "gpt-SECRET_CANARY"}), ("rollout-test.jsonl",), 0
    )
    assert value is not None
    assert value.model == "unknown"
    assert "SECRET_CANARY" not in repr(value)


@pytest.mark.asyncio
async def test_invalid_session_metadata_clears_previous_session_identity(tmp_path):
    write_source(
        tmp_path,
        metadata()
        + [
            checkpoint(counts(0)),
            event("session_meta", {"id": "SECRET_CANARY"}),
            checkpoint(counts(100)),
        ],
    )
    p = provider(tmp_path)
    snapshot = await p.collect()
    assert metric(snapshot, "today-input") == 0
    assert all(record.tokens[0] == 0 for record in p.ledger.records.values())
    assert "SECRET_CANARY" not in repr(p._contexts)


@pytest.mark.asyncio
async def test_byte_budget_backfill_resumes_and_later_baseline_reconciles(tmp_path):
    from dayboard.integrations.ai.reader import LocalReader

    rows = metadata() + [checkpoint(counts(i * 10, i * 2, i * 3)) for i in range(30)]
    write_source(tmp_path, rows)
    reader = LocalReader(
        str(tmp_path),
        parser=parse_rollout,
        subtree=("sessions",),
        byte_budget=1024,
        max_record=512,
    )
    p = CodexProvider(CodexSettings(True, str(tmp_path)), readers=(reader,), clock=lambda: NOW)
    for _ in range(60):
        snapshot = await p.collect()
        if metric(snapshot, "today-input") == 232:
            break
    assert metric(snapshot, "today-input") == 232
    assert metric(snapshot, "today-output") == 87
    assert metric(await p.collect(), "today-input") == 232


@pytest.mark.asyncio
async def test_recent_first_file_cap_discloses_partial_source(tmp_path):
    import os

    from dayboard.integrations.ai.reader import LocalReader

    for index in range(4):
        rows = [
            event("session_meta", {"id": f"71851ec1-bef7-4f75-a284-34470090d30{index}"}),
            checkpoint(counts(0)),
            checkpoint(counts(10 * (index + 1))),
        ]
        path = write_source(tmp_path, rows, f"rollout-{index}.jsonl")
        os.utime(path, (index + 1, index + 1))
    reader = LocalReader(
        str(tmp_path),
        parser=parse_rollout,
        subtree=("sessions",),
        max_files=1,
    )
    p = CodexProvider(CodexSettings(True, str(tmp_path)), readers=(reader,), clock=lambda: NOW)
    snapshot = await p.collect()
    assert metric(snapshot, "today-input") == 40
    assert "file cap" in metric(snapshot, "diagnostics")
