"""Exactly one fixed-shape GraphQL query per refresh, without extracting credentials."""

import json
import os
import shutil
from typing import Any

from dayboard.core.models import ErrorCode
from dayboard.core.processes import run_bounded
from dayboard.core.providers import ProviderUnavailable
from dayboard.settings import GitHubSettings

_FIELDS = """
  nameWithOwner
  pullRequests(first: 10, states: [OPEN], orderBy: {field: UPDATED_AT, direction: DESC}) {
    totalCount
    pageInfo { hasNextPage }
    nodes {
      number title url isDraft updatedAt reviewDecision mergeStateStatus
      author { login }
      commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
    }
  }
  defaultBranchRef {
    name
    target { ... on Commit {
      history(first: 5) { nodes {
        oid messageHeadline url committedDate author { name user { login } }
      } }
    } }
  }
"""


class GitHubCLI:
    def __init__(self, *, executable: str | None = None) -> None:
        self.executable = executable

    async def fetch(self, repositories: tuple[str, ...]) -> dict[str, Any]:
        GitHubSettings(True, repositories)
        executable = self.executable or shutil.which("gh")
        if executable is None:
            raise ProviderUnavailable(ErrorCode.GITHUB_SETUP_REQUIRED)
        definitions: list[str] = []
        selections: list[str] = []
        variables: list[str] = []
        for index, repository in enumerate(repositories):
            owner, name = repository.split("/")
            definitions.extend((f"$owner{index}: String!", f"$name{index}: String!"))
            selections.append(
                f"r{index}: repository(owner: $owner{index}, name: $name{index}) {{ {_FIELDS} }}"
            )
            variables.extend(("-f", f"owner{index}={owner}", "-f", f"name{index}={name}"))
        query = (
            "query("
            + ", ".join(definitions)
            + ") { "
            + " ".join(selections)
            + " rateLimit { remaining resetAt } }"
        )
        command = [
            executable,
            "api",
            "graphql",
            "--hostname",
            "github.com",
            "--method",
            "POST",
            "-f",
            f"query={query}",
            *variables,
        ]
        # Authentication is inherited/delegated to gh; its token is never read by Dayboard.
        env = dict(os.environ)
        env.update({"GH_PROMPT_DISABLED": "1", "GH_DEBUG": "", "GH_PAGER": "cat"})
        try:
            output = await run_bounded(
                command,
                timeout=15,
                env=env,
                allowed_exit_codes=(0, 1),
            )
        except (OSError, ProviderUnavailable):
            raise ProviderUnavailable(ErrorCode.GITHUB_SETUP_REQUIRED) from None
        if not output.strip():
            raise ProviderUnavailable(ErrorCode.GITHUB_SETUP_REQUIRED)
        try:
            data = json.loads(output)
            if not isinstance(data, dict):
                raise ValueError
            return data
        except (ValueError, TypeError, RecursionError):
            raise ValueError("Invalid GitHub response; no raw CLI output was retained.") from None
