"""Two bounded public cache files; private account readings cannot enter this API."""

import json
from dataclasses import asdict
from datetime import datetime
from typing import Any

from dayboard.core.models import Coverage, Metric, ProviderKey, Quality, Snapshot
from dayboard.paths import AppPaths, private_read, private_write


class CacheError(ValueError):
    pass


def _public(source: str) -> None:
    if source not in ("weather", "news"):
        raise CacheError("Only public weather and news may be cached.")


def _decode(payload: Any) -> Snapshot:
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "snapshot"}:
        raise ValueError("Invalid cache envelope.")
    version = payload["schema_version"]
    if type(version) is not int or version not in (0, 1):
        raise ValueError("Unsupported cache version.")
    data = payload["snapshot"]
    if not isinstance(data, dict):
        raise ValueError("Invalid snapshot.")
    data = data.copy()
    if version == 0:
        data.setdefault("quality", Quality.REPORTED.value)
        data.setdefault("coverage", Coverage.PARTIAL.value)
        data.setdefault("synthetic", False)
    if set(data) != {
        "key",
        "observed_at",
        "collected_at",
        "metrics",
        "quality",
        "coverage",
        "synthetic",
    }:
        raise ValueError("Unexpected snapshot fields.")
    if not isinstance(data["key"], dict) or set(data["key"]) != {"provider", "account"}:
        raise ValueError("Invalid source.")
    if not isinstance(data["metrics"], list) or len(data["metrics"]) > 256:
        raise ValueError("Invalid metrics.")
    metrics = []
    for metric in data["metrics"]:
        if not isinstance(metric, dict) or set(metric) != {"id", "label", "value", "unit"}:
            raise ValueError("Unexpected metric fields.")
        metrics.append(Metric(**metric))
    return Snapshot(
        ProviderKey(**data["key"]),
        datetime.fromisoformat(data["observed_at"]),
        datetime.fromisoformat(data["collected_at"]),
        tuple(metrics),
        Quality(data["quality"]),
        Coverage(data["coverage"]),
        data["synthetic"],
    )


class PublicCache:
    def __init__(self, paths: AppPaths) -> None:
        self.paths = paths

    def save(self, snapshot: Snapshot) -> None:
        _public(snapshot.key.provider)
        if snapshot.key.account is not None or snapshot.synthetic:
            raise CacheError("Private or synthetic readings cannot be persisted.")
        payload = asdict(snapshot)
        payload["observed_at"] = snapshot.observed_at.isoformat()
        payload["collected_at"] = snapshot.collected_at.isoformat()
        try:
            self.paths.ensure()
            private_write(
                self.paths.cache_dir / f"{snapshot.key.provider}.json",
                json.dumps({"schema_version": 1, "snapshot": payload}, allow_nan=False).encode(),
            )
        except (OSError, ValueError, TypeError):
            raise CacheError("Cannot save protected public cache.") from None

    def load(self, source: str) -> Snapshot | None:
        _public(source)
        try:
            raw = private_read(self.paths.cache_dir / f"{source}.json")
        except FileNotFoundError:
            return None
        except OSError:
            raise CacheError("Cannot read protected public cache.") from None
        try:
            result = _decode(json.loads(raw))
            if result.key != ProviderKey(source) or result.synthetic:
                raise ValueError("Source mismatch.")
            return result
        except (ValueError, TypeError, KeyError, RecursionError):
            raise CacheError("Invalid public cache; existing file was left unchanged.") from None
