import asyncio
import copy
import json
import threading
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from dayboard.core.models import Coverage, ErrorCode
from dayboard.core.providers import (
    Capability,
    ProviderRegistry,
    ProviderUnavailable,
    RateLimited,
)
from dayboard.core.scheduler import Scheduler
from dayboard.core.store import SnapshotStore
from dayboard.integrations.ai.warp import WarpProvider, parse_runs
from dayboard.settings import WarpSettings

NOW = datetime(2026, 11, 12, 16, tzinfo=UTC)
SOURCE_TIME = datetime(2026, 11, 12, 15, 57, tzinfo=UTC)
SETTINGS = WarpSettings(True, "test-key", "team-one")
CANARY = "PRIVATE_BODY_TOOL_ARGS_SECRET_CANARY"


def payload():
    return {
        "as_of": SOURCE_TIME.isoformat(),
        "team_uid": "team-one",
        "page_info": {"has_next_page": True, "next_cursor": CANARY},
        "runs": [
            {
                "run_id": "run-one",
                "state": "SUCCEEDED",
                "created_at": SOURCE_TIME.isoformat(),
                "updated_at": SOURCE_TIME.isoformat(),
                "conversation_id": "conversation-one",
                "root_run_id": "run-one",
                "request_usage": {
                    "total_tokens": 100,
                    "inference_cost": 1.25,
                    "compute_cost": 0,
                    "platform_cost": 0.125,
                    "inference_cost_usd": 0.0125,
                    "compute_cost_usd": 0,
                    "platform_cost_usd": 0.00125,
                    "usage_by_category": {CANARY: CANARY},
                    "model_token_usage": [CANARY],
                },
                "prompt": CANARY,
                "submitted_prompt": CANARY,
                "title": CANARY,
                "status_message": {"message": CANARY},
                "artifacts": [CANARY],
                "agent_config": {"metadata": CANARY},
                "metadata": {CANARY: CANARY},
            }
        ],
    }


def provider(response=None, *, credential=lambda: "synthetic-key", handler=None, settings=SETTINGS):
    return WarpProvider(
        settings,
        credential,
        httpx.MockTransport(handler or (lambda request: httpx.Response(200, json=response))),
        clock=lambda: NOW,
    )


def test_parser_retains_only_frozen_allowlisted_metadata():
    report = parse_runs(payload(), team_uid="team-one")
    assert report.as_of == SOURCE_TIME
    assert report.has_next_page is True
    assert report.team_uid == "team-one"
    assert report.runs[0].usage.total_tokens == 100
    assert report.runs[0].usage.inference_cost == Decimal("1.25")
    assert report.runs[0].usage.platform_cost_usd == Decimal("0.00125")
    assert CANARY not in repr(report)
    with pytest.raises(FrozenInstanceError):
        report.runs[0].state = "FAILED"


def test_absent_usage_stays_unknown_and_zero_is_recorded_zero():
    data = payload()
    data["runs"][0].pop("request_usage")
    assert parse_runs(data).runs[0].usage is None
    data["runs"][0]["request_usage"] = {"compute_cost": 0}
    usage = parse_runs(data).runs[0].usage
    assert usage.total_tokens is None
    assert usage.inference_cost is None
    assert usage.compute_cost == Decimal(0)


@pytest.mark.parametrize("field", ["run_id", "conversation_id", "parent_run_id", "root_run_id"])
@pytest.mark.parametrize("value", ["x" * 129, "\x1b[31m", "has spaces", 42, {}])
def test_invalid_identity_fields_never_enter_models(field, value):
    data = payload()
    data["runs"][0][field] = value
    with pytest.raises(ValueError, match="Invalid Warp run response"):
        parse_runs(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("total_tokens", -1),
        ("total_tokens", True),
        ("total_tokens", 1.5),
        ("total_tokens", 2**63),
        ("inference_cost", -0.5),
        ("platform_cost", float("nan")),
        ("compute_cost", float("inf")),
        ("inference_cost_usd", "0.2"),
        ("compute_cost_usd", True),
        ("platform_cost_usd", 10**10),
    ],
)
def test_invalid_usage_is_not_silently_zero(field, value):
    data = payload()
    data["runs"][0]["request_usage"][field] = value
    with pytest.raises(ValueError, match="Invalid Warp run response"):
        parse_runs(data)


@pytest.mark.parametrize("field", ["as_of", "created_at", "updated_at"])
@pytest.mark.parametrize("value", ["2026-11-12", "2026-11-12T16:00:00", CANARY, None])
def test_source_times_must_be_bounded_timezone_aware_rfc3339(field, value):
    data = payload()
    (data if field == "as_of" else data["runs"][0])[field] = value
    with pytest.raises(ValueError):
        parse_runs(data)


def test_invalid_envelopes_duplicates_rows_and_scope_are_rejected():
    data = payload()
    cases = [
        None,
        {},
        {**data, "runs": data["runs"] * 26},
        {**data, "runs": data["runs"] * 2},
        {**data, "page_info": {"has_next_page": "true"}},
        {**data, "team_uid": "other-team"},
    ]
    for case in cases:
        with pytest.raises(ValueError, match="Invalid Warp run response"):
            parse_runs(case, team_uid="team-one")


def test_unselected_scope_can_span_teams_and_unknown_state_is_not_raw_text():
    data = payload()
    data["team_uid"] = None
    data["runs"][0]["state"] = CANARY
    report = parse_runs(data)
    assert report.team_uid is None
    assert report.runs[0].state == "UNKNOWN"
    assert CANARY not in repr(report)


async def test_fixed_get_bounded_page_auth_header_team_scope_and_separate_source_time():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert str(request.url) == "https://app.warp.dev/api/v1/agent/runs?limit=25"
        assert request.headers["Authorization"] == "Bearer synthetic-key"
        assert request.headers["X-Warp-Team-Uid"] == "team-one"
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, json=payload())

    source = provider(handler=handler)
    snapshot = await source.collect()
    assert len(requests) == 1
    assert source.spec.key.provider == "warp-runs"
    assert source.spec.capabilities == (Capability.AI,)
    assert source.spec.interval_seconds == 300
    assert snapshot.coverage == Coverage.PARTIAL
    assert snapshot.observed_at == SOURCE_TIME
    assert snapshot.collected_at == NOW
    assert CANARY not in repr(snapshot)
    metrics = {m.id: m.value for m in snapshot.metrics}
    assert "overlap" in metrics["scope"].lower()
    assert "not an account total" in metrics["scope"].lower()
    assert "more" in metrics["pagination"].lower()
    assert metrics["source-time"] == SOURCE_TIME.isoformat()
    assert "estimate" in metrics["usd-policy"].lower()
    assert metrics["run-0-tokens"] == 100
    assert "1.25" in metrics["run-0-credits"]


async def test_overlapping_conversations_and_parent_child_are_never_summed():
    data = payload()
    child = copy.deepcopy(data["runs"][0])
    child.update(run_id="run-child", parent_run_id="run-one")
    data["runs"].append(child)
    snapshot = await provider(data).collect()
    metrics = {m.id: m.value for m in snapshot.metrics}
    assert metrics["run-0-tokens"] == metrics["run-1-tokens"] == 100
    assert not any("total" in m.id or "account" in m.id for m in snapshot.metrics)
    assert "parent run-one" in metrics["run-1"]


async def test_missing_usage_is_explicitly_unknown_in_snapshot():
    data = payload()
    data["runs"][0].pop("request_usage")
    snapshot = await provider(data).collect()
    metrics = {m.id: m.value for m in snapshot.metrics}
    assert metrics["run-0-tokens"] is None
    assert "unknown" in metrics["run-0-credits"].lower()
    assert "unknown" in metrics["run-0-usd"].lower()


async def test_constructor_never_reads_keychain_and_default_lookup_is_off_ui_thread(monkeypatch):
    calls = []
    ui_thread = threading.get_ident()

    class FakeKeychain:
        def __init__(self):
            assert threading.get_ident() != ui_thread

        def get(self, source, account):
            calls.append((source, account, threading.get_ident()))
            return "synthetic-key"

    monkeypatch.setattr("dayboard.integrations.ai.warp.provider.Keychain", FakeKeychain)
    source = provider(payload(), credential=None)
    assert calls == []
    await source.collect()
    assert len(calls) == 1
    assert calls[0][:2] == ("warp", "test-key")
    assert calls[0][2] != ui_thread


def test_disabled_source_cannot_construct_collector_or_touch_credential():
    with pytest.raises(ValueError, match="explicitly enabled"):
        provider(payload(), settings=WarpSettings(), credential=lambda: pytest.fail("credential"))


@pytest.mark.parametrize("secret", [None, "", "has newline\n", "x" * 4097, "contains space", "é"])
async def test_missing_or_invalid_credential_fails_before_transport(secret):
    source = provider(
        credential=lambda: secret,
        handler=lambda request: pytest.fail("request with invalid credential"),
    )
    with pytest.raises(ProviderUnavailable) as error:
        await source.collect()
    assert error.value.code == ErrorCode.WARP_AUTH_REQUIRED
    assert CANARY not in str(error.value)


async def test_locked_keychain_errors_are_sanitized_before_transport():
    def credential():
        raise RuntimeError(CANARY)

    with pytest.raises(ProviderUnavailable) as error:
        await provider(credential=credential).collect()
    assert error.value.code == ErrorCode.WARP_AUTH_REQUIRED
    assert CANARY not in str(error.value)


@pytest.mark.parametrize(
    "status,code",
    [
        (401, ErrorCode.WARP_AUTH_REQUIRED),
        (403, ErrorCode.WARP_AUTH_REQUIRED),
        (500, ErrorCode.WARP_UNAVAILABLE),
        (302, ErrorCode.WARP_UNAVAILABLE),
    ],
)
async def test_http_failures_drop_response_bodies_and_never_follow_redirects(status, code):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            status, text=CANARY, headers={"Location": "https://evil.invalid/steal"}
        )

    with pytest.raises(ProviderUnavailable) as error:
        await provider(handler=handler).collect()
    assert error.value.code == code
    assert CANARY not in repr(error.value)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "retry,expected",
    [
        ("120", 120),
        ("Thu, 12 Nov 2026 16:02:00 GMT", 120),
        ("-4", 0),
        ("nan", 300),
        (CANARY, 300),
        ("99999999999999999999999", 86400),
    ],
)
async def test_retry_after_is_safe_finite_and_bounded(retry, expected):
    with pytest.raises(RateLimited) as error:
        await provider(
            handler=lambda r: httpx.Response(429, text=CANARY, headers={"retry-after": retry})
        ).collect()
    assert error.value.retry_after == expected


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"x" * 1_048_577),
        httpx.Response(200, content=CANARY.encode()),
        httpx.Response(200, content=b'{"runs": [NaN]}'),
        httpx.Response(200, content=b"[" * 10000),
        httpx.Response(200, json=payload(), headers={"content-encoding": "unknown"}),
        httpx.Response(200, content=b"{}", headers={"content-length": "999999999999"}),
    ],
)
async def test_oversized_malformed_and_encoded_payloads_have_safe_errors(response):
    with pytest.raises(ProviderUnavailable) as error:
        await provider(handler=lambda r: response).collect()
    assert error.value.code == ErrorCode.INVALID_RESULT
    assert CANARY not in repr(error.value)


@pytest.mark.parametrize("exception", [httpx.ReadTimeout(CANARY), httpx.ConnectError(CANARY)])
async def test_network_errors_never_expose_raw_exception(exception):
    def handler(request):
        raise exception

    with pytest.raises(ProviderUnavailable) as error:
        await provider(handler=handler).collect()
    assert error.value.code in (ErrorCode.TIMEOUT, ErrorCode.WARP_UNAVAILABLE)
    assert CANARY not in repr(error.value)


async def test_no_team_header_without_selected_scope():
    def handler(request):
        assert "X-Warp-Team-Uid" not in request.headers
        data = payload()
        data["team_uid"] = None
        return httpx.Response(200, json=data)

    await provider(handler=handler, settings=WarpSettings(True)).collect()


async def test_cancellation_closes_stream_and_does_not_publish_partial_results():
    entered = asyncio.Event()
    closed = asyncio.Event()

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            entered.set()
            yield b'{"runs":['
            await asyncio.Event().wait()

        async def aclose(self):
            closed.set()

    source = provider(handler=lambda r: httpx.Response(200, stream=Stream()))
    task = asyncio.create_task(source.collect())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()


async def test_scheduler_keeps_last_good_reading_after_safe_failure():
    responses = [httpx.Response(200, json=payload()), httpx.Response(500, text=CANARY)]
    source = provider(handler=lambda r: responses.pop(0))
    registry = ProviderRegistry()
    registry.register(source)
    store = SnapshotStore()
    ticks = [0.0]
    scheduler = Scheduler(registry, store, clock=lambda: ticks[0], jitter=lambda: 0)
    await scheduler.refresh(source.spec.key)
    first = store.status(source.spec.key).snapshot
    ticks[0] = 301
    await scheduler.refresh(source.spec.key)
    status = store.status(source.spec.key)
    assert status.snapshot is first
    assert status.error == ErrorCode.WARP_UNAVAILABLE
    assert CANARY not in repr(status)


async def test_maximum_page_produces_bounded_metrics():
    data = payload()
    data["runs"] = [
        {**data["runs"][0], "run_id": f"run-{i}", "root_run_id": f"run-{i}"} for i in range(25)
    ]
    snapshot = await provider(data).collect()
    assert len(snapshot.metrics) <= 256
    assert len([m for m in snapshot.metrics if m.id.endswith("-tokens")]) == 25
    assert len(json.dumps([m.value for m in snapshot.metrics])) < 100_000


@pytest.mark.parametrize(
    "body",
    [
        b'{"cost":1e9999999999999999999999999999999999999999999999}',
        b'{"runs":[],"as_of":"2026-11-12T16:00:00Z","team_uid":null,'
        b'"page_info":{"has_next_page":false},"cost":' + b"9" * 5000 + b"}",
    ],
)
async def test_extreme_json_numbers_are_mapped_to_safe_invalid_result(body, caplog):
    with pytest.raises(ProviderUnavailable) as error:
        await provider(handler=lambda r: httpx.Response(200, content=body)).collect()
    assert error.value.code == ErrorCode.INVALID_RESULT
    assert CANARY not in caplog.text


async def test_chunked_body_cap_stops_reading_and_closes_stream():
    chunks = []
    closed = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for index in range(30):
                chunks.append(index)
                yield b"x" * 65536

        async def aclose(self):
            closed.append(True)

    with pytest.raises(ProviderUnavailable) as error:
        await provider(handler=lambda r: httpx.Response(200, stream=Stream())).collect()
    assert error.value.code == ErrorCode.INVALID_RESULT
    assert len(chunks) == 17
    assert closed == [True]


async def test_compressed_response_is_rejected_without_reading_body():
    closed = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            pytest.fail("encoded body must not be read or decompressed")
            yield b""

        async def aclose(self):
            closed.append(True)

    with pytest.raises(ProviderUnavailable) as error:
        await provider(
            handler=lambda r: httpx.Response(
                200, stream=Stream(), headers={"content-encoding": "gzip"}
            )
        ).collect()
    assert error.value.code == ErrorCode.INVALID_RESULT
    assert closed == [True]


async def test_overall_deadline_maps_safely_and_closes_stream(monkeypatch):
    monkeypatch.setattr("dayboard.integrations.ai.warp.provider.COLLECTION_TIMEOUT_SECONDS", 0.01)
    closed = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.Event().wait()
            yield b""

        async def aclose(self):
            closed.append(True)

    with pytest.raises(ProviderUnavailable) as error:
        await provider(handler=lambda r: httpx.Response(200, stream=Stream())).collect()
    assert error.value.code == ErrorCode.TIMEOUT
    assert closed == [True]


async def test_cancellation_during_credential_read_never_sends_late_request():
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def credential():
        entered.set()
        release.wait(timeout=2)
        finished.set()
        return "synthetic-key"

    source = provider(
        credential=credential,
        handler=lambda r: pytest.fail("canceled collection must not make a late request"),
    )
    task = asyncio.create_task(source.collect())
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 2)
    await asyncio.sleep(0)


async def test_environment_credentials_are_not_a_fallback(monkeypatch):
    monkeypatch.setenv("WARP_API_KEY", CANARY)

    class FakeKeychain:
        def get(self, source, account):
            return None

    monkeypatch.setattr("dayboard.integrations.ai.warp.provider.Keychain", FakeKeychain)
    with pytest.raises(ProviderUnavailable) as error:
        await provider(
            credential=None,
            handler=lambda r: pytest.fail("no fallback API request allowed"),
        ).collect()
    assert error.value.code == ErrorCode.WARP_AUTH_REQUIRED


async def test_successful_collection_logs_and_provider_state_exclude_private_payload(caplog):
    source = provider(payload())
    with caplog.at_level("DEBUG"):
        await source.collect()
    assert CANARY not in caplog.text
    assert CANARY not in repr(source.__dict__)
    assert "synthetic-key" not in repr(source.__dict__)


async def test_empty_page_is_not_a_zero_usage_claim():
    data = payload()
    data["runs"] = []
    data["page_info"]["has_next_page"] = False
    snapshot = await provider(data).collect()
    metrics = {m.id: m.value for m in snapshot.metrics}
    assert "No runs returned" in metrics["empty"]
    assert "end of page list" in metrics["pagination"]
    assert not any(m.unit == "tokens" for m in snapshot.metrics)
