"""Local OS counters only: no addresses, process inventory, probes or persistent state."""

import asyncio
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import psutil

from dayboard.core.models import Coverage, Metric, ProviderKey, Quality, Snapshot
from dayboard.core.providers import Capability, ProviderSpec


@dataclass(frozen=True)
class CpuCounters:
    total: float
    idle: float


@dataclass(frozen=True)
class MemoryCounters:
    total: int
    available: int


@dataclass(frozen=True)
class NetworkCounters:
    name: str
    received: int
    sent: int


@dataclass(frozen=True)
class SystemSample:
    cpu: CpuCounters | None
    memory: MemoryCounters | None
    network: tuple[NetworkCounters, ...] | None


class SystemReader(Protocol):
    def sample(self) -> SystemSample: ...


class PsutilReader:
    def sample(self) -> SystemSample:
        cpu = None
        memory = None
        network = None
        failures = (OSError, psutil.Error, RuntimeError, TypeError, ValueError, OverflowError)
        try:
            times = psutil.cpu_times()._asdict()
            # Guest counters are already included in user/nice; never count them twice.
            total = sum(
                value for name, value in times.items() if name not in ("guest", "guest_nice")
            )
            cpu = CpuCounters(total, times["idle"] + times.get("iowait", 0))
        except failures:
            pass
        try:
            ram = psutil.virtual_memory()
            memory = MemoryCounters(ram.total, ram.available)
        except failures:
            pass
        try:
            stats = psutil.net_if_stats()
            counters = psutil.net_io_counters(pernic=True, nowrap=False)
            active = [
                NetworkCounters(name, counter.bytes_recv, counter.bytes_sent)
                for name, counter in sorted(counters.items())
                if name in stats
                and stats[name].isup
                and name not in ("lo", "lo0")
                and "loopback" not in stats[name].flags.split(",")
            ]
            if len(active) <= 128:
                network = tuple(active)
        except failures:
            pass
        return SystemSample(cpu, memory, network)


def _valid_cpu(value: CpuCounters | None) -> bool:
    return (
        value is not None
        and all(
            type(counter) in (int, float) and math.isfinite(counter) and counter >= 0
            for counter in (value.total, value.idle)
        )
        and value.idle <= value.total
    )


def _cpu_delta(current: CpuCounters | None, previous: CpuCounters | None) -> float | None:
    if not _valid_cpu(current) or not _valid_cpu(previous):
        return None
    assert current is not None and previous is not None
    total, idle = current.total - previous.total, current.idle - previous.idle
    if total <= 0 or idle < 0 or idle > total:
        return None
    return round(100 * (total - idle) / total, 1)


def _network_map(values: tuple[NetworkCounters, ...] | None) -> dict[str, NetworkCounters] | None:
    if values is None or not 0 < len(values) <= 128:
        return None
    result = {}
    for value in values:
        if (
            not isinstance(value.name, str)
            or not 0 < len(value.name) <= 128
            or value.name in result
            or any(type(n) is not int or n < 0 for n in (value.received, value.sent))
        ):
            return None
        result[value.name] = value
    return result


def _network_delta(
    current: tuple[NetworkCounters, ...] | None,
    previous: tuple[NetworkCounters, ...] | None,
    elapsed: float,
) -> tuple[float | None, float | None]:
    now, before = _network_map(current), _network_map(previous)
    if now is None or before is None or now.keys() != before.keys():
        return None, None
    received = sent = 0
    for name, value in now.items():
        incoming, outgoing = value.received - before[name].received, value.sent - before[name].sent
        if incoming < 0 or outgoing < 0:
            return None, None
        received += incoming
        sent += outgoing
    return received / elapsed, sent / elapsed


class TelemetryProvider:
    def __init__(
        self,
        reader: SystemReader | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.reader = reader if reader is not None else PsutilReader()
        self.clock = clock
        self.wall_clock = wall_clock
        self._previous: tuple[float, SystemSample] | None = None
        self.spec = ProviderSpec(
            ProviderKey("mac-telemetry"),
            "Local CPU, memory and network",
            (Capability.SYSTEM,),
            interval_seconds=1,
            timeout_seconds=3,
        )

    async def collect(self) -> Snapshot:
        # No blocking psutil interval calls and no thread-local/global CPU-percent baseline.
        current = await asyncio.to_thread(self.reader.sample)
        stamp = self.clock()
        cpu = incoming = outgoing = None
        if self._previous is not None:
            previous_stamp, previous = self._previous
            elapsed = stamp - previous_stamp
            # After sleep, suspension, or a clock anomaly, establish a new baseline.
            if math.isfinite(elapsed) and 0 < elapsed <= 10:
                cpu = _cpu_delta(current.cpu, previous.cpu)
                incoming, outgoing = _network_delta(current.network, previous.network, elapsed)
        self._previous = stamp, current
        ram = current.memory
        total = available = used = None
        percent = None
        if (
            ram is not None
            and type(ram.total) is int
            and type(ram.available) is int
            and ram.total > 0
            and 0 <= ram.available <= ram.total
        ):
            total, available = ram.total, ram.available
            used = total - available
            percent = round(100 * used / total, 1)
        notes = []
        if cpu is None:
            notes.append("CPU warming up/reset/unavailable")
        if incoming is None:
            notes.append("Network warming up/reset/unavailable")
        if percent is None:
            notes.append("RAM unavailable")
        now = self.wall_clock()
        return Snapshot(
            self.spec.key,
            now,
            now,
            (
                Metric("cpu_percent", "CPU", cpu, "%"),
                Metric("memory_percent", "RAM", percent, "%"),
                Metric("memory_used", "RAM in use (total minus available)", used, "B"),
                Metric("memory_total", "RAM total", total, "B"),
                Metric("memory_available", "RAM available", available, "B"),
                Metric("network_receive", "Download", incoming, "B/s"),
                Metric("network_send", "Upload", outgoing, "B/s"),
                Metric(
                    "network_scope",
                    "Network scope",
                    "All active non-loopback interfaces; VPN/bridges may double-count",
                ),
                Metric("sample_status", "Sampling", "; ".join(notes) or "Ready"),
            ),
            Quality.DERIVED,
            Coverage.LOCAL,
        )
