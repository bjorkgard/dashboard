import threading
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from dayboard.core.models import Coverage, Quality
from dayboard.integrations.telemetry import (
    CpuCounters,
    MemoryCounters,
    NetworkCounters,
    PsutilReader,
    SystemSample,
    TelemetryProvider,
)

NOW = datetime(2026, 11, 11, tzinfo=UTC)


def sample(total=100, idle=60, received=1000, sent=500, name="en0"):
    return SystemSample(
        CpuCounters(total, idle),
        MemoryCounters(16_000, 6000),
        (NetworkCounters(name, received, sent),),
    )


class Reader:
    def __init__(self, *samples):
        self.samples = iter(samples)
        self.thread = None

    def sample(self):
        self.thread = threading.get_ident()
        return next(self.samples)


def values(snapshot):
    return {metric.id: metric.value for metric in snapshot.metrics}


async def test_first_sample_has_unknown_rates_but_real_memory_and_uses_worker_thread():
    reader = Reader(sample())
    provider = TelemetryProvider(reader, clock=lambda: 1, wall_clock=lambda: NOW)
    reading = await provider.collect()
    result = values(reading)
    assert result["cpu_percent"] is None
    assert result["network_receive"] is None
    assert result["network_send"] is None
    assert result["memory_used"] == 10_000
    assert result["memory_available"] == 6000
    assert result["memory_percent"] == 62.5
    assert reader.thread != threading.get_ident()
    assert reading.coverage == Coverage.LOCAL
    assert reading.quality == Quality.DERIVED
    assert not reading.synthetic
    assert provider.spec.interval_seconds == 1


async def test_second_sample_uses_cpu_counter_delta_and_monotonic_network_rates():
    times = iter([1.0, 3.0])
    provider = TelemetryProvider(
        Reader(sample(), sample(total=110, idle=67, received=5096, sent=1524)),
        clock=lambda: next(times),
        wall_clock=lambda: NOW,
    )
    await provider.collect()
    result = values(await provider.collect())
    assert result["cpu_percent"] == 30
    assert result["network_receive"] == 2048
    assert result["network_send"] == 512


async def test_no_traffic_is_zero_not_unknown_after_warmup():
    times = iter([1, 2])
    provider = TelemetryProvider(
        Reader(sample(), sample(total=110, idle=70)),
        clock=lambda: next(times),
        wall_clock=lambda: NOW,
    )
    await provider.collect()
    result = values(await provider.collect())
    assert result["cpu_percent"] == 0
    assert result["network_receive"] == 0
    assert result["network_send"] == 0


@pytest.mark.parametrize(
    "second",
    [
        sample(total=10, idle=6, received=10, sent=5),
        sample(name="en1"),
        SystemSample(CpuCounters(110, 65), MemoryCounters(16000, 6000), ()),
    ],
)
async def test_counter_resets_interface_changes_and_no_interfaces_do_not_spike(second):
    times = iter([1, 2])
    provider = TelemetryProvider(
        Reader(sample(), second),
        clock=lambda: next(times),
        wall_clock=lambda: NOW,
    )
    await provider.collect()
    result = values(await provider.collect())
    assert result["network_receive"] is None
    assert result["network_send"] is None
    assert result["memory_percent"] == 62.5


@pytest.mark.parametrize("times", [[1, 1], [2, 1], [1, 100]])
async def test_nonpositive_or_sleep_gap_resets_rate_baselines(times):
    stamps = iter(times)
    provider = TelemetryProvider(
        Reader(sample(), sample(total=110, idle=67, received=10**9)),
        clock=lambda: next(stamps),
        wall_clock=lambda: NOW,
    )
    await provider.collect()
    result = values(await provider.collect())
    assert result["cpu_percent"] is None
    assert result["network_receive"] is None


async def test_network_is_sum_of_per_interface_deltas_not_lifetime_counters():
    first = SystemSample(
        None,
        None,
        (
            NetworkCounters("en0", 1000, 500),
            NetworkCounters("en1", 2000, 1000),
        ),
    )
    second = SystemSample(
        None,
        None,
        (
            NetworkCounters("en0", 1100, 550),
            NetworkCounters("en1", 2200, 1100),
        ),
    )
    times = iter([0, 1])
    provider = TelemetryProvider(
        Reader(first, second), clock=lambda: next(times), wall_clock=lambda: NOW
    )
    await provider.collect()
    result = values(await provider.collect())
    assert result["network_receive"] == 300
    assert result["network_send"] == 150


async def test_unavailable_groups_remain_unknown_and_recover_without_old_baseline():
    times = iter([0, 1, 2, 3])
    provider = TelemetryProvider(
        Reader(sample(), SystemSample(None, None, None), sample(), sample(total=110, idle=65)),
        clock=lambda: next(times),
        wall_clock=lambda: NOW,
    )
    await provider.collect()
    missing = values(await provider.collect())
    assert missing["memory_percent"] is None
    assert missing["cpu_percent"] is None
    assert values(await provider.collect())["cpu_percent"] is None
    assert values(await provider.collect())["cpu_percent"] == 50


@pytest.mark.parametrize(
    "memory", [MemoryCounters(0, 0), MemoryCounters(100, 101), MemoryCounters(-1, 0)]
)
async def test_invalid_memory_is_unknown_not_negative_or_infinite(memory):
    provider = TelemetryProvider(Reader(SystemSample(None, memory, None)), wall_clock=lambda: NOW)
    result = values(await provider.collect())
    assert result["memory_percent"] is None
    assert result["memory_used"] is None


def test_psutil_reader_excludes_loopback_and_down_interfaces_and_disables_wrap_cache(monkeypatch):
    import dayboard.integrations.telemetry as module

    cpu = SimpleNamespace(_asdict=lambda: {"user": 40, "system": 10, "idle": 50})
    monkeypatch.setattr(module.psutil, "cpu_times", lambda: cpu)
    monkeypatch.setattr(
        module.psutil, "virtual_memory", lambda: SimpleNamespace(total=16000, available=6000)
    )
    stats = {
        "en0": SimpleNamespace(isup=True, flags="up,broadcast"),
        "lo0": SimpleNamespace(isup=True, flags="up,loopback"),
        "en1": SimpleNamespace(isup=False, flags="broadcast"),
    }
    monkeypatch.setattr(module.psutil, "net_if_stats", lambda: stats)

    def counters(*, pernic, nowrap):
        assert pernic and not nowrap
        return {name: SimpleNamespace(bytes_recv=1000, bytes_sent=100) for name in stats}

    monkeypatch.setattr(module.psutil, "net_io_counters", counters)
    reading = PsutilReader().sample()
    assert reading.cpu == CpuCounters(100, 50)
    assert reading.network == (NetworkCounters("en0", 1000, 100),)


def test_os_group_failures_do_not_hide_other_groups_or_expose_exception_text(monkeypatch):
    import dayboard.integrations.telemetry as module

    def broken():
        raise OSError("SYNTHETIC_PRIVATE_INFORMATION")

    monkeypatch.setattr(module.psutil, "cpu_times", broken)
    monkeypatch.setattr(module.psutil, "net_if_stats", broken)
    monkeypatch.setattr(
        module.psutil, "virtual_memory", lambda: SimpleNamespace(total=100, available=25)
    )
    reading = PsutilReader().sample()
    assert reading.cpu is None
    assert reading.network is None
    assert reading.memory == MemoryCounters(100, 25)
    assert "SYNTHETIC_PRIVATE_INFORMATION" not in repr(reading)
