import argparse
import json
import sys
from importlib.metadata import version

from dayboard.app import DayboardApp
from dayboard.config import ConfigError
from dayboard.diagnostics import diagnose
from dayboard.paths import AppPaths
from dayboard.security.redaction import redact


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dayboard",
        description="Dayboard — a local-first macOS terminal workspace (milestone 1).",
        epilog=(
            "Run without arguments for first-run setup; use --demo for an isolated synthetic "
            "preview. Press q to quit. Accounts, telemetry, tabs and news connections arrive "
            "in later milestones. Credentials use macOS Keychain only; private state is kept "
            "outside the project in the operating system's Dayboard application directories."
        ),
    )
    parser.add_argument("--version", action="version", version=f"dayboard {version('dayboard')}")
    parser.add_argument(
        "--demo", action="store_true", help="synthetic preview; no account/state access"
    )
    subparsers = parser.add_subparsers(dest="command")
    doctor = subparsers.add_parser(
        "doctor", help="allowlisted diagnostics; never reads credentials"
    )
    doctor.add_argument("--json", action="store_true", help="emit machine-readable diagnostics")
    args = parser.parse_args(argv)
    if args.command == "doctor":
        if args.demo:
            parser.error("--demo cannot be combined with doctor.")
        report, healthy = diagnose(AppPaths.default())
        report = redact(report)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print("DAYBOARD / DIAGNOSTICS\n")
            for key, value in report.items():
                print(f"{key.replace('_', ' ')}: {value}")
        return 0 if healthy else 1
    try:
        app = DayboardApp(demo=args.demo)
    except ConfigError:
        print(
            "Dayboard: cannot load configuration. Existing state was left unchanged. "
            "Run 'dayboard doctor' for safe diagnostics, or '--demo' for an isolated preview.",
            file=sys.stderr,
        )
        return 2
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print(
            "Dayboard needs an interactive terminal. Use 'dayboard doctor --json' for diagnostics.",
            file=sys.stderr,
        )
        return 2
    try:
        app.run()
    except KeyboardInterrupt:
        return 130
    return 0
