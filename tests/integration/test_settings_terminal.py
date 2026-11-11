import json
import os
import select
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX pseudo-terminal integration")


@pytest.mark.parametrize("demo", [True, False], ids=["demo-cancel", "first-run-save"])
def test_settings_keyboard_flow_keeps_owned_application_running_until_explicit_quit(tmp_path, demo):
    master, slave = os.openpty()
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "TERM": "xterm-256color",
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "dayboard", *(["--demo"] if demo else [])],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        cwd=tmp_path,
        env=env,
        start_new_session=True,
    )
    os.close(slave)
    output = bytearray()
    phase, offset = "startup", 0
    returned_alive = False
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.05)[0]:
                try:
                    block = os.read(master, 65536)
                except OSError:
                    break
                if not block:
                    break
                output.extend(block)
            tail = output[offset:]
            marker = b"SYNTHETIC" if demo else b"FIRST RUN"
            if phase == "startup" and marker in tail and b"DAYBOARD" in output:
                os.write(master, b"s")
                phase, offset = "settings", len(output)
            elif phase == "settings" and b"INTEGRATION SETTINGS" in tail:
                os.write(master, b"\x1b" if demo else b"\x13")  # Esc / Ctrl-S
                phase, offset = "overview", len(output)
            elif phase == "overview" and b"DAYBOARD" in tail:
                returned_alive = process.poll() is None
                os.write(master, b"q")  # Only this owned test PTY, never the user's dashboard.
                phase = "quit"
            if process.poll() is not None:
                break
        assert returned_alive, "Settings did not return to a running overview."
        assert process.wait(timeout=3) == 0
        assert b"Traceback" not in output
        configs = list(tmp_path.rglob("config.json"))
        if demo:
            assert not configs
        else:
            assert len(configs) == 1
            config = json.loads(configs[0].read_text())
            assert config["onboarding_complete"]
            assert not any(config[name]["enabled"] for name in ("calendar", "weather", "github"))
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        os.close(master)
