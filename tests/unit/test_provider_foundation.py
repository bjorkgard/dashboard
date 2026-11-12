import os
import threading
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from dayboard.core.ai_observations import CreditRecord, QuotaWindow
from dayboard.errors import ConfigError
from dayboard.integrations.ai.reader import LocalReader
from dayboard.settings import (
    CodexSettings,
    KiroSettings,
    MailboxSelection,
    MailSettings,
    WarpSettings,
)


def test_new_settings_are_opt_in_and_preserve_explicit_scope():
    assert not any(
        s.enabled for s in (CodexSettings(), KiroSettings(), MailSettings(), WarpSettings())
    )
    assert not CodexSettings().limits_enabled
    mail = MailSettings(True, (MailboxSelection("account-one", ("Inbox",), "Example"),))
    assert MailSettings.decode(mail.encode()) == mail
    for settings in (
        CodexSettings(True, "/example"),
        KiroSettings(True, "/example"),
        WarpSettings(True),
    ):
        assert type(settings).decode(settings.encode()) == settings
    with pytest.raises(ConfigError):
        MailSettings(True)
    with pytest.raises(ConfigError):
        KiroSettings(True, "relative")
    with pytest.raises(ConfigError):
        WarpSettings.decode({"api_key": "PRIVATE_CANARY"})


def test_credits_and_quota_windows_remain_distinct_and_validated():
    now = datetime.now(UTC)
    credit = CreditRecord("execution-one", "session-one", now, Decimal("0.0438"), "kiro-model")
    assert credit.amount == Decimal("0.0438")
    quota = QuotaWindow("limit-one", "primary", 25.0, now, None, None, None)
    assert quota.used_percent == 25 and quota.reset_at is None
    with pytest.raises(ValueError):
        CreditRecord("bad", "session", now, Decimal("NaN"))
    with pytest.raises(ValueError):
        QuotaWindow("limit", "primary", float("nan"), now)


def test_recent_first_selection_occurs_before_the_file_cap(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    for index in range(5):
        path = root / f"{index}.jsonl"
        path.write_text(f'{{"value": {index}}}\n')
        os.utime(path, ns=(1_000_000 + index, 1_000_000 + index))
    reader = LocalReader[int](
        str(root),
        parser=lambda data, path, offset: data["value"],
        max_files=2,
    )
    batch = reader.scan(threading.Event())
    assert batch.records == (4, 3)
    assert "file cap" in batch.issues
    assert batch.files == (("4.jsonl",), ("3.jsonl",))
    reader.commit(batch)
    reader.close()


def test_layout_and_parser_only_see_explicit_selected_subtree(tmp_path):
    root = tmp_path / "root"
    (root / "sessions").mkdir(parents=True)
    (root / "sessions" / "rollout-one.jsonl").write_text('{"value": 1}\n')
    (root / "auth.json").write_text("PRIVATE_CANARY")
    reader = LocalReader[tuple](
        str(root),
        subtree=("sessions",),
        parser=lambda data, path, offset: (data["value"], path, offset),
        accept_file=lambda path: path[-1].startswith("rollout-") and path[-1].endswith(".jsonl"),
    )
    batch = reader.scan(threading.Event())
    assert batch.records == ((1, ("rollout-one.jsonl",), 0),)
    assert "CANARY" not in repr(batch.records)
    batch.close()
