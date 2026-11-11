from dayboard.calendar_setup import authorize_calendar, calendar_status
from dayboard.paths import AppPaths


async def test_status_without_helper_does_not_build_or_authorize(tmp_path, monkeypatch):
    import dayboard.calendar_setup as module

    async def forbidden(*args):
        raise AssertionError("Status must not build or request access.")

    monkeypatch.setattr(module, "build_bridge", forbidden)
    paths = AppPaths(tmp_path / "data", tmp_path / "cache")
    assert (await calendar_status(paths))["status"] == "not_built"
    assert not paths.data_dir.exists()


async def test_authorization_is_an_explicit_shared_operation(tmp_path, monkeypatch):
    import dayboard.calendar_setup as module

    calls = []

    async def build(paths):
        calls.append("build")
        return tmp_path / "fake"

    class Bridge:
        def __init__(self, path):
            pass

        async def call(self, operation):
            calls.append(operation)
            return {"schema_version": 1, "status": "authorized", "unexpected": "PRIVATE"}

    monkeypatch.setattr(module, "build_bridge", build)
    monkeypatch.setattr(module, "CalendarBridge", Bridge)
    result = await authorize_calendar(AppPaths(tmp_path / "data", tmp_path / "cache"))
    assert result == {"schema_version": 1, "status": "authorized"}
    assert calls == ["build", "authorize"]
