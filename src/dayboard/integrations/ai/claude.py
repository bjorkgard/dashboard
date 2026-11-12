"""Observed Claude Code 2.x message.usage profile, not a vendor schema guarantee."""

import re
from datetime import UTC, datetime
from typing import Any

from dayboard.core.ai_usage import Counts, UsageRecord

FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)
_ID = re.compile(r"[A-Za-z0-9_.:\[\]/-]{1,160}\Z")


class InvalidUsage(ValueError):
    def __init__(self) -> None:
        super().__init__("Unsupported or invalid Claude usage metadata.")


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise InvalidUsage()
    return value


def _count(value: Any) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= 10**12:
        raise InvalidUsage()
    return value


def parse_usage(data: Any) -> UsageRecord | None:
    if not isinstance(data, dict):
        raise InvalidUsage()
    message = data.get("message")
    has_usage = "usage" in data or (isinstance(message, dict) and "usage" in message)
    if data.get("type") != "assistant":
        if has_usage:
            raise InvalidUsage()
        return None
    if not has_usage:
        return None
    if not isinstance(message, dict) or not isinstance(message.get("usage"), dict):
        raise InvalidUsage()
    usage = message["usage"]
    if not any(field in usage for field in FIELDS):
        raise InvalidUsage()
    stamp = data.get("timestamp")
    try:
        if not isinstance(stamp, str) or len(stamp) > 64:
            raise ValueError
        timestamp = datetime.fromisoformat(stamp)
        if timestamp.utcoffset() is None:
            raise ValueError
    except (ValueError, OverflowError):
        raise InvalidUsage() from None
    version = data.get("version")
    if version is not None and (
        not isinstance(version, str) or not re.fullmatch(r"2\.\d{1,4}\.\d{1,6}", version)
    ):
        raise InvalidUsage()
    tokens = tuple(_count(usage.get(field)) for field in FIELDS)
    buckets: Counts = (None, None)
    if "cache_creation" in usage:
        split = usage["cache_creation"]
        if not isinstance(split, dict):
            raise InvalidUsage()
        buckets = (
            _count(split.get("ephemeral_5m_input_tokens")),
            _count(split.get("ephemeral_1h_input_tokens")),
        )
        if all(value is not None for value in buckets):
            total = sum(value or 0 for value in buckets)
            if tokens[2] is not None and total != tokens[2]:
                raise InvalidUsage()
    return UsageRecord(
        _identifier(message.get("id")),
        _identifier(data.get("sessionId")),
        None if data.get("requestId") is None else _identifier(data["requestId"]),
        timestamp.astimezone(UTC),
        _identifier(message.get("model")),
        version,
        tokens,
        buckets,
    )
