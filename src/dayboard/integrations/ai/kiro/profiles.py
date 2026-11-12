"""Public example: https://zenn.dev/zenogawa/articles/kiro-cli-credit?locale=en.

The example documents Linux CLI 2.16.1's --v3 sessions. Version numbers alone
are not evidence that this storage format exists. No CLI-header usage fields
are currently verified. Only allowlisted immutable usage metadata leaves here.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from dayboard.core.ai_observations import CreditRecord

_WORKSPACE = re.compile(r"[0-9a-f]{16}\Z")
_SESSION = re.compile(r"sess_[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
_EXECUTION = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
Profile = Literal["v3-credit", "v3-session-only", "unsupported"]


class InvalidKiroUsage(ValueError):
    def __init__(self) -> None:
        super().__init__("Unsupported or invalid Kiro usage metadata.")


@dataclass(frozen=True)
class KiroObservation:
    profile: Profile
    session_id: str | None = None
    credit: CreditRecord | None = None


def identity(*parts: str) -> str:
    """Length-delimited identity prevents source/session/execution collisions."""
    return hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()


def _timestamp(value: Any) -> datetime:
    try:
        if not isinstance(value, str) or len(value) > 64:
            raise ValueError
        result = datetime.fromisoformat(value)
        if result.utcoffset() is None:
            raise ValueError
        return result.astimezone(UTC)
    except (ValueError, OverflowError):
        raise InvalidKiroUsage() from None


def parse_observation(
    data: Any,
    relative_path: tuple[str, ...],
    line_offset: int,
    *,
    source_id: str,
) -> KiroObservation | None:
    """Normalize only the exact observed layout/envelope; never infer usage."""
    if not (
        len(relative_path) == 3
        and _WORKSPACE.fullmatch(relative_path[0])
        and _SESSION.fullmatch(relative_path[1])
        and relative_path[2] == "messages.jsonl"
    ):
        return KiroObservation("unsupported")
    if not isinstance(data, dict) or not isinstance(data.get("payload"), dict):
        return KiroObservation("unsupported")
    payload = data["payload"]
    kind = payload.get("type")
    session_id = identity(source_id, *relative_path[:-1])
    if kind != "usage_summary":
        # These envelopes are independently present in the cited public fixture.
        if kind in ("user", "turn_start", "turn_end"):
            _timestamp(data.get("timestamp"))
            return KiroObservation("v3-session-only", session_id)
        return KiroObservation("unsupported")
    execution = payload.get("executionId")
    summaries = payload.get("promptTurnSummaries")
    if (
        not isinstance(execution, str)
        or not _EXECUTION.fullmatch(execution)
        or not isinstance(summaries, list)
        or not 1 <= len(summaries) <= 64
    ):
        raise InvalidKiroUsage()
    total = Decimal(0)
    for summary in summaries:
        if not isinstance(summary, dict) or summary.get("unit") != "credit":
            raise InvalidKiroUsage()
        amount = summary.get("usage")
        if type(amount) not in (int, float, Decimal):
            raise InvalidKiroUsage()
        decimal = Decimal(str(amount))
        if (
            not decimal.is_finite()
            or not 0 <= decimal <= 10**9
            or (decimal != 0 and decimal.adjusted() < -30)
        ):
            raise InvalidKiroUsage()
        total += decimal
    if total > 10**9:
        raise InvalidKiroUsage()
    record = CreditRecord(
        identity(session_id, execution),
        session_id,
        _timestamp(data.get("timestamp")),
        total,
    )
    return KiroObservation("v3-credit", session_id, record)
