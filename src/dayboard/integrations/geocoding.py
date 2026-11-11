"""Explicit location search only. No IP-based geolocation or implicit first result."""

import asyncio
import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from dayboard.core.providers import RateLimited
from dayboard.security.redaction import safe_text
from dayboard.settings import WeatherSettings

ENDPOINT = "https://geocoding-api.open-meteo.com/v1/search"
MAX_RESPONSE_BYTES = 262_144


@dataclass(frozen=True)
class LocationChoice:
    label: str
    latitude: float
    longitude: float
    timezone: str


def _label(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 200:
        raise ValueError("Invalid location label.")
    return safe_text(value).replace("\n", " ").replace("\t", " ").strip()


def parse_locations(payload: Any) -> tuple[LocationChoice, ...]:
    if not isinstance(payload, dict):
        raise ValueError("Invalid location search response.")
    results = payload.get("results", [])
    if not isinstance(results, list) or len(results) > 5:
        raise ValueError("Location search exceeded the requested result limit.")
    choices = []
    try:
        for result in results:
            if not isinstance(result, dict):
                raise ValueError("Invalid location result.")
            parts = dict.fromkeys(
                (
                    _label(result["name"]),
                    _label(result.get("admin1", "")),
                    _label(result.get("country", "")),
                )
            )
            label = ", ".join(part for part in parts if part)[:100]
            settings = WeatherSettings(
                True,
                result["latitude"],
                result["longitude"],
                label,
                result["timezone"],
            )
            choices.append(
                LocationChoice(
                    settings.label,
                    settings.latitude,
                    settings.longitude,
                    settings.timezone,
                )
            )
    except (KeyError, TypeError, OverflowError):
        raise ValueError("Location search returned incomplete results.") from None
    return tuple(choices)


class Geocoder:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.transport = transport
        self.clock = clock
        self._blocked_until = 0.0

    async def search(self, query: str) -> tuple[LocationChoice, ...]:
        query = query.strip()
        if not 2 <= len(query) <= 100 or any(
            ord(char) < 32 or 127 <= ord(char) < 160 for char in query
        ):
            raise ValueError("Enter a city name with 2–100 printable characters.")
        stamp = self.clock()
        if stamp < self._blocked_until:
            raise RateLimited(retry_after=self._blocked_until - stamp)
        async with (
            asyncio.timeout(10),
            httpx.AsyncClient(
                transport=self.transport,
                timeout=8,
                follow_redirects=False,
                trust_env=False,
                headers={"User-Agent": "Dayboard-location-search", "Accept-Encoding": "identity"},
            ) as client,
        ):
            async with client.stream(
                "GET",
                ENDPOINT,
                params={"name": query, "count": 5, "language": "en", "format": "json"},
            ) as response:
                if response.status_code == 429:
                    try:
                        delay = float(response.headers.get("retry-after", "60"))
                        if not math.isfinite(delay):
                            raise ValueError
                    except ValueError:
                        delay = 60
                    delay = max(0, delay)
                    self._blocked_until = self.clock() + delay
                    raise RateLimited(retry_after=delay)
                if response.status_code != 200:
                    raise ValueError("Location search is unavailable.")
                output = bytearray()
                async for chunk in response.aiter_bytes():
                    output.extend(chunk)
                    if len(output) > MAX_RESPONSE_BYTES:
                        raise ValueError("Location search exceeded its response size limit.")
        try:
            return parse_locations(json.loads(output))
        except (ValueError, RecursionError):
            raise ValueError("Location search returned invalid results.") from None
