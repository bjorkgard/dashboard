import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from dayboard.security.identifiers import validate_identifier


class Quality(StrEnum):
    REPORTED = "reported"
    DERIVED = "derived"
    ESTIMATED = "estimated"


class Coverage(StrEnum):
    LOCAL = "local machine"
    ACCOUNT = "account"
    PARTIAL = "partial"


class ConnectionState(StrEnum):
    DISCONNECTED = "not connected"
    READY = "ready"
    STALE = "stale"
    ERROR = "error"


class ErrorCode(StrEnum):
    AI_SOURCE_UNAVAILABLE = (
        "Local AI source unavailable or unsafe; choose an owned directory in Settings"
    )
    AI_PERMISSION_DENIED = "Local AI source access denied; review source permissions"
    AI_UNSUPPORTED = "Source format or requested usage metric is unsupported"
    MAIL_PERMISSION_REQUIRED = "Authorize Mail Automation explicitly in Settings"
    MAIL_PERMISSION_DENIED = "Mail Automation denied; review macOS privacy permissions"
    MAIL_UNAVAILABLE = "Mail unavailable; open Mail explicitly and check selected mailboxes"
    MAIL_SETUP_REQUIRED = "Configure Mail explicitly in Settings"
    CODEX_SETUP_REQUIRED = "Codex limits unavailable; install Codex and sign in separately"
    WARP_AUTH_REQUIRED = "Warp API key unavailable; configure Dayboard Keychain credentials"
    WARP_UNAVAILABLE = "Warp run usage unavailable; check connection and selected scope"
    GITHUB_SETUP_REQUIRED = (
        "GitHub unavailable; install gh, run gh auth login, and check repository access"
    )
    PERMISSION_REQUIRED = "calendar permission required; run dayboard calendar authorize"
    PERMISSION_DENIED = "calendar access denied; review macOS Calendar privacy permissions"
    UNAVAILABLE = "provider unavailable; run setup or check native helper"
    TIMEOUT = "collection timed out"
    RATE_LIMITED = "rate limited"
    COLLECTION_FAILED = "collection failed"
    INVALID_RESULT = "invalid provider result"


@dataclass(frozen=True)
class ProviderKey:
    provider: str
    account: str | None = None

    def __post_init__(self) -> None:
        validate_identifier(self.provider)
        if self.account is not None:
            validate_identifier(self.account)


@dataclass(frozen=True)
class Metric:
    id: str
    label: str
    value: str | int | float | None
    unit: str = ""

    def __post_init__(self) -> None:
        validate_identifier(self.id)
        if (
            not isinstance(self.label, str)
            or len(self.label) > 200
            or not isinstance(self.unit, str)
            or len(self.unit) > 32
        ):
            raise ValueError("Invalid metric labels.")
        if self.value is not None and type(self.value) not in (str, int, float):
            raise ValueError("Invalid metric value.")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("Nonfinite readings must be unknown.")
        if isinstance(self.value, str) and len(self.value) > 4096:
            raise ValueError("Metric text exceeds the size limit.")


@dataclass(frozen=True)
class Snapshot:
    key: ProviderKey
    observed_at: datetime
    collected_at: datetime
    metrics: tuple[Metric, ...]
    quality: Quality = Quality.REPORTED
    coverage: Coverage = Coverage.LOCAL
    synthetic: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.key, ProviderKey):
            raise ValueError("Invalid source.")
        for date in (self.observed_at, self.collected_at):
            if not isinstance(date, datetime) or date.utcoffset() is None:
                raise ValueError("Readings require timezone-aware timestamps.")
        if (
            not isinstance(self.metrics, tuple)
            or len(self.metrics) > 256
            or not all(isinstance(metric, Metric) for metric in self.metrics)
        ):
            raise ValueError("Metrics must be an immutable, bounded tuple.")
        if len({metric.id for metric in self.metrics}) != len(self.metrics):
            raise ValueError("Metric identifiers must be unique within a snapshot.")
        if not isinstance(self.quality, Quality) or not isinstance(self.coverage, Coverage):
            raise ValueError("Invalid reading provenance.")
        if type(self.synthetic) is not bool:
            raise ValueError("Invalid synthetic-data flag.")


@dataclass(frozen=True)
class SourceStatus:
    state: ConnectionState
    snapshot: Snapshot | None = None
    error: ErrorCode | None = None
    attempted_at: datetime | None = None
