"""Apple Mail header sampling; no source access or authorization at import/construction."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from dayboard.core.models import Coverage, ErrorCode, Metric, ProviderKey, Snapshot
from dayboard.core.providers import Capability, ProviderSpec, ProviderUnavailable
from dayboard.integrations.mail.bridge import MailBridge, authorize_mail, mail_status
from dayboard.paths import AppPaths
from dayboard.security.redaction import safe_text
from dayboard.settings import MailboxSelection, MailSettings

__all__ = [
    "MailProvider",
    "MailHeader",
    "parse_headers",
    "mail_status",
    "authorize_mail",
    "discover_mailboxes",
]


class Reader(Protocol):
    async def call(self, operation: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class MailHeader:
    id: str
    account_id: str
    account_name: str
    mailbox_path: tuple[str, ...]
    sender: str
    subject: str
    received: datetime
    read: bool


def _text(value: Any, limit: int) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError("Invalid Mail metadata.")
    return safe_text(value)[:limit]


def _authorized(data: Any) -> None:
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int:
        raise ValueError("Invalid Mail bridge response.")
    if data["schema_version"] != 1:
        raise ValueError("Invalid Mail bridge version.")
    status = data.get("status")
    if status == "not_determined":
        raise ProviderUnavailable(ErrorCode.MAIL_PERMISSION_REQUIRED)
    if status in ("denied", "restricted"):
        raise ProviderUnavailable(ErrorCode.MAIL_PERMISSION_DENIED)
    if status != "authorized":
        raise ProviderUnavailable(ErrorCode.MAIL_UNAVAILABLE)


def parse_headers(data: Any, settings: MailSettings) -> tuple[MailHeader, ...]:
    """Project bounded headers only, discard unknown/raw fields and out-of-scope records."""
    _authorized(data)
    raw = data.get("headers")
    if not isinstance(raw, list) or len(raw) > 100:
        raise ValueError("Invalid Mail header list.")
    selected = {(box.account_id, box.mailbox_path): box for box in settings.mailboxes}
    result: dict[tuple[str, tuple[str, ...], str], MailHeader] = {}
    counts: dict[tuple[str, tuple[str, ...]], int] = {}
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Invalid Mail header.")
        try:
            account = _text(item["account_id"], 256)
            parts = item["mailbox_path"]
            if not isinstance(parts, list) or not 1 <= len(parts) <= 8:
                raise ValueError("Invalid Mail mailbox.")
            path = tuple(_text(part, 200) for part in parts)
            scope = account, path
            if scope not in selected:
                continue
            identity = _text(item["id"], 128)
            if not identity or type(item["read"]) is not bool:
                raise ValueError("Invalid Mail header identity or flag.")
            received = datetime.fromisoformat(_text(item["received"], 64))
            if received.utcoffset() is None:
                raise ValueError("Invalid Mail date.")
            header = MailHeader(
                identity,
                account,
                selected[scope].account_name,
                path,
                _text(item["sender"], 500),
                _text(item["subject"], 500),
                received,
                item["read"],
            )
            key = account, path, identity
            if key not in result:
                if counts.get(scope, 0) >= 20:
                    continue
                counts[scope] = counts.get(scope, 0) + 1
                result[key] = header
        except (KeyError, TypeError, OverflowError):
            raise ValueError("Invalid Mail header metadata.") from None
        except ValueError:
            raise ValueError("Invalid Mail header metadata.") from None
    return tuple(sorted(result.values(), key=lambda item: item.received, reverse=True)[:50])


async def discover_mailboxes(paths: AppPaths) -> tuple[MailboxSelection, ...]:
    """Explicit bounded discovery, never requests permission or builds the helper."""
    data = await MailBridge(paths).call("discover")
    _authorized(data)
    raw = data.get("mailboxes")
    if not isinstance(raw, list) or len(raw) > 200:
        raise ValueError("Invalid Mail mailbox discovery.")
    result: dict[tuple[str, tuple[str, ...]], MailboxSelection] = {}
    try:
        for item in raw:
            box = MailboxSelection.decode(item)
            result[box.account_id, box.mailbox_path] = box
    except (ValueError, TypeError):
        raise ValueError("Invalid Mail mailbox discovery.") from None
    return tuple(result.values())


class MailProvider:
    def __init__(
        self, settings: MailSettings, paths: AppPaths, bridge: Reader | None = None
    ) -> None:
        if not settings.enabled:
            raise ValueError("Mail must be explicitly enabled.")
        self.settings = settings
        self.bridge = bridge if bridge is not None else MailBridge(paths, settings.mailboxes)
        self.spec = ProviderSpec(
            ProviderKey("apple-mail"),
            "Apple Mail",
            (Capability.MAIL,),
            interval_seconds=120,
            timeout_seconds=15,
        )

    async def collect(self) -> Snapshot:
        data = await self.bridge.call("headers")
        headers = parse_headers(data, self.settings)
        # A missing scope is not a successful empty inbox; Scheduler keeps the last good snapshot.
        if data.get("selection_missing"):
            raise ProviderUnavailable(ErrorCode.MAIL_UNAVAILABLE)
        now = datetime.now(UTC)
        metrics = [
            Metric(
                "scope",
                "Selected mailbox sample",
                f"{len(headers)} headers · sampled, not globally latest"
                + (" · truncated" if data.get("truncated") else ""),
            ),
        ]
        for index, header in enumerate(headers):
            label = header.received.astimezone().strftime("%d %b %H:%M")
            label += " · read" if header.read else " · unread"
            value = (
                f"{header.sender} · {header.subject} · "
                f"{header.account_name or header.account_id} / {' / '.join(header.mailbox_path)}"
            )
            metrics.append(Metric(f"mail-{index}", label, value[:3000]))
        return Snapshot(self.spec.key, now, now, tuple(metrics), coverage=Coverage.PARTIAL)
