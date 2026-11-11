from copy import deepcopy
from datetime import UTC, datetime

import pytest

from dayboard.core.models import ErrorCode, Quality
from dayboard.core.providers import ProviderUnavailable, RateLimited
from dayboard.integrations.github.models import parse_report
from dayboard.integrations.github.provider import GitHubProvider
from dayboard.settings import GitHubSettings

NOW = datetime(2026, 11, 11, 12, tzinfo=UTC)
REPOSITORY = "owner/repo"
SHA = "a" * 40


def repository():
    return {
        "nameWithOwner": REPOSITORY,
        "pullRequests": {
            "totalCount": 1,
            "pageInfo": {"hasNextPage": False},
            "nodes": [
                {
                    "number": 7,
                    "title": "Synthetic PR\x1b[31m [bold]",
                    "url": f"https://github.com/{REPOSITORY}/pull/7",
                    "isDraft": True,
                    "updatedAt": "2026-11-11T11:00:00Z",
                    "author": {"login": "example"},
                    "reviewDecision": "REVIEW_REQUIRED",
                    "mergeStateStatus": "DRAFT",
                    "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS"}}}]},
                }
            ],
        },
        "defaultBranchRef": {
            "name": "main",
            "target": {
                "history": {
                    "nodes": [
                        {
                            "oid": SHA,
                            "messageHeadline": "Synthetic commit",
                            "url": f"https://github.com/{REPOSITORY}/commit/{SHA}",
                            "committedDate": "2026-11-11T10:00:00Z",
                            "author": {"name": "Example", "user": {"login": "example"}},
                        }
                    ]
                }
            },
        },
    }


def response():
    return {
        "data": {
            "r0": repository(),
            "rateLimit": {"remaining": 100, "resetAt": "2026-11-11T13:00:00Z"},
        }
    }


class Reader:
    def __init__(self, data):
        self.data = data
        self.calls = 0

    async def fetch(self, repositories):
        assert repositories == (REPOSITORY,)
        self.calls += 1
        return deepcopy(self.data)


def test_report_keeps_pr_state_and_default_branch_commits_as_inert_metadata():
    report = parse_report(response(), (REPOSITORY,))
    repo = report.repositories[0]
    assert repo.open_pr_count == 1
    assert repo.default_branch == "main"
    assert repo.pull_requests[0].draft
    assert repo.pull_requests[0].checks == "SUCCESS"
    assert repo.pull_requests[0].review == "REVIEW_REQUIRED"
    assert "\x1b" not in repo.pull_requests[0].title
    assert "[bold]" in repo.pull_requests[0].title
    assert repo.commits[0].sha == SHA
    assert repo.commits[0].committed_at.utcoffset().total_seconds() == 0


def test_missing_checks_and_review_are_unknown_not_success():
    data = response()
    pr = data["data"]["r0"]["pullRequests"]["nodes"][0]
    pr["reviewDecision"] = None
    pr["commits"]["nodes"] = []
    pr["author"] = None
    normalized = parse_report(data, (REPOSITORY,)).repositories[0].pull_requests[0]
    assert normalized.checks == "UNKNOWN"
    assert normalized.review == "UNKNOWN"
    assert normalized.author == "unknown"


def test_empty_repository_does_not_fail_or_invent_commits():
    data = response()
    data["data"]["r0"]["defaultBranchRef"] = None
    data["data"]["r0"]["pullRequests"].update(totalCount=0, nodes=[])
    repo = parse_report(data, (REPOSITORY,)).repositories[0]
    assert repo.open_pr_count == 0
    assert not repo.commits


def test_partial_repository_access_is_explicit_and_keeps_accessible_data():
    data = response()
    data["data"]["r1"] = None
    data["errors"] = [{"type": "NOT_FOUND", "message": "SYNTHETIC_PRIVATE_DETAILS"}]
    report = parse_report(data, (REPOSITORY, "owner/private"))
    assert len(report.repositories) == 1
    assert report.unavailable == ("owner/private",)
    assert "SYNTHETIC_PRIVATE_DETAILS" not in repr(report)


def test_total_access_failure_is_not_zero_prs():
    with pytest.raises(ProviderUnavailable) as error:
        parse_report({"data": {"r0": None}}, (REPOSITORY,))
    assert error.value.code == ErrorCode.GITHUB_SETUP_REQUIRED


def test_malformed_date_error_does_not_echo_raw_api_text():
    data = response()
    data["data"]["r0"]["pullRequests"]["nodes"][0]["updatedAt"] = "SYNTHETIC_PRIVATE_VALUE"
    with pytest.raises(ValueError) as error:
        parse_report(data, (REPOSITORY,))
    assert "SYNTHETIC_PRIVATE_VALUE" not in str(error.value)


@pytest.mark.parametrize("kind", ["url", "date", "number", "count", "oversized", "wrong-repo"])
def test_malformed_or_unsafe_metadata_is_rejected(kind):
    data = response()
    repo = data["data"]["r0"]
    pr = repo["pullRequests"]["nodes"][0]
    if kind == "url":
        pr["url"] = "javascript:alert(1)"
    elif kind == "date":
        pr["updatedAt"] = "2026-11-11T11:00:00"
    elif kind == "number":
        pr["number"] = True
    elif kind == "count":
        repo["pullRequests"]["totalCount"] = -1
    elif kind == "oversized":
        repo["pullRequests"]["nodes"] *= 11
    else:
        repo["nameWithOwner"] = "someone/else"
    with pytest.raises(ValueError):
        parse_report(data, (REPOSITORY,))


async def test_provider_shows_prs_commits_and_truthful_total_count():
    reader = Reader(response())
    provider = GitHubProvider(GitHubSettings(True, (REPOSITORY,)), reader, clock=lambda: NOW)
    snapshot = await provider.collect()
    assert snapshot.quality == Quality.REPORTED
    assert snapshot.key.provider == "github"
    assert provider.spec.interval_seconds == 60
    rendered = "\n".join(f"{m.label}: {m.value}" for m in snapshot.metrics)
    assert "#7" in rendered and "DRAFT" in rendered and "SUCCESS" in rendered
    assert "Synthetic commit" in rendered
    assert "main" in rendered
    assert "SYNTHETIC_PRIVATE_DETAILS" not in rendered


async def test_exhausted_rate_limit_keeps_successful_snapshot_then_defers_more_requests():
    data = response()
    data["data"]["rateLimit"]["remaining"] = 0
    reader = Reader(data)
    provider = GitHubProvider(GitHubSettings(True, (REPOSITORY,)), reader, clock=lambda: NOW)
    assert (await provider.collect()).metrics
    with pytest.raises(RateLimited) as error:
        await provider.collect()
    assert error.value.retry_after == 3600
    assert reader.calls == 1


async def test_api_rate_limit_error_is_not_auth_failure():
    reader = Reader({"data": None, "errors": [{"type": "RATE_LIMITED", "message": "PRIVATE"}]})
    provider = GitHubProvider(GitHubSettings(True, (REPOSITORY,)), reader, clock=lambda: NOW)
    with pytest.raises(RateLimited):
        await provider.collect()


async def test_preview_never_claims_its_rows_are_the_full_server_count():
    data = response()
    repo = data["data"]["r0"]
    repo["pullRequests"]["totalCount"] = 20
    repo["pullRequests"]["pageInfo"]["hasNextPage"] = True
    provider = GitHubProvider(GitHubSettings(True, (REPOSITORY,)), Reader(data), clock=lambda: NOW)
    snapshot = await provider.collect()
    assert next(m.value for m in snapshot.metrics if m.id == "open-pr-count") == 20
    assert "preview" in str(next(m.value for m in snapshot.metrics if m.id == "scope")).lower()
