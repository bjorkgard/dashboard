"""Selected-repository overview, with bounded previews and no account-wide activity claim."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from dayboard.core.models import Coverage, Metric, ProviderKey, Quality, Snapshot
from dayboard.core.providers import Capability, ProviderSpec, RateLimited
from dayboard.integrations.github.models import parse_report, parse_timestamp
from dayboard.integrations.github.transport import GitHubCLI
from dayboard.settings import GitHubSettings


class GitHubReader(Protocol):
    async def fetch(self, repositories: tuple[str, ...]) -> dict[str, Any]: ...


class GitHubProvider:
    def __init__(
        self,
        settings: GitHubSettings,
        reader: GitHubReader | None = None,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not settings.enabled:
            raise ValueError("GitHub must be explicitly enabled.")
        self.settings = settings
        self.reader = reader if reader is not None else GitHubCLI()
        self.clock = clock
        self._not_before: datetime | None = None
        self.spec = ProviderSpec(
            ProviderKey("github"),
            "GitHub.com",
            (Capability.GITHUB,),
            interval_seconds=60,
            timeout_seconds=20,
        )

    async def collect(self) -> Snapshot:
        now = self.clock()
        if self._not_before is not None and now < self._not_before:
            raise RateLimited(retry_after=(self._not_before - now).total_seconds())
        payload = await self.reader.fetch(self.settings.repositories)
        errors = payload.get("errors", [])
        if not isinstance(errors, list) or not all(isinstance(error, dict) for error in errors):
            raise ValueError("Invalid GitHub error envelope.")
        data = payload.get("data")
        limit = data.get("rateLimit") if isinstance(data, dict) else None
        reset = None
        if isinstance(limit, dict) and limit.get("resetAt") is not None:
            reset = parse_timestamp(limit["resetAt"])
        if any(
            error.get("type") == "RATE_LIMITED"
            or (
                isinstance(error.get("extensions"), dict)
                and error["extensions"].get("code") == "RATE_LIMITED"
            )
            for error in errors
        ):
            raise RateLimited(retry_after=max(60, (reset - now).total_seconds()) if reset else 60)
        report = parse_report(payload, self.settings.repositories)
        if isinstance(limit, dict) and type(limit.get("remaining")) is int:
            if limit["remaining"] == 0 and reset is not None and reset > now:
                self._not_before = reset
        pull_requests = sorted(
            (pr for repo in report.repositories for pr in repo.pull_requests),
            key=lambda pr: pr.updated_at,
            reverse=True,
        )
        commits = sorted(
            (commit for repo in report.repositories for commit in repo.commits),
            key=lambda commit: commit.committed_at,
            reverse=True,
        )
        total = sum(repo.open_pr_count for repo in report.repositories)
        branches = " · ".join(
            f"{repo.name}:{repo.default_branch or '(empty)'}" for repo in report.repositories
        )
        metrics = [
            Metric("open-pr-count", "Open PRs (accessible repos)", total),
            Metric("branches", "Default branches", branches),
            Metric(
                "scope",
                "Scope",
                f"Preview: showing {min(5, len(pull_requests))} of {total} open PRs; "
                "up to 5 recent commits from bounded default-branch histories",
            ),
        ]
        if report.unavailable:
            metrics.append(
                Metric(
                    "unavailable",
                    "Repositories unavailable",
                    f"{len(report.unavailable)} of {len(self.settings.repositories)} "
                    "· access or name issue",
                )
            )
        if not pull_requests:
            metrics.append(
                Metric("no-prs", "Pull requests", "No open PRs in accessible repositories")
            )
        for index, pr in enumerate(pull_requests[:5]):
            metrics.append(
                Metric(
                    f"pr-{index}",
                    f"{pr.repository} #{pr.number}",
                    f"{pr.title} · {'DRAFT' if pr.draft else 'OPEN'} · @{pr.author} · "
                    f"checks {pr.checks} · review {pr.review} · merge {pr.merge_state}",
                )
            )
        if not commits:
            metrics.append(
                Metric("no-commits", "Recent commits", "No default-branch commits available")
            )
        for index, commit in enumerate(commits[:5]):
            when = commit.committed_at.astimezone().strftime("%d %b %H:%M")
            metrics.append(
                Metric(
                    f"commit-{index}",
                    f"{commit.repository} {commit.sha[:7]}",
                    f"{commit.title} · {commit.author} · {when}",
                )
            )
        now = self.clock()
        return Snapshot(
            self.spec.key,
            now,
            now,
            tuple(metrics),
            Quality.REPORTED,
            Coverage.PARTIAL,
        )
