"""Read-only, fixed-host run listing with lazy Dayboard-owned Keychain access."""

import asyncio
import json
import math
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, DecimalException
from email.utils import parsedate_to_datetime

import httpx

from dayboard.core.models import Coverage, ErrorCode, Metric, ProviderKey, Snapshot
from dayboard.core.providers import Capability, ProviderSpec, ProviderUnavailable, RateLimited
from dayboard.integrations.ai.warp.models import WarpReport, WarpUsage, parse_runs
from dayboard.security.keychain import Keychain
from dayboard.settings import WarpSettings

ENDPOINT = "https://app.warp.dev/api/v1/agent/runs"
MAX_RESPONSE_BYTES = 1_048_576
REQUEST_TIMEOUT_SECONDS = 10
COLLECTION_TIMEOUT_SECONDS = 15


def _retry_after(value: str, now: datetime) -> float:
    if len(value) > 128:
        return 300
    try:
        delay = float(value)
        if not math.isfinite(delay):
            return 300
    except ValueError:
        try:
            delay = (parsedate_to_datetime(value) - now).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return 300
    return max(0, min(delay, 86400))


def _reject_constant(value: str) -> None:
    raise ValueError("Invalid Warp run response.")


def _costs(usage: WarpUsage | None, *, usd: bool = False) -> str:
    fields = ("inference", "compute", "platform")
    amounts = (
        (usage.inference_cost_usd, usage.compute_cost_usd, usage.platform_cost_usd)
        if usage is not None and usd
        else (
            (usage.inference_cost, usage.compute_cost, usage.platform_cost)
            if usage is not None
            else (None, None, None)
        )
    )
    return " · ".join(
        f"{name} {amount if amount is not None else 'unknown'}"
        for name, amount in zip(fields, amounts, strict=True)
    )


def _snapshot(report: WarpReport, key: ProviderKey, now: datetime) -> Snapshot:
    metrics = [
        Metric(
            "scope",
            "Scope",
            "Bounded overlapping run/conversation preview; not an account total. "
            "Parent/child and resumed conversations may overlap. "
            "No desktop-wide usage or remaining-credit balance.",
        ),
        Metric("source-time", "API source time (may lag activity)", report.as_of.isoformat()),
        Metric("team", "API team scope", report.team_uid or "Across accessible teams"),
        Metric(
            "pagination",
            "Coverage",
            f"{len(report.runs)} runs on one page of at most 25; "
            + (
                "more results available (not fetched)"
                if report.has_next_page
                else "end of page list"
            ),
        ),
        Metric(
            "usd-policy", "USD provenance", "Billed amount or fallback estimate; not an invoice"
        ),
    ]
    if not report.runs:
        metrics.append(Metric("empty", "Runs", "No runs returned in this scope"))
    for index, run in enumerate(report.runs):
        relationships = (
            f"conversation {run.conversation_id or 'unknown'} · "
            f"parent {run.parent_run_id or 'none'} · root {run.root_run_id}"
        )
        metrics.extend(
            (
                Metric(
                    f"run-{index}",
                    f"Run {run.run_id}",
                    f"{run.state} · {relationships} · "
                    f"created {run.created_at.isoformat()} · updated {run.updated_at.isoformat()}",
                ),
                Metric(
                    f"run-{index}-tokens",
                    "Conversation tokens (overlapping)",
                    run.usage.total_tokens if run.usage is not None else None,
                    "tokens",
                ),
                Metric(f"run-{index}-credits", "Run credits", _costs(run.usage), "Warp credits"),
                Metric(
                    f"run-{index}-usd",
                    "Run USD (billed or estimated)",
                    _costs(run.usage, usd=True),
                    "USD",
                ),
            )
        )
    return Snapshot(key, report.as_of, now, tuple(metrics), coverage=Coverage.PARTIAL)


class WarpProvider:
    def __init__(
        self,
        settings: WarpSettings,
        credential: Callable[[], str | None] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not settings.enabled:
            raise ValueError("Warp must be explicitly enabled.")
        self.settings = settings
        self._credential = credential
        self._transport = transport
        self.clock = clock
        self.spec = ProviderSpec(
            ProviderKey("warp-runs"),
            "Warp / Oz runs",
            (Capability.AI,),
            interval_seconds=300,
            timeout_seconds=20,
        )

    def _read_credential(self) -> str | None:
        if self._credential is not None:
            return self._credential()
        return Keychain().get("warp", self.settings.credential_id)

    async def collect(self) -> Snapshot:
        """Fetch one page only; no state or credentials are cached on the provider."""
        try:
            async with asyncio.timeout(COLLECTION_TIMEOUT_SECONDS):
                return await self._collect()
        except (TimeoutError, httpx.TimeoutException):
            raise ProviderUnavailable(ErrorCode.TIMEOUT) from None
        except httpx.HTTPError:
            raise ProviderUnavailable(ErrorCode.WARP_UNAVAILABLE) from None

    async def _collect(self) -> Snapshot:
        try:
            credential = await asyncio.to_thread(self._read_credential)
        except Exception:
            raise ProviderUnavailable(ErrorCode.WARP_AUTH_REQUIRED) from None
        if (
            not isinstance(credential, str)
            or not 1 <= len(credential) <= 4096
            or any(not 33 <= ord(char) <= 126 for char in credential)
        ):
            raise ProviderUnavailable(ErrorCode.WARP_AUTH_REQUIRED)
        headers = {
            "Authorization": f"Bearer {credential}",
            "User-Agent": "Dayboard-warp",
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        }
        if self.settings.team_uid:
            headers["X-Warp-Team-Uid"] = self.settings.team_uid
        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=REQUEST_TIMEOUT_SECONDS,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            async with client.stream(
                "GET", ENDPOINT, params={"limit": 25}, headers=headers
            ) as response:
                if response.status_code == 429:
                    raise RateLimited(
                        retry_after=_retry_after(
                            response.headers.get("retry-after", "300"), self.clock()
                        )
                    )
                if response.status_code in (401, 403):
                    raise ProviderUnavailable(ErrorCode.WARP_AUTH_REQUIRED)
                if response.status_code != 200:
                    raise ProviderUnavailable(ErrorCode.WARP_UNAVAILABLE)
                # Reject unsolicited compression before decoding, including decompression bombs.
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise ProviderUnavailable(ErrorCode.INVALID_RESULT)
                length = response.headers.get("content-length")
                if length is not None and (
                    not length.isascii()
                    or not length.isdecimal()
                    or len(length) > 10
                    or int(length) > MAX_RESPONSE_BYTES
                ):
                    raise ProviderUnavailable(ErrorCode.INVALID_RESULT)
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise ProviderUnavailable(ErrorCode.INVALID_RESULT)
                    body.extend(chunk)
        try:
            report = parse_runs(
                json.loads(body, parse_float=Decimal, parse_constant=_reject_constant),
                team_uid=self.settings.team_uid,
            )
        except (ValueError, TypeError, OverflowError, RecursionError, DecimalException):
            raise ProviderUnavailable(ErrorCode.INVALID_RESULT) from None
        return _snapshot(report, self.spec.key, self.clock())
