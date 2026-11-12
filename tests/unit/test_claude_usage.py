"""Synthetic profile: message.usage JSONL; public ccusage issues #888/#901.

Not vendor schema guarantees. No real conversation or credentials are fixtures.
"""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from dayboard.core.ai_usage import UsageLedger
from dayboard.integrations.ai.claude import InvalidUsage, parse_usage

NOW = datetime(2026, 11, 12, 12, tzinfo=UTC)


def record(identifier="msg-one", output=2, **changes):
    value = {
        "type": "assistant",
        "timestamp": NOW.isoformat(),
        "sessionId": "session-one",
        "version": "2.1.96",
        "requestId": "request-one",
        "message": {
            "id": identifier,
            "model": "claude-example",
            "content": [{"text": "PRIVATE_PROMPT_CANARY", "input": "PRIVATE_TOOL_CANARY"}],
            "usage": {
                "input_tokens": 10,
                "output_tokens": output,
                "cache_creation_input_tokens": 20,
                "cache_read_input_tokens": 30,
            },
        },
        "cwd": "/PRIVATE_PATH_CANARY",
        **changes,
    }
    return value


def parsed(**changes):
    return parse_usage(record(**changes))


def test_parser_only_retains_allowlisted_metadata():
    usage = parsed()
    assert usage.tokens == (10, 2, 20, 30)
    assert usage.model == "claude-example"
    assert "CANARY" not in repr(usage)
    assert parsed(requestId=None).message_id == usage.message_id
    assert parse_usage({"type": "user", "message": "PRIVATE_PROMPT_CANARY"}) is None
    assert parse_usage({"type": "future-event"}) is None


@pytest.mark.parametrize("bad", [True, -1, 2.5, float("nan"), "2", 10**20])
def test_invalid_counters_fail_without_raw_input(bad):
    value = record()
    value["message"]["usage"]["output_tokens"] = bad
    with pytest.raises(InvalidUsage) as error:
        parse_usage(value)
    assert "CANARY" not in str(error.value)


def test_missing_fields_and_cache_subdivisions_are_not_double_counted():
    value = record()
    del value["message"]["usage"]["cache_read_input_tokens"]
    value["message"]["usage"]["cache_creation"] = {
        "ephemeral_5m_input_tokens": 5,
        "ephemeral_1h_input_tokens": 15,
    }
    usage = parse_usage(value)
    assert usage.tokens == (10, 2, 20, None)
    assert usage.cache_buckets == (5, 15)
    value["message"]["usage"]["cache_creation"]["ephemeral_1h_input_tokens"] = 16
    with pytest.raises(InvalidUsage):
        parse_usage(value)


def test_unrecognized_usage_and_missing_identity_do_not_become_zero():
    for field in ("id", "model"):
        value = record()
        del value["message"][field]
        with pytest.raises(InvalidUsage):
            parse_usage(value)
    with pytest.raises(InvalidUsage):
        parse_usage({"type": "new-assistant", "usage": {"output_tokens": 2}})
    value = record(timestamp="2026-11-12T10:00:00")
    with pytest.raises(InvalidUsage):
        parse_usage(value)


def test_revisions_replay_forks_and_missing_request_ids_are_idempotent():
    ledger = UsageLedger()
    first = parsed(requestId=None)
    assert ledger.add(first, NOW)
    ledger.add(replace(first, session_id="fork-session"), NOW)
    ledger.add(replace(first, request_id="request-one", tokens=(10, 50, 20, 30)), NOW)
    ledger.add(first, NOW)  # replay of a historical partial must not regress or conflict
    summary = ledger.summary(NOW, 1)
    assert summary.tokens == (10, 50, 20, 30)
    assert summary.requests == 1
    assert summary.sessions == 1
    assert not ledger.conflicts
    assert ledger.add(parsed(identifier="msg-two"), NOW)
    assert ledger.summary(NOW, 1).requests == 2


def test_conflicting_revisions_are_quarantined_not_componentwise_maximized():
    ledger = UsageLedger()
    first = parsed()
    ledger.add(first, NOW)
    ledger.add(replace(first, tokens=(9, 50, 20, 30)), NOW)
    assert ledger.conflicts == 1
    assert ledger.summary(NOW, 1).requests == 0
    ledger.add(first, NOW)
    assert ledger.summary(NOW, 1).requests == 0


def test_unknown_categories_remain_unknown_and_can_be_completed():
    ledger = UsageLedger()
    first = parsed()
    ledger.add(replace(first, tokens=(10, 2, None, 30)), NOW)
    assert ledger.summary(NOW, 1).tokens[2] is None
    ledger.add(first, NOW)
    assert ledger.summary(NOW, 1).tokens == first.tokens


def test_timezone_windows_revision_midnight_retention_and_capacity():
    ledger = UsageLedger(timezone="America/New_York", limit=2)
    first = replace(parsed(), timestamp=datetime(2026, 11, 12, 4, 59, tzinfo=UTC))
    ledger.add(first, NOW)
    ledger.add(replace(first, timestamp=NOW, tokens=(10, 30, 20, 30)), NOW)
    assert ledger.summary(NOW, 1).requests == 0
    assert ledger.summary(NOW, 7).tokens[1] == 30
    ledger.add(parsed(identifier="msg-two"), NOW)
    assert not ledger.add(parsed(identifier="msg-three"), NOW)
    assert ledger.capped
    later = datetime(2026, 12, 20, tzinfo=UTC)
    ledger.prune(later)
    assert ledger.summary(later, 30).requests == 0


def test_dst_fall_back_hours_are_one_calendar_day_not_two():
    ledger = UsageLedger(timezone="America/New_York")
    now = datetime(2026, 11, 1, 12, tzinfo=UTC)
    first = replace(parsed(), timestamp=datetime(2026, 11, 1, 5, 30, tzinfo=UTC))
    second = replace(
        first, message_id="msg-two", timestamp=datetime(2026, 11, 1, 6, 30, tzinfo=UTC)
    )
    ledger.add(first, now)
    ledger.add(second, now)
    assert ledger.summary(now, 1).requests == 2
