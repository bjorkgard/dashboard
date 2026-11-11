"""Exercise the installed entry point through a PTY, with a temporary home."""

import os
import select
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="macOS/POSIX terminal smoke test")


@pytest.mark.parametrize(
    "arguments,marker",
    [(["--demo"], b"SYNTHETIC"), ([], b"FIRST RUN")],
    ids=["synthetic-demo", "first-run-onboarding"],
)
def test_terminal_starts_renders_and_quits_without_persisting_state(tmp_path, arguments, marker):
    master, slave = os.openpty()
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "TERM": "xterm-256color",
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "dayboard", *arguments],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        cwd=tmp_path,
        env=env,
        start_new_session=True,
    )
    os.close(slave)
    output = bytearray()
    quit_sent = False
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.05)[0]:
                try:
                    block = os.read(master, 65536)
                except OSError:
                    break
                if not block:
                    break
                output.extend(block)
            if marker in output and b"DAYBOARD" in output and not quit_sent:
                os.write(master, b"q")
                quit_sent = True
            if process.poll() is not None:
                break
        assert quit_sent, "The application did not render its expected startup screen."
        assert process.wait(timeout=3) == 0
        assert b"Traceback" not in output
        assert not list(tmp_path.iterdir()), "Demo or quit-without-saving wrote user state."
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        os.close(master)
