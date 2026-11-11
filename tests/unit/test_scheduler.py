import asyncio
from datetime import UTC, datetime

import pytest

from dayboard.core.models import ErrorCode, Metric, ProviderKey, Snapshot
from dayboard.core.providers import Capability, ProviderRegistry, ProviderSpec, RateLimited
from dayboard.core.scheduler import Scheduler
from dayboard.core.store import SnapshotStore


class Provider:
    def __init__(self, name, action=None, **kwargs):
        self.spec = ProviderSpec(
            ProviderKey(name),
            name,
            (Capability.SYSTEM,),
            interval_seconds=0.01,
            timeout_seconds=0.1,
            **kwargs,
        )
        self.action = action
        self.calls = 0
        self.cancelled = asyncio.Event()

    async def collect(self):
        self.calls += 1
        if self.action:
            await self.action()
        now = datetime.now(UTC)
        return Snapshot(
            self.spec.key,
            now,
            now,
            (Metric("count", "Count", self.calls),),
            synthetic=self.spec.synthetic,
        )


def registry(*providers, demo=False):
    result = ProviderRegistry(demo=demo)
    for provider in providers:
        result.register(provider)
    return result


def test_registry_rejects_duplicates_and_mode_mixing():
    result = registry(Provider("one"))
    with pytest.raises(ValueError):
        result.register(Provider("one"))
    with pytest.raises(ValueError):
        result.register(Provider("demo", synthetic=True))
    with pytest.raises(ValueError):
        registry(Provider("live"), demo=True)


@pytest.mark.parametrize(
    "field,value",
    [
        ("interval_seconds", 0),
        ("timeout_seconds", -1),
        ("interval_seconds", float("inf")),
    ],
)
def test_invalid_collection_budgets_are_rejected(field, value):
    with pytest.raises(ValueError):
        ProviderSpec(ProviderKey("one"), "One", (Capability.SYSTEM,), **{field: value})


async def test_slow_provider_does_not_block_fast_provider_and_shutdown_cancels():
    cancelled = asyncio.Event()
    started = asyncio.Event()
    ready = asyncio.Event()

    async def slow():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def fast():
        ready.set()

    store = SnapshotStore()
    scheduler = Scheduler(registry(Provider("slow", slow), Provider("fast", fast)), store)
    scheduler.start()
    try:
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.wait_for(ready.wait(), 1)
        await asyncio.sleep(0)
        assert store.history(ProviderKey("fast"))
    finally:
        await scheduler.close()
    assert cancelled.is_set()
    assert scheduler.running_count == 0


async def test_timeout_records_safe_code_not_provider_exception():
    async def hang():
        await asyncio.Event().wait()

    provider = Provider("slow", hang)
    store = SnapshotStore()
    scheduler = Scheduler(registry(provider), store)
    assert await scheduler.refresh(provider.spec.key)
    assert store.status(provider.spec.key).error == ErrorCode.TIMEOUT


async def test_backoff_retry_after_and_success_reset_with_monotonic_clock():
    time = [100.0]

    async def limited():
        if provider.calls == 1:
            raise RateLimited(retry_after=5)

    provider = Provider("api", limited)
    store = SnapshotStore()
    scheduler = Scheduler(registry(provider), store, clock=lambda: time[0], jitter=lambda: 0)
    await scheduler.refresh(provider.spec.key)
    assert scheduler.next_due(provider.spec.key) == 105
    assert not await scheduler.refresh(provider.spec.key)
    assert provider.calls == 1
    time[0] = 105
    assert await scheduler.refresh(provider.spec.key)
    assert store.status(provider.spec.key).error is None
    assert scheduler.next_due(provider.spec.key) == pytest.approx(105.01)


async def test_failure_retains_last_good_and_backs_off_exponentially():
    time = [0.0]

    async def flaky():
        if provider.calls > 1:
            raise RuntimeError("SYNTHETIC_SECRET")

    provider = Provider("flaky", flaky)
    store = SnapshotStore()
    scheduler = Scheduler(registry(provider), store, clock=lambda: time[0], jitter=lambda: 0)
    await scheduler.refresh(provider.spec.key)
    reading = store.history(provider.spec.key)[-1]
    time[0] = 1
    await scheduler.refresh(provider.spec.key)
    time[0] = 2
    await scheduler.refresh(provider.spec.key)
    status = store.status(provider.spec.key)
    assert status.snapshot == reading
    assert status.error == ErrorCode.COLLECTION_FAILED
    assert scheduler.next_due(provider.spec.key) == pytest.approx(2.02)
    assert "SYNTHETIC_SECRET" not in repr(status)


async def test_overlapping_refresh_is_skipped():
    started, release = asyncio.Event(), asyncio.Event()

    async def block():
        started.set()
        await release.wait()

    provider = Provider("one", block)
    scheduler = Scheduler(registry(provider), SnapshotStore())
    task = asyncio.create_task(scheduler.refresh(provider.spec.key))
    await started.wait()
    assert not await scheduler.refresh(provider.spec.key)
    release.set()
    assert await task
    assert provider.calls == 1


async def test_mismatched_account_snapshot_is_rejected():
    class WrongProvider(Provider):
        async def collect(self):
            now = datetime.now(UTC)
            return Snapshot(ProviderKey("other", "private"), now, now, ())

    provider = WrongProvider("one")
    store = SnapshotStore()
    scheduler = Scheduler(registry(provider), store)
    await scheduler.refresh(provider.spec.key)
    assert not store.history(ProviderKey("other", "private"))
    assert store.status(provider.spec.key).error == ErrorCode.INVALID_RESULT


async def test_start_and_close_are_idempotent():
    scheduler = Scheduler(registry(Provider("one")), SnapshotStore())
    scheduler.start()
    scheduler.start()
    assert scheduler.running_count == 1
    await scheduler.close()
    await scheduler.close()
    assert scheduler.running_count == 0
