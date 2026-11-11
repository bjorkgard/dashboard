"""Defense in depth; diagnostics must still use an explicit nonsecret allowlist."""

import re
from collections.abc import Iterable
from typing import Any

_ANSI = re.compile(r"\x1b(?:\][^\x07\x1b]*(?:\x07|\x1b\\)|\[[0-?]*[ -/]*[@-~])")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_SENSITIVE = re.compile(r"token|password|secret|authorization|cookie|api.?key|credential", re.I)
_BEARER = re.compile(r"\bBearer\s+[^\s,;]+", re.I)
_ASSIGNMENT = re.compile(
    r"\b((?:access_?token|refresh_?token|password|secret|api_?key)\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
    re.I,
)


def safe_text(value: str, known_secrets: Iterable[str] = ()) -> str:
    for secret in known_secrets:
        if secret:
            value = value.replace(secret, "[REDACTED]")
    value = _CONTROL.sub("", _ANSI.sub("", value))
    value = _BEARER.sub("Bearer [REDACTED]", value)
    return _ASSIGNMENT.sub(r"\1[REDACTED]", value)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            safe_text(str(key)): "[REDACTED]" if _SENSITIVE.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (tuple, list)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return safe_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return "[UNSUPPORTED]"
