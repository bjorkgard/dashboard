"""Immutable metadata only; no PR bodies, patches, email addresses or raw API errors."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from dayboard.core.models import ErrorCode
from dayboard.core.providers import ProviderUnavailable
from dayboard.security.redaction import safe_text
from dayboard.settings import GitHubSettings


@dataclass(frozen=True)
class PullRequest:
    repository: str
    number: int
    title: str
    url: str
    draft: bool
    updated_at: datetime
    author: str
    review: str
    checks: str
    merge_state: str


@dataclass(frozen=True)
class Commit:
    repository: str
    sha: str
    title: str
    url: str
    committed_at: datetime
    author: str


@dataclass(frozen=True)
class RepositoryStatus:
    name: str
    open_pr_count: int
    more_prs: bool
    default_branch: str | None
    pull_requests: tuple[PullRequest, ...]
    commits: tuple[Commit, ...]


@dataclass(frozen=True)
class GitHubReport:
    repositories: tuple[RepositoryStatus, ...]
    unavailable: tuple[str, ...]


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Invalid GitHub object.")
    return value


def _text(value: Any, *, limit: int = 160) -> str:
    if not isinstance(value, str) or len(value) > 4096:
        raise ValueError("Invalid GitHub text.")
    return safe_text(value).replace("\n", " ").replace("\t", " ")[:limit]


def parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("Invalid GitHub date.")
    try:
        date = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("Invalid GitHub date.") from None
    if date.utcoffset() is None:
        raise ValueError("GitHub dates must include a timezone.")
    return date.astimezone(UTC)


def _nodes(value: Any, limit: int) -> tuple[dict[str, Any], ...]:
    nodes = _object(value).get("nodes")
    if not isinstance(nodes, list) or len(nodes) > limit:
        raise ValueError("GitHub response exceeded its requested item limit.")
    return tuple(_object(node) for node in nodes)


def _url(value: Any, path: str) -> str:
    if not isinstance(value, str) or len(value) > 1024:
        raise ValueError("Invalid GitHub item URL.")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or parsed.path.casefold() != path.casefold()
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("GitHub item URL does not match its repository.")
    return value


def _author(value: Any) -> str:
    if value is None:
        return "unknown"
    data = _object(value)
    user = data.get("user")
    login = _object(user).get("login") if user is not None else data.get("login")
    return _text(login or data.get("name") or "unknown", limit=100)


def _state(value: Any, choices: tuple[str, ...]) -> str:
    return value if isinstance(value, str) and value in choices else "UNKNOWN"


def _pull_request(data: dict[str, Any], repository: str) -> PullRequest:
    number = data["number"]
    if type(number) is not int or number < 1 or type(data["isDraft"]) is not bool:
        raise ValueError("Invalid PR number or draft state.")
    checks = "UNKNOWN"
    commits = _nodes(data["commits"], 1)
    if commits:
        rollup = _object(commits[0]["commit"]).get("statusCheckRollup")
        if rollup is not None:
            checks = _state(
                _object(rollup).get("state"),
                (
                    "SUCCESS",
                    "FAILURE",
                    "ERROR",
                    "PENDING",
                    "EXPECTED",
                ),
            )
    return PullRequest(
        repository,
        number,
        _text(data["title"]),
        _url(data["url"], f"/{repository}/pull/{number}"),
        data["isDraft"],
        parse_timestamp(data["updatedAt"]),
        _author(data.get("author")),
        _state(data.get("reviewDecision"), ("APPROVED", "CHANGES_REQUESTED", "REVIEW_REQUIRED")),
        checks,
        _state(
            data.get("mergeStateStatus"),
            (
                "BLOCKED",
                "BEHIND",
                "CLEAN",
                "DIRTY",
                "DRAFT",
                "HAS_HOOKS",
                "UNSTABLE",
                "UNKNOWN",
            ),
        ),
    )


def _commit(data: dict[str, Any], repository: str) -> Commit:
    sha = data["oid"]
    if not isinstance(sha, str) or not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", sha):
        raise ValueError("Invalid GitHub commit identifier.")
    return Commit(
        repository,
        sha,
        _text(data["messageHeadline"]),
        _url(data["url"], f"/{repository}/commit/{sha}"),
        parse_timestamp(data["committedDate"]),
        _author(data.get("author")),
    )


def _repository(data: dict[str, Any], expected: str) -> RepositoryStatus:
    name = data["nameWithOwner"]
    GitHubSettings(True, (name,))
    if name.casefold() != expected.casefold():
        raise ValueError("GitHub returned an unexpected repository.")
    connection = _object(data["pullRequests"])
    count = connection["totalCount"]
    nodes = _nodes(connection, 10)
    if type(count) is not int or count < len(nodes):
        raise ValueError("Invalid GitHub PR count.")
    has_next = _object(connection["pageInfo"])["hasNextPage"]
    if type(has_next) is not bool:
        raise ValueError("Invalid GitHub pagination metadata.")
    pull_requests = tuple(_pull_request(node, name) for node in nodes)
    branch_name = None
    commits: tuple[Commit, ...] = ()
    branch = data.get("defaultBranchRef")
    if branch is not None:
        branch = _object(branch)
        branch_name = _text(branch["name"], limit=100)
        history = _object(branch["target"])["history"]
        commits = tuple(_commit(node, name) for node in _nodes(history, 5))
    return RepositoryStatus(
        name,
        count,
        has_next or count > len(nodes),
        branch_name,
        pull_requests,
        commits,
    )


def parse_report(payload: Any, repositories: tuple[str, ...]) -> GitHubReport:
    GitHubSettings(True, repositories)
    try:
        root = _object(payload)
        data = root.get("data")
        if not isinstance(data, dict):
            raise ProviderUnavailable(ErrorCode.GITHUB_SETUP_REQUIRED)
        results, unavailable = [], []
        for index, repository in enumerate(repositories):
            value = data.get(f"r{index}")
            if value is None:
                unavailable.append(repository)
            else:
                results.append(_repository(_object(value), repository))
        if not results:
            raise ProviderUnavailable(ErrorCode.GITHUB_SETUP_REQUIRED)
        return GitHubReport(tuple(results), tuple(unavailable))
    except (KeyError, TypeError, OverflowError):
        raise ValueError("Invalid GitHub metadata; no raw response was retained.") from None
