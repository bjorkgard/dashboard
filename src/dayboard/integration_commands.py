"""Explicit user setup only; none of these actions run during dashboard startup."""

import argparse
import asyncio
import sys
from dataclasses import replace
from typing import Any

from dayboard.calendar_setup import authorize_calendar, calendar_status
from dayboard.config import Config, ConfigError, ConfigStore
from dayboard.core.providers import ProviderUnavailable
from dayboard.github_commands import add_github_commands, handle_github_setup
from dayboard.integrations.native import bridge_path
from dayboard.paths import AppPaths
from dayboard.settings import CalendarSettings, WeatherSettings


def add_setup_commands(subparsers: Any) -> None:
    add_github_commands(subparsers)
    weather = subparsers.add_parser("weather", help="configure opt-in Open-Meteo weather")
    actions = weather.add_subparsers(dest="action", required=True)
    location = actions.add_parser(
        "set", help="save coordinates; starts requests on next normal launch"
    )
    location.add_argument("--latitude", type=float, required=True)
    location.add_argument("--longitude", type=float, required=True)
    location.add_argument("--name", required=True, help="location display label")
    location.add_argument("--timezone", default="UTC", help="IANA timezone, e.g. Europe/Stockholm")
    location.add_argument("--units", choices=["celsius", "fahrenheit"], default="celsius")
    actions.add_parser("disable", help="stop weather collection")
    calendar = subparsers.add_parser("calendar", help="configure read-only macOS Calendar")
    actions = calendar.add_subparsers(dest="action", required=True)
    authorize = actions.add_parser(
        "authorize", help="build helper, request Calendar access and enable"
    )
    authorize.add_argument(
        "--calendar-id", action="append", help="optional calendar filter; repeatable"
    )
    actions.add_parser("status", help="read permission status only; never builds or prompts")
    actions.add_parser("disable", help="stop event collection without revoking macOS permissions")


def handle_setup(args: argparse.Namespace, paths: AppPaths) -> int:
    if args.command == "github":
        return handle_github_setup(args, paths)
    try:
        store = ConfigStore(paths)
        config = store.load() or Config()
        if args.command == "weather":
            if args.action == "disable":
                weather = replace(config.weather, enabled=False)
            else:
                weather = WeatherSettings(
                    True,
                    args.latitude,
                    args.longitude,
                    args.name,
                    args.timezone,
                    args.units,
                )
            store.save(replace(config, weather=weather))
            print("Weather settings saved. Restart normal Dayboard to apply; demo stays isolated.")
            return 0
        if args.action == "disable":
            store.save(replace(config, calendar=replace(config.calendar, enabled=False)))
            print("Calendar collection disabled. macOS permission grants were left unchanged.")
            return 0
        if args.action == "status":
            if not bridge_path(paths).exists():
                print("Calendar helper not built. Run 'dayboard calendar authorize' to set up.")
                return 1
            result = asyncio.run(calendar_status(paths))
        else:
            calendar = CalendarSettings(
                True,
                tuple(args.calendar_id) if args.calendar_id else config.calendar.calendar_ids,
            )
            result = asyncio.run(authorize_calendar(paths))
            if result.get("status") == "authorized":
                store.save(replace(config, calendar=calendar))
        status = result.get("status")
        if status == "authorized":
            print("Calendar access authorized. Restart normal Dayboard to collect upcoming events.")
            return 0
        print(
            "Calendar access denied or not granted. Review Calendar privacy permissions for your "
            "terminal/host app. Dayboard does not bypass host permission restrictions."
        )
        return 1
    except ConfigError as error:
        print(f"Invalid integration settings: {error}", file=sys.stderr)
        return 2
    except (OSError, ValueError, TimeoutError, ProviderUnavailable):
        print(
            "Calendar helper unavailable or timed out. Requires macOS 14+ and Swift Command Line "
            "Tools (xcode-select --install). No new calendar configuration was enabled.",
            file=sys.stderr,
        )
        return 1
