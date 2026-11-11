"""One owned task per source. Provider failures never propagate raw exception text."""

import asyncio
import random
import time
from collections.abc import Callable
from datetime import UTC, datetime

from dayboard.core.models import ErrorCode, ProviderKey, Snapshot
from dayboard.core.providers import ProviderRegistry, ProviderUnavailable, RateLimited
from dayboard.core.store import SnapshotStore


class Scheduler:
    def __init__(
        self,
        registry: ProviderRegistry,
        store: SnapshotStore,
        *,
        clock: Callable[[], float] = time.monotonic,
        jitter: Callable[[], float] = random.random,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self.registry = registry
        self.store = store
        self._clock = clock
        self._jitter = jitter
        self._tasks: dict[ProviderKey, asyncio.Task[None]] = {}
        self._locks: dict[ProviderKey, asyncio.Lock] = {}
        self._due: dict[ProviderKey, float] = {}
        self._failures: dict[ProviderKey, int] = {}
        self._closed = False
        self._on_change = on_change

    @property
    def running_count(self) -> int:
        return sum(not task.done() for task in self._tasks.values())

    def next_due(self, key: ProviderKey) -> float:
        return self._due.get(key, 0)

    def start(self) -> None:
        if self._closed:
            raise RuntimeError("A closed scheduler cannot be restarted.")
        for provider in self.registry.all():
            key = provider.spec.key
            if key not in self._tasks:
                self._tasks[key] = asyncio.create_task(self._loop(key), name="dayboard-collector")

    async def _loop(self, key: ProviderKey) -> None:
        while True:
            await self.refresh(key)
            await asyncio.sleep(max(0.001, self.next_due(key) - self._clock()))

    async def refresh(self, key: ProviderKey) -> bool:
        provider = self.registry.get(key)
        lock = self._locks.setdefault(key, asyncio.Lock())
        if self._closed or lock.locked() or self.next_due(key) > self._clock():
            return False
        async with lock:
            error: ErrorCode | None = None
            retry_after = 0.0
            try:
                async with asyncio.timeout(provider.spec.timeout_seconds):
                    result = await provider.collect()
                if (
                    not isinstance(result, Snapshot)
                    or result.key != key
                    or result.synthetic != self.registry.demo
                ):
                    error = ErrorCode.INVALID_RESULT
                else:
                    self.store.publish(result)
            except TimeoutError:
                error = ErrorCode.TIMEOUT
            except ProviderUnavailable as unavailable:
                error = unavailable.code
            except RateLimited as limited:
                error = ErrorCode.RATE_LIMITED
                retry_after = limited.retry_after
            except asyncio.CancelledError:
                raise
            except Exception:
                error = ErrorCode.COLLECTION_FAILED
            interval = provider.spec.interval_seconds
            if error is not None:
                failures = min(self._failures.get(key, 0) + 1, 16)
                self._failures[key] = failures
                self.store.fail(key, error, datetime.now(UTC))
                interval = max(interval, min(interval * 2 ** (failures - 1), 300))
            else:
                self._failures[key] = 0
            delay = max(retry_after, interval * (1 + 0.1 * max(0, min(1, self._jitter()))))
            self._due[key] = self._clock() + delay
        if self._on_change is not None:
            self._on_change()
        return True

    async def close(self) -> None:
        self._closed = True
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
