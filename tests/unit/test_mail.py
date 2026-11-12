from datetime import UTC, datetime

import pytest

from dayboard.core.models import ErrorCode
from dayboard.core.providers import Capability, ProviderUnavailable
from dayboard.integrations.mail import MailProvider, mail_status, parse_headers
from dayboard.paths import AppPaths
from dayboard.settings import MailboxSelection, MailSettings


def settings():
    return MailSettings(True, (MailboxSelection("account", ("Inbox",), "Synthetic"),))


def header(**changes):
    return {
        "id": "42",
        "account_id": "account",
        "mailbox_path": ["Inbox"],
        "sender": "Synthetic Sender",
        "subject": "Fixture subject",
        "received": "2026-11-12T10:00:00Z",
        "read": False,
        **changes,
    }


def response(headers=None, **changes):
    return {
        "schema_version": 1,
        "status": "authorized",
        "headers": headers if headers is not None else [header()],
        **changes,
    }


def test_parser_projects_only_allowlisted_fields_and_deduplicates():
    result = parse_headers(response([header(body="PRIVATE_CANARY"), header()]), settings())
    assert len(result) == 1
    assert result[0].subject == "Fixture subject"
    assert "PRIVATE_CANARY" not in repr(result)
    assert result[0].received == datetime(2026, 11, 12, 10, tzinfo=UTC)


def test_parser_rejects_scope_and_limits():
    assert parse_headers(response([header(account_id="other")]), settings()) == ()
    with pytest.raises(ValueError, match="Invalid Mail"):
        parse_headers(response([header()] * 101), settings())
    with pytest.raises(ValueError, match="Invalid Mail"):
        parse_headers(response([header(read=1)]), settings())


@pytest.mark.parametrize(
    "status,code",
    [
        ("not_determined", ErrorCode.MAIL_PERMISSION_REQUIRED),
        ("denied", ErrorCode.MAIL_PERMISSION_DENIED),
        ("not_running", ErrorCode.MAIL_UNAVAILABLE),
    ],
)
def test_safe_permission_errors(status, code):
    with pytest.raises(ProviderUnavailable) as error:
        parse_headers(response(status=status), settings())
    assert error.value.code == code


async def test_provider_is_lazy_and_exports_sampled_metrics(tmp_path):
    class Bridge:
        def __init__(self):
            self.calls = []

        async def call(self, operation):
            self.calls.append(operation)
            return response()

    bridge = Bridge()
    provider = MailProvider(settings(), AppPaths(tmp_path / "data", tmp_path / "cache"), bridge)
    assert bridge.calls == []
    assert provider.spec.key.provider == "apple-mail"
    assert provider.spec.capabilities == (Capability.MAIL,)
    assert provider.spec.interval_seconds == 120
    snapshot = await provider.collect()
    assert bridge.calls == ["headers"]
    assert "sample" in str(snapshot.metrics[0].value).lower()
    assert "Fixture subject" in repr(snapshot)


async def test_missing_helper_status_never_builds_or_creates_state(tmp_path):
    paths = AppPaths(tmp_path / "data", tmp_path / "cache")
    status = await mail_status(paths)
    assert status["status"] in {"setup_required", "unsupported"}
    assert not paths.data_dir.exists()


def test_disabled_provider_fails_before_bridge_access(tmp_path):
    with pytest.raises(ValueError):
        MailProvider(MailSettings(), AppPaths(tmp_path / "data", tmp_path / "cache"))


@pytest.mark.parametrize(
    "changes",
    [
        {"received": "SECRET_CANARY"},
        {"received": "2026-01-01T12:00:00"},
        {"sender": ["SECRET_CANARY"]},
        {"subject": "x" * 501},
        {"mailbox_path": ["Inbox"] * 9},
        {"id": ""},
    ],
)
def test_invalid_metadata_errors_are_safe(changes):
    with pytest.raises(ValueError) as error:
        parse_headers(response([header(**changes)]), settings())
    assert "SECRET_CANARY" not in str(error.value)
    assert error.value.__cause__ is None


def test_caps_sorting_and_mailbox_provenance():
    boxes = tuple(MailboxSelection("account", (f"Box{index}",)) for index in range(5))
    selected = MailSettings(True, boxes)
    raw = [
        header(
            id=str(index), mailbox_path=[f"Box{box}"], received=f"2026-11-{index + 1:02}T10:00:00Z"
        )
        for box in range(5)
        for index in range(20)
    ]
    result = parse_headers(response(raw), selected)
    assert len(result) == 50
    assert result[0].received >= result[-1].received
    assert len({(item.id, item.mailbox_path) for item in result}) == 50


async def test_missing_selected_mailbox_is_failure_not_empty_success(tmp_path):
    class Bridge:
        async def call(self, operation):
            return response([], selection_missing=True)

    provider = MailProvider(settings(), AppPaths(tmp_path / "data", tmp_path / "cache"), Bridge())
    with pytest.raises(ProviderUnavailable) as error:
        await provider.collect()
    assert error.value.code == ErrorCode.MAIL_UNAVAILABLE


async def test_scheduler_keeps_last_good_on_failure_and_cancellation(tmp_path):
    import asyncio

    from dayboard.core.models import ConnectionState
    from dayboard.core.providers import ProviderRegistry
    from dayboard.core.scheduler import Scheduler
    from dayboard.core.store import SnapshotStore

    class Bridge:
        mode = "good"

        async def call(self, operation):
            if self.mode == "fail":
                raise RuntimeError("RAW_PRIVATE_CANARY")
            if self.mode == "cancel":
                raise asyncio.CancelledError
            return response([header(body="RAW_BODY_CANARY", password="RAW_SECRET_CANARY")])

    bridge = Bridge()
    provider = MailProvider(settings(), AppPaths(tmp_path / "data", tmp_path / "cache"), bridge)
    registry, store = ProviderRegistry(), SnapshotStore()
    registry.register(provider)
    tick = [0]
    scheduler = Scheduler(registry, store, clock=lambda: tick[0], jitter=lambda: 0)
    assert await scheduler.refresh(provider.spec.key)
    good = store.status(provider.spec.key).snapshot
    bridge.mode, tick[0] = "fail", 121
    assert await scheduler.refresh(provider.spec.key)
    status = store.status(provider.spec.key)
    assert status.state == ConnectionState.STALE
    assert status.snapshot is good
    assert "CANARY" not in repr(status)
    bridge.mode, tick[0] = "cancel", 1000
    with pytest.raises(asyncio.CancelledError):
        await scheduler.refresh(provider.spec.key)
    assert store.status(provider.spec.key).snapshot is good
    await scheduler.close()
