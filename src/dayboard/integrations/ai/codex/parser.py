"""Positive rollout profile: session_meta, turn_context and event_msg/token_count.

Counter semantics follow codex-rs/protocol TokenUsageInfo: input includes cached input;
output includes reasoning output. last_token_usage is used only to validate a first
single-call baseline, never added alongside total_token_usage. No text is retained.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID

type Counters = tuple[int, int, int, int]


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class RolloutEvent:
    file_id: str
    offset: int
    timestamp: datetime
    kind: str
    session_id: str | None = None
    model: str | None = None
    counts: Counters | None = None
    last: Counters | None = None


def _counts(value: Any) -> Counters | None:
    if not isinstance(value, dict):
        return None
    values = tuple(
        value.get(key)
        for key in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_output_tokens",
        )
    )
    if any(type(v) is not int or not 0 <= v <= 10**12 for v in values):
        return None
    i, c, o, r = cast(Counters, values)
    total = value.get("total_tokens")
    if c > i or r > o or type(total) is not int or total != i + o:
        return None
    return i, c, o, r


def parse_rollout(data: Any, path: tuple[str, ...], offset: int) -> RolloutEvent | None:
    """Pure parser; drops unknown envelopes and all non-allowlisted payload fields."""
    if not isinstance(data, dict) or not isinstance(data.get("payload"), dict):
        return None
    raw_stamp = data.get("timestamp")
    if not isinstance(raw_stamp, str) or len(raw_stamp) > 64:
        return None
    try:
        stamp = datetime.fromisoformat(raw_stamp.replace("Z", "+00:00"))
        if stamp.utcoffset() is None:
            return None
    except ValueError:
        return None
    file_id = digest("/".join(path))
    payload = data["payload"]
    match data.get("type"):
        case "session_meta":
            sid = payload.get("id")
            if not isinstance(sid, str) or len(sid) > 36:
                return RolloutEvent(file_id, offset, stamp, "session")
            try:
                sid = str(UUID(sid))
            except ValueError:
                return RolloutEvent(file_id, offset, stamp, "session")
            return RolloutEvent(file_id, offset, stamp, "session", session_id=digest(sid))
        case "turn_context":
            model = payload.get("model")
            if (
                not isinstance(model, str)
                or not re.fullmatch(
                    r"(?:gpt-\d+(?:\.\d+)*(?:-(?:codex|mini|nano|pro|max|latest))*"
                    r"|o[1-9]\d*(?:-mini)?|codex-mini-latest)",
                    model,
                )
                or len(model) > 100
            ):
                model = "unknown"
            return RolloutEvent(file_id, offset, stamp, "model", model=model)
        case "event_msg":
            info = payload.get("info")
            if payload.get("type") != "token_count" or not isinstance(info, dict):
                return None
            counts = _counts(info.get("total_token_usage"))
            if counts is None:
                return None
            return RolloutEvent(
                file_id,
                offset,
                stamp,
                "checkpoint",
                counts=counts,
                last=_counts(info.get("last_token_usage")),
            )
    return None
