import asyncio
import os
import signal
import sys
import time

import psutil
import pytest

from dayboard.core.processes import run_bounded


@pytest.mark.skipif(os.name != "posix", reason="Owned POSIX process-group cleanup")
async def test_timeout_stops_descendants_not_only_the_direct_process(tmp_path):
    child_pid = tmp_path / "child-pid"
    child = f"import os,time; open({str(child_pid)!r},'w').write(str(os.getpid())); time.sleep(8)"
    parent = (
        "import subprocess,sys; "
        f"process=subprocess.Popen([sys.executable,'-c',{child!r}]); process.wait()"
    )
    pid = None
    alive = True
    try:
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            await run_bounded([sys.executable, "-c", parent], timeout=0.5)
        pid = int(child_pid.read_text())
        assert time.monotonic() - started < 3, "Process cleanup exceeded its cancellation budget."
        for _ in range(100):
            try:
                process = psutil.Process(pid)
                alive = process.is_running() and process.status() != psutil.STATUS_ZOMBIE
            except psutil.NoSuchProcess:
                alive = False
            if not alive:
                break
            await asyncio.sleep(0.01)
    finally:
        if pid is not None and alive:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    assert not alive, "The cancelled operation left an owned descendant running."
