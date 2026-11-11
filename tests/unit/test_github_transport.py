import json
import sys

import pytest

from dayboard.core.models import ErrorCode
from dayboard.core.processes import run_bounded
from dayboard.core.providers import ProviderUnavailable
from dayboard.integrations.github.transport import GitHubCLI


async def test_cli_transport_uses_a_bounded_read_query_and_explicit_host(monkeypatch):
    import dayboard.integrations.github.transport as module

    async def fake_run(command, *, timeout, env, allowed_exit_codes):  # noqa: ASYNC109
        assert command[:3] == ["/fake/gh", "api", "graphql"]
        assert command[command.index("--hostname") + 1] == "github.com"
        assert command[command.index("--method") + 1] == "POST"
        assert "owner0=owner" in command
        assert "name0=repo" in command
        query = next(value for value in command if value.startswith("query="))
        assert "mutation" not in query
        assert "pullRequests(first: 10" in query
        assert "history(first: 5)" in query
        assert "messageHeadline" in query and "messageBody" not in query
        assert timeout == 15
        assert env["GH_PROMPT_DISABLED"] == "1"
        assert env["GH_DEBUG"] == ""
        assert allowed_exit_codes == (0, 1)
        return json.dumps({"data": {"r0": None}, "errors": [{"type": "NOT_FOUND"}]}).encode()

    monkeypatch.setattr(module, "run_bounded", fake_run)
    result = await GitHubCLI(executable="/fake/gh").fetch(("owner/repo",))
    assert "errors" in result


async def test_missing_cli_produces_safe_setup_error(monkeypatch):
    import dayboard.integrations.github.transport as module

    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    with pytest.raises(ProviderUnavailable) as error:
        await GitHubCLI().fetch(("owner/repo",))
    assert error.value.code == ErrorCode.GITHUB_SETUP_REQUIRED


async def test_empty_auth_failure_is_not_shown_as_an_empty_repository(monkeypatch):
    import dayboard.integrations.github.transport as module

    async def empty(*args, **kwargs):
        return b""

    monkeypatch.setattr(module, "run_bounded", empty)
    with pytest.raises(ProviderUnavailable):
        await GitHubCLI(executable="/fake/gh").fetch(("owner/repo",))


async def test_shared_process_runner_can_retain_json_on_allowed_exit_one():
    output = await run_bounded(
        [sys.executable, "-c", "import sys; print('{\"data\":null}'); sys.exit(1)"],
        timeout=2,
        allowed_exit_codes=(0, 1),
    )
    assert json.loads(output) == {"data": None}
