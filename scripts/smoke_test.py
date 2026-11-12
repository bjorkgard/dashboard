"""Run inside an isolated environment containing only a built Dayboard package."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import httpx
from textual.widgets import Button, Static, TabbedContent

from dayboard.app import DayboardApp
from dayboard.integrations.ai.codex import CodexLimitsProvider, CodexProvider
from dayboard.integrations.ai.kiro import KiroProvider
from dayboard.integrations.ai.provider import ClaudeProvider
from dayboard.integrations.ai.warp import WarpProvider
from dayboard.integrations.mail import MailProvider
from dayboard.paths import AppPaths
from dayboard.settings import (
    ClaudeSettings,
    CodexSettings,
    KiroSettings,
    MailboxSelection,
    MailSettings,
    WarpSettings,
)
from dayboard.ui.settings import SettingsScreen


async def smoke(expected_version: str) -> None:
    actual = version("dayboard")
    if actual != expected_version:
        raise RuntimeError("Installed distribution has the wrong version.")
    if not files("dayboard").joinpath("ui/styles/shell.tcss").is_file():
        raise RuntimeError("Installed distribution is missing its stylesheet.")
    for name in ("CalendarBridge.swift", "Info.plist", "MailBridge.swift", "MailInfo.plist"):
        if not files("dayboard").joinpath("native", name).is_file():
            raise RuntimeError("Installed distribution is missing native calendar sources.")
    with TemporaryDirectory(prefix="dayboard-claude-smoke-") as directory:
        # Resolve only our temporary fixture; source traversal itself never follows links.
        root = await asyncio.to_thread(Path(directory).resolve)
        projects = root / "projects"
        projects.mkdir()
        source = projects / "synthetic.jsonl"
        source.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "sessionId": "synthetic-session",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "version": "2.1.96",
                    "message": {
                        "id": "synthetic-message",
                        "model": "claude-example",
                        "content": [{"text": "PRIVATE_SMOKE_CANARY"}],
                        "usage": {"input_tokens": 101, "output_tokens": 12},
                    },
                }
            )
            + "\n"
        )
        before = source.read_bytes(), source.stat().st_mode, source.stat().st_mtime_ns
        provider = ClaudeProvider(ClaudeSettings(True, str(root)))
        snapshot = await provider.collect()
        if next(metric.value for metric in snapshot.metrics if metric.id == "today-input") != 101:
            raise RuntimeError("Installed Claude reader did not collect synthetic usage.")
        if "CANARY" in repr(snapshot) + repr(provider.ledger.records):
            raise RuntimeError("Installed Claude reader retained unallowlisted content.")
        if (source.read_bytes(), source.stat().st_mode, source.stat().st_mtime_ns) != before:
            raise RuntimeError("Installed Claude reader modified its source.")
        await smoke_providers(root)
    app = DayboardApp(demo=True)
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        if actual not in str(app.query_one("#release", Static).render()):
            raise RuntimeError("Installed UI reports the wrong version.")
        if "DEMO" not in str(app.query_one("#mode", Static).render()):
            raise RuntimeError("Isolated smoke test did not start in demo mode.")
        if not app.store.history(app.registry.all()[0].spec.key):
            raise RuntimeError("Synthetic collector did not produce a reading.")
        scheduler = app.scheduler
        for key, pane in (
            ("2", "schedule"),
            ("3", "mac"),
            ("4", "github"),
            ("5", "ai"),
            ("6", "inbox"),
        ):
            await pilot.press(key)
            if app.query_one(TabbedContent).active != pane:
                raise RuntimeError("Installed workspace navigation did not switch tabs.")
            if "SYNTHETIC" not in str(app.query_one(f"#card-{pane}-detail", Static).render()):
                raise RuntimeError("Installed detail view did not show synthetic data.")
        if app.scheduler is not scheduler:
            raise RuntimeError("Tab navigation must not replace collectors.")
        await pilot.press("s")
        await pilot.pause()
        if not isinstance(app.screen, SettingsScreen):
            raise RuntimeError("Installed Settings screen did not open.")
        if not app.screen.query_one("#settings-save", Button).disabled:
            raise RuntimeError("Demo Settings must not allow saving real preferences.")
        if not app.screen.query_one("#claude-source").disabled:
            raise RuntimeError("Demo Claude Settings must be read-only.")
        await pilot.press("escape")
        await pilot.pause()
        if isinstance(app.screen, SettingsScreen):
            raise RuntimeError("Cancel did not return to the installed overview.")
        if app.query_one(TabbedContent).active != "inbox":
            raise RuntimeError("Settings Cancel did not preserve the selected workspace tab.")
        await pilot.press("q")
    if app.scheduler.running_count:
        raise RuntimeError("Collectors remained alive after exit.")
    print(f"Installed package smoke test passed: dayboard {actual}")


def _source_fixtures(root: Path, stamp: str) -> tuple[Path, Path]:
    codex = root / "codex"
    (codex / "sessions").mkdir(parents=True)
    (codex / "archived_sessions").mkdir()
    session_id = "00000000-0000-4000-8000-000000000001"
    counts = {
        "input_tokens": 10,
        "cached_input_tokens": 2,
        "output_tokens": 4,
        "reasoning_output_tokens": 1,
        "total_tokens": 14,
    }
    rows = (
        {"type": "session_meta", "timestamp": stamp, "payload": {"id": session_id}},
        {"type": "turn_context", "timestamp": stamp, "payload": {"model": "gpt-5.4"}},
        {
            "type": "event_msg",
            "timestamp": stamp,
            "payload": {
                "type": "token_count",
                "info": {"total_token_usage": counts, "last_token_usage": counts},
            },
        },
    )
    (codex / "sessions" / "rollout-example.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows)
    )
    kiro = root / "kiro-sessions"
    folder = kiro / ("a" * 16) / ("sess_" + session_id)
    folder.mkdir(parents=True)
    (folder / "messages.jsonl").write_text(
        json.dumps(
            {
                "timestamp": stamp,
                "payload": {
                    "type": "usage_summary",
                    "executionId": "execution-one",
                    "promptTurnSummaries": [{"unit": "credit", "usage": 0.125}],
                },
            }
        )
        + "\n"
    )
    return codex, kiro


async def smoke_providers(root: Path) -> None:
    stamp = datetime.now(UTC).isoformat()
    codex_root, kiro_root = await asyncio.to_thread(_source_fixtures, root, stamp)
    codex = CodexProvider(CodexSettings(True, str(codex_root)))
    kiro = KiroProvider(KiroSettings(True, str(kiro_root)))

    class MailReader:
        async def call(self, operation: str) -> dict[str, Any]:
            return {
                "schema_version": 1,
                "status": "authorized",
                "headers": [
                    {
                        "id": "1",
                        "account_id": "example",
                        "mailbox_path": ["Inbox"],
                        "sender": "Synthetic sender",
                        "subject": "Synthetic subject",
                        "received": stamp,
                        "read": False,
                        "content": "PRIVATE_SMOKE_CANARY",
                    }
                ],
            }

    class Limits:
        async def read_limits(self) -> dict[str, Any]:
            return {
                "rateLimits": {
                    "limitId": "codex",
                    "primary": {
                        "usedPercent": 25.0,
                        "resetsAt": None,
                        "windowDurationMins": 300,
                    },
                }
            }

    def warp_response(request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            raise RuntimeError("Warp smoke transport must remain read-only.")
        return httpx.Response(
            200,
            json={
                "as_of": stamp,
                "team_uid": None,
                "page_info": {"has_next_page": False},
                "runs": [
                    {
                        "run_id": "run-one",
                        "root_run_id": "run-one",
                        "state": "SUCCEEDED",
                        "created_at": stamp,
                        "updated_at": stamp,
                        "prompt": "PRIVATE_SMOKE_CANARY",
                        "request_usage": {"total_tokens": 100, "inference_cost": 0.5},
                    }
                ],
            },
        )

    mail = MailProvider(
        MailSettings(True, (MailboxSelection("example", ("Inbox",)),)),
        AppPaths(root / "data", root / "cache"),
        MailReader(),
    )
    limits = CodexLimitsProvider(
        CodexSettings(False, str(codex_root), limits_enabled=True), transport=Limits()
    )
    warp = WarpProvider(
        WarpSettings(True),
        credential=lambda: "FAKE_SMOKE_KEY",
        transport=httpx.MockTransport(warp_response),
    )
    try:
        snapshots = [await provider.collect() for provider in (codex, kiro, mail, limits, warp)]
        if any("PRIVATE_SMOKE_CANARY" in repr(snapshot) for snapshot in snapshots):
            raise RuntimeError("Provider smoke test retained unallowlisted content.")
        observed = {
            snapshot.key.provider: {metric.id: metric.value for metric in snapshot.metrics}
            for snapshot in snapshots
        }
        if observed["codex-local"]["today-output"] != 4:
            raise RuntimeError("Installed Codex local accounting smoke failed.")
        if observed["kiro-local"]["today-credits"] != "0.125":
            raise RuntimeError("Installed Kiro recorded-credit smoke failed.")
        if observed["warp-runs"]["run-0-tokens"] != 100:
            raise RuntimeError("Installed Warp run-preview smoke failed.")
    finally:
        codex.close()
        kiro.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, dest="expected_version")
    args = parser.parse_args()
    asyncio.run(smoke(args.expected_version))
