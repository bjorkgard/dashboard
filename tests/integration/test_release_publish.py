"""Run the workflow's publishing shell against fake local gh/jq tools, never GitHub."""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMMIT = "1" * 40


@pytest.fixture
def publisher(tmp_path):
    workflow = (ROOT / ".github/workflows/release.yml").read_text()
    script = textwrap.dedent(workflow.split("        run: |\n", 1)[1])
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_gh = bin_dir / "gh"
    fake_gh.write_text(
        f"#!{sys.executable}\n"
        + textwrap.dedent("""
        import json
        import os
        import sys
        from pathlib import Path

        args = sys.argv[1:]
        if args[0] == "api":
            if "/git/ref/tags/" in args[1] and os.environ["ANNOTATED"] == "true":
                result = {"object": {"type": "tag", "sha": "2" * 40}}
            else:
                result = {"object": {"type": "commit", "sha": os.environ["REMOTE_COMMIT"]}}
            print(json.dumps(result))
        elif args[:2] == ["release", "create"]:
            Path(os.environ["CALL_LOG"]).write_text(json.dumps(args))
        else:
            raise SystemExit("Unexpected gh operation")
        """),
        encoding="utf-8",
    )
    fake_gh.chmod(0o700)
    fake_jq = bin_dir / "jq"
    fake_jq.write_text(
        f"#!{sys.executable}\n"
        + textwrap.dedent("""
        import json
        import sys

        data = json.load(sys.stdin)
        field = sys.argv[-1].split(".")[-1]
        print(data["object"][field])
        """),
        encoding="utf-8",
    )
    fake_jq.chmod(0o700)
    dist = tmp_path / "dist"
    dist.mkdir()
    for name in ("dayboard-1.2.3.whl", "dayboard-1.2.3.tar.gz", "SHA256SUMS", "RELEASE_NOTES.md"):
        (dist / name).write_text("Synthetic publishing fixture")
    env = {
        **os.environ,
        "PATH": str(bin_dir) + os.pathsep + os.defpath,
        "GH_REPO": "synthetic/dayboard",
        "RELEASE_TAG": "v1.2.3",
        "PACKAGE_VERSION": "1.2.3",
        "PRERELEASE": "false",
        "RELEASE_COMMIT": COMMIT,
        "REMOTE_COMMIT": COMMIT,
        "ANNOTATED": "false",
        "CALL_LOG": str(tmp_path / "gh-call.json"),
    }
    return tmp_path, script, env


@pytest.mark.parametrize("annotated", ["false", "true"])
@pytest.mark.parametrize("prerelease", ["false", "true"])
def test_publish_flags_and_lightweight_or_annotated_tag_resolution(
    publisher, annotated, prerelease
):
    directory, script, env = publisher
    env.update(ANNOTATED=annotated, PRERELEASE=prerelease)
    result = subprocess.run(
        ["/bin/bash", "-e", "-o", "pipefail", "-c", script],
        cwd=directory,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    args = json.loads(Path(env["CALL_LOG"]).read_text())
    assert args[:3] == ["release", "create", "v1.2.3"]
    assert "--verify-tag" in args
    assert "--generate-notes" in args
    assert "--notes-file" in args
    assert "--clobber" not in args
    assert ("--prerelease" in args) == (prerelease == "true")
    assert ("--latest=false" in args) == (prerelease == "true")
    assert "dist/SHA256SUMS" in args


def test_moved_tag_blocks_publishing(publisher):
    directory, script, env = publisher
    env["REMOTE_COMMIT"] = "3" * 40
    result = subprocess.run(
        ["/bin/bash", "-e", "-o", "pipefail", "-c", script],
        cwd=directory,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "no longer matches" in result.stderr
    assert not Path(env["CALL_LOG"]).exists()
