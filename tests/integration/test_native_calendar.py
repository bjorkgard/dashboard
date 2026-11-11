import shutil
import sys

import pytest

from dayboard.integrations.native import CalendarBridge, build_bridge
from dayboard.paths import AppPaths


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("xcrun") is None,
    reason="Native calendar compilation requires macOS and Swift Command Line Tools.",
)
async def test_native_source_compiles_and_checks_only_permission_status(tmp_path):
    paths = AppPaths(tmp_path / "data", tmp_path / "cache")
    executable = await build_bridge(paths)
    result = await CalendarBridge(executable).call("status")
    assert result["status"] in {
        "not_determined",
        "denied",
        "restricted",
        "authorized",
        "unavailable",
    }
    assert "events" not in result
    assert executable.stat().st_mode & 0o777 == 0o700
