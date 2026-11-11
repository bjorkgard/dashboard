import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest

from dayboard.core.models import (
    ConnectionState,
    Coverage,
    ErrorCode,
    Metric,
    ProviderKey,
    Quality,
    Snapshot,
)
from dayboard.core.store import CacheError, PublicCache, SnapshotStore
from dayboard.paths import AppPaths

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def snapshot(provider="weather", account=None, value=12, when=NOW):
    return Snapshot(
        key=ProviderKey(provider, account),
        observed_at=when,
        collected_at=when,
        metrics=(Metric("temperature", "Temperature", value, "°C"),),
        quality=Quality.REPORTED,
        coverage=Coverage.ACCOUNT,
    )


def test_snapshots_are_immutable_and_unknown_is_not_zero():
    reading = snapshot(value=None)
    assert reading.metrics[0].value is None
    with pytest.raises(FrozenInstanceError):
        reading.coverage = Coverage.LOCAL
    with pytest.raises(FrozenInstanceError):
        reading.metrics[0].value = 0


def test_mutable_metrics_and_naive_dates_are_rejected():
    with pytest.raises(ValueError):
        Snapshot(ProviderKey("weather"), NOW, NOW, [])
    with pytest.raises(ValueError):
        snapshot(when=datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        Metric("bad", "Bad", float("nan"))


def test_store_retains_last_good_on_failure_and_recovers():
    store = SnapshotStore()
    reading = snapshot()
    store.publish(reading)
    store.fail(reading.key, ErrorCode.TIMEOUT, NOW + timedelta(seconds=1))
    status = store.status(reading.key, now=NOW, stale_after=60)
    assert status.state == ConnectionState.STALE
    assert status.snapshot == reading
    assert status.error == ErrorCode.TIMEOUT
    store.publish(snapshot(value=14, when=NOW + timedelta(seconds=2)))
    assert store.status(reading.key, now=NOW, stale_after=60).state == ConnectionState.READY


def test_observation_age_not_collection_age_controls_freshness():
    store = SnapshotStore()
    store.publish(snapshot())
    status = store.status(ProviderKey("weather"), now=NOW + timedelta(seconds=61), stale_after=60)
    assert status.state == ConnectionState.STALE


def test_missing_sources_and_failures_without_data_are_distinct():
    store = SnapshotStore()
    key = ProviderKey("weather")
    assert store.status(key, now=NOW).state == ConnectionState.DISCONNECTED
    store.fail(key, ErrorCode.COLLECTION_FAILED, NOW)
    assert store.status(key, now=NOW).state == ConnectionState.ERROR
    assert store.status(key, now=NOW).snapshot is None


def test_histories_are_bounded_and_isolated_by_account():
    store = SnapshotStore(history_limit=3)
    for index in range(10):
        store.publish(snapshot(account="work", value=index, when=NOW + timedelta(seconds=index)))
    store.publish(snapshot(account="personal", value=99))
    assert len(store.history(ProviderKey("weather", "work"))) == 3
    assert store.history(ProviderKey("weather", "work"))[-1].metrics[0].value == 9
    assert store.history(ProviderKey("weather", "personal"))[0].metrics[0].value == 99


def test_out_of_order_results_do_not_replace_newer_readings():
    store = SnapshotStore()
    store.publish(snapshot(value=2, when=NOW + timedelta(seconds=2)))
    store.publish(snapshot(value=1))
    assert store.status(ProviderKey("weather"), now=NOW).snapshot.metrics[0].value == 2


def test_provider_count_is_bounded():
    store = SnapshotStore(max_sources=1)
    store.publish(snapshot("weather"))
    with pytest.raises(ValueError):
        store.publish(snapshot("news"))


def test_public_cache_round_trip_and_private_source_refusal(tmp_path):
    paths = AppPaths(tmp_path / "data", tmp_path / "cache")
    cache = PublicCache(paths)
    cache.save(snapshot())
    assert cache.load("weather") == snapshot()
    assert paths.cache_dir.stat().st_mode & 0o777 == 0o700
    assert (paths.cache_dir / "weather.json").stat().st_mode & 0o777 == 0o600
    with pytest.raises(CacheError):
        cache.save(snapshot("google", "work"))
    with pytest.raises(CacheError):
        cache.save(snapshot("weather", "work"))
    with pytest.raises(CacheError):
        cache.load("../credentials")


def test_cache_migration_and_corruption_do_not_overwrite_files(tmp_path):
    paths = AppPaths(tmp_path / "data", tmp_path / "cache")
    cache = PublicCache(paths)
    cache.save(snapshot())
    file = paths.cache_dir / "weather.json"
    payload = json.loads(file.read_text())
    payload["schema_version"] = 0
    file.write_text(json.dumps(payload))
    assert cache.load("weather") == snapshot()
    assert json.loads(file.read_text())["schema_version"] == 0
    file.write_text('{"schema_version":999,"secret":"SYNTHETIC_SECRET"}')
    with pytest.raises(CacheError) as error:
        cache.load("weather")
    assert "SYNTHETIC_SECRET" not in str(error.value)
    assert "999" in file.read_text()
