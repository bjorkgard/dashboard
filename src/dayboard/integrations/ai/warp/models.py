"""Allowlisted metadata from https://docs.warp.dev/openapi.json.

RequestUsage.total_tokens describes a conversation, not a disjoint run delta.
Its three *_cost fields are credits; *_usd may be billed OR fallback estimates.
Optional model/category breakdowns are deliberately not retained.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

MAX_RUNS = 25
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}")
_RFC3339 = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})")
_STATES = frozenset(
    {
        "QUEUED",
        "PENDING",
        "CLAIMED",
        "INPROGRESS",
        "SUCCEEDED",
        "FAILED",
        "BLOCKED",
        "ERROR",
        "CANCELLED",
    }
)
_INVALID = "Invalid Warp run response."


@dataclass(frozen=True)
class WarpUsage:
    total_tokens: int | None
    inference_cost: Decimal | None
    compute_cost: Decimal | None
    platform_cost: Decimal | None
    inference_cost_usd: Decimal | None
    compute_cost_usd: Decimal | None
    platform_cost_usd: Decimal | None


@dataclass(frozen=True)
class WarpRun:
    run_id: str
    state: str
    created_at: datetime
    updated_at: datetime
    conversation_id: str | None
    parent_run_id: str | None
    root_run_id: str
    usage: WarpUsage | None


@dataclass(frozen=True)
class WarpReport:
    runs: tuple[WarpRun, ...]
    as_of: datetime
    team_uid: str | None
    has_next_page: bool


def _identity(value: Any) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(_INVALID)
    return value


def _optional_identity(value: Any) -> str | None:
    return None if value is None else _identity(value)


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 40 or not _RFC3339.fullmatch(value):
        raise ValueError(_INVALID)
    try:
        return datetime.fromisoformat(value).astimezone(UTC)
    except (ValueError, OverflowError):
        raise ValueError(_INVALID) from None


def _amount(value: Any) -> Decimal | None:
    if value is None:
        return None
    if type(value) not in (int, float, Decimal) or len(str(value)) > 64:
        raise ValueError(_INVALID)
    amount = Decimal(str(value))
    if not amount.is_finite() or not 0 <= amount <= 10**9:
        raise ValueError(_INVALID)
    return amount


def _usage(value: Any) -> WarpUsage | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(_INVALID)
    tokens = value.get("total_tokens")
    if tokens is not None and (type(tokens) is not int or not 0 <= tokens < 2**63):
        raise ValueError(_INVALID)
    return WarpUsage(
        tokens,
        _amount(value.get("inference_cost")),
        _amount(value.get("compute_cost")),
        _amount(value.get("platform_cost")),
        _amount(value.get("inference_cost_usd")),
        _amount(value.get("compute_cost_usd")),
        _amount(value.get("platform_cost_usd")),
    )


def parse_runs(data: Any, *, team_uid: str = "") -> WarpReport:
    """Copy only validated scalar fields into frozen, bounded normalized metadata.

    No reference to the payload, pagination cursor, prompt or arbitrary metadata
    survives parsing. A selected scope must match the server's reported scope.
    Missing usage is unknown; missing fields within usage remain individually unknown.
    """
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("runs"), list)
        or len(data["runs"]) > MAX_RUNS
        or not isinstance(data.get("page_info"), dict)
        or type(data["page_info"].get("has_next_page")) is not bool
        or "team_uid" not in data
    ):
        raise ValueError(_INVALID)
    team = _optional_identity(data["team_uid"])
    if team_uid and (_identity(team_uid) != team):
        raise ValueError(_INVALID)
    as_of = _timestamp(data.get("as_of"))
    runs: list[WarpRun] = []
    seen: set[str] = set()
    for item in data["runs"]:
        if not isinstance(item, dict):
            raise ValueError(_INVALID)
        identity = _identity(item.get("run_id"))
        if identity in seen:
            raise ValueError(_INVALID)
        seen.add(identity)
        state = item.get("state")
        if not isinstance(state, str) or len(state) > 128:
            raise ValueError(_INVALID)
        runs.append(
            WarpRun(
                identity,
                state if state in _STATES else "UNKNOWN",
                _timestamp(item.get("created_at")),
                _timestamp(item.get("updated_at")),
                _optional_identity(item.get("conversation_id")),
                _optional_identity(item.get("parent_run_id")),
                _identity(item.get("root_run_id")),
                _usage(item.get("request_usage")),
            )
        )
    return WarpReport(tuple(runs), as_of, team, data["page_info"]["has_next_page"])
