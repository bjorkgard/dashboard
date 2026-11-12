"""Latest per-limit quota observations, separate from local token accounting."""

import asyncio
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from dayboard.core.ai_observations import QuotaWindow
from dayboard.core.models import Coverage, ErrorCode, Metric, ProviderKey, Snapshot
from dayboard.core.providers import Capability, ProviderSpec, ProviderUnavailable
from dayboard.integrations.ai.codex.parser import digest
from dayboard.integrations.ai.codex.transport import CodexAppServer
from dayboard.settings import CodexSettings


class LimitsTransport(Protocol):
    async def read_limits(self) -> dict[str, Any]: ...


def parse_limits(payload: Any, now: datetime) -> tuple[QuotaWindow, ...]:
    """0.167.2 quota profile; multi-bucket view supersedes its single-bucket mirror."""
    try:
        if not isinstance(payload, dict):
            raise ValueError
        account = payload.get("accountId")
        if account is not None and (not isinstance(account, str) or len(account) > 256):
            raise ValueError
        account_id = digest(account) if account else None
        buckets = payload.get("rateLimitsByLimitId")
        single = payload.get("rateLimits")
        if not isinstance(single, dict):
            raise ValueError
        if buckets is None:
            buckets = {single.get("limitId") or "codex": single}
        if not isinstance(buckets, dict) or len(buckets) > 32:
            raise ValueError
        windows: list[QuotaWindow] = []
        for identifier, bucket in sorted(buckets.items()):
            if (
                not isinstance(identifier, str)
                or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", identifier)
                or not isinstance(bucket, dict)
            ):
                raise ValueError
            declared = bucket.get("limitId")
            if declared is not None and declared != identifier:
                raise ValueError
            for kind in ("primary", "secondary"):
                value = bucket.get(kind)
                if value is None:
                    continue
                if not isinstance(value, dict) or "usedPercent" not in value:
                    raise ValueError
                reset = value.get("resetsAt")
                if reset is not None and (type(reset) is not int or not 0 <= reset <= 253402300799):
                    raise ValueError
                windows.append(
                    QuotaWindow(
                        identifier if identifier == "codex" else "limit-" + digest(identifier)[:16],
                        kind,
                        value["usedPercent"],
                        now,
                        datetime.fromtimestamp(reset, UTC) if reset is not None else None,
                        value.get("windowDurationMins"),
                        account_id,
                    )
                )
        if not windows:
            raise ValueError
        return tuple(windows)
    except (ValueError, TypeError, OverflowError, OSError):
        raise ProviderUnavailable(ErrorCode.AI_UNSUPPORTED) from None


class CodexLimitsProvider:
    def __init__(
        self,
        settings: CodexSettings,
        *,
        transport: LimitsTransport | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not settings.limits_enabled:
            raise ValueError("Codex limits require separate explicit enablement.")
        self.settings, self.clock = settings, clock
        self.transport = (
            transport if transport is not None else CodexAppServer(settings.source_root)
        )
        self.windows: tuple[QuotaWindow, ...] = ()
        self._lock = asyncio.Lock()
        self.spec = ProviderSpec(
            ProviderKey("codex-limits"),
            "Codex optional account limits",
            (Capability.AI,),
            interval_seconds=300,
            timeout_seconds=10,
        )

    async def collect(self) -> Snapshot:
        async with self._lock:
            payload = await self.transport.read_limits()
            now = self.clock()
            windows = parse_limits(payload, now)
            metrics = [
                Metric("coverage", "Coverage", "CLI account quotas · not local usage or billing"),
                Metric("observed", "Quota observed at", now.isoformat()),
            ]
            for index, window in enumerate(windows):
                prefix = f"quota-{index}"
                metrics.extend(
                    (
                        Metric(
                            f"{prefix}-used",
                            f"{window.limit_id} {window.window} used",
                            window.used_percent,
                            "%",
                        ),
                        Metric(
                            f"{prefix}-duration", "Window duration", window.duration_minutes, "min"
                        ),
                        Metric(
                            f"{prefix}-reset",
                            "Reset at",
                            window.reset_at.isoformat() if window.reset_at else None,
                        ),
                    )
                )
            snapshot = Snapshot(self.spec.key, now, now, tuple(metrics), coverage=Coverage.ACCOUNT)
            self.windows = windows
            return snapshot
