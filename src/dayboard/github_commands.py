"""Repository selection is nonsecret configuration, not a GitHub authentication flow."""

import argparse
import sys
from dataclasses import replace
from typing import Any

from dayboard.config import Config, ConfigError, ConfigStore
from dayboard.paths import AppPaths
from dayboard.settings import GitHubSettings


def add_github_commands(subparsers: Any) -> None:
    github = subparsers.add_parser("github", help="configure read-only GitHub.com status via gh")
    actions = github.add_subparsers(dest="action", required=True)
    select = actions.add_parser("set", help="select one to five repositories; no login or API call")
    select.add_argument("--repo", action="append", required=True, help="OWNER/REPO; repeatable")
    actions.add_parser("disable", help="stop collection; leave GitHub CLI credentials untouched")
    actions.add_parser(
        "status", help="show local configuration only; no connectivity or login test"
    )


def handle_github_setup(args: argparse.Namespace, paths: AppPaths) -> int:
    try:
        store = ConfigStore(paths)
        config = store.load() or Config()
        if args.action == "status":
            print("GitHub " + ("enabled" if config.github.enabled else "disabled"))
            for repository in config.github.repositories:
                print(f"  {repository}")
            print(
                "Local configuration only. Authenticate separately with "
                "'gh auth login --hostname github.com'."
            )
            return 0
        if args.action == "disable":
            settings = replace(config.github, enabled=False)
        else:
            settings = GitHubSettings(True, tuple(args.repo))
        store.save(replace(config, github=settings))
        print(
            "GitHub settings saved. Restart normal Dayboard to apply; "
            "gh retains credential ownership."
        )
        return 0
    except ConfigError as error:
        print(f"Invalid GitHub settings: {error}", file=sys.stderr)
        return 2
