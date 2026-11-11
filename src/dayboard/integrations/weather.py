"""Open-Meteo model-derived weather, never automatic user geolocation."""

import json
import math
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from dayboard.core.models import Coverage, Metric, ProviderKey, Quality, Snapshot
from dayboard.core.providers import Capability, ProviderSpec, RateLimited
from dayboard.settings import WeatherSettings

ENDPOINT = "https://api.open-meteo.com/v1/forecast"
MAX_RESPONSE_BYTES = 262_144
CONDITIONS = {
    0: "Clear",
    1: "Mostly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Freezing fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    56: "Freezing drizzle",
    57: "Freezing drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    66: "Freezing rain",
    67: "Freezing rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Light showers",
    81: "Showers",
    82: "Heavy showers",
    85: "Snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Thunderstorm with hail",
}


def number(value: Any, *, minimum: float = -1000, maximum: float = 1000) -> float | None:
    if value is None:
        return None
    if (
        type(value) not in (int, float)
        or not math.isfinite(value)
        or not minimum <= value <= maximum
    ):
        raise ValueError("Invalid numeric weather reading.")
    return float(value)


def _timestamp(value: Any) -> datetime:
    epoch = number(value, minimum=0, maximum=32_503_680_000)
    if epoch is None:
        raise ValueError("Missing weather observation time.")
    return datetime.fromtimestamp(epoch, UTC)


def parse_weather(data: Any, settings: WeatherSettings, now: datetime) -> Snapshot:
    if not isinstance(data, dict) or not isinstance(data.get("current"), dict):
        raise ValueError("Weather response is missing current conditions.")
    current = data["current"]
    observed = _timestamp(current.get("time"))
    if not -900 <= (now - observed).total_seconds() <= 21_600:
        raise ValueError("Weather observation is outside the accepted time window.")
    unit = "°C" if settings.temperature_unit == "celsius" else "°F"
    wind_unit = "km/h" if settings.temperature_unit == "celsius" else "mph"
    reported_units = data.get("current_units", {})
    if not isinstance(reported_units, dict) or reported_units.get("temperature_2m", unit) != unit:
        raise ValueError("Weather service returned unexpected temperature units.")
    code = number(current.get("weather_code"), minimum=0, maximum=99)
    condition = CONDITIONS.get(
        int(code) if code is not None and code.is_integer() else -1,
        "Unknown conditions",
    )
    hourly = data.get("hourly", {})
    if not isinstance(hourly, dict):
        raise ValueError("Invalid hourly weather.")
    times = hourly.get("time", [])
    temperatures = hourly.get("temperature_2m", [])
    chances = hourly.get("precipitation_probability", [])
    if (
        not all(isinstance(values, list) for values in (times, temperatures, chances))
        or len(times) > 96
        or len(times) != len(temperatures)
        or len(times) != len(chances)
    ):
        raise ValueError("Invalid hourly weather arrays.")
    outlook: list[str] = []
    for stamp, temperature, chance in zip(times, temperatures, chances, strict=True):
        when = _timestamp(stamp)
        temperature = number(temperature)
        chance = number(chance, minimum=0, maximum=100)
        if when < now or len(outlook) >= 4:
            continue
        time = when.astimezone(ZoneInfo(settings.timezone)).strftime("%H:%M")
        temp = "?" if temperature is None else f"{temperature:g}"
        rain = "?" if chance is None else f"{chance:g}"
        outlook.append(f"{time} {temp}{unit} rain {rain}%")
    return Snapshot(
        ProviderKey("weather"),
        observed,
        now,
        (
            Metric("location", "Location", settings.label),
            Metric("temperature", condition, number(current.get("temperature_2m")), unit),
            Metric("feels", "Feels like", number(current.get("apparent_temperature")), unit),
            Metric(
                "humidity",
                "Humidity",
                number(current.get("relative_humidity_2m"), minimum=0, maximum=100),
                "%",
            ),
            Metric("wind", "Wind", number(current.get("wind_speed_10m"), minimum=0), wind_unit),
            Metric("outlook", "Next hours", " · ".join(outlook) or "Unavailable"),
            Metric("source", "Source", "Open-Meteo · model-derived · CC BY 4.0"),
        ),
        Quality.DERIVED,
        Coverage.PARTIAL,
    )


class WeatherProvider:
    def __init__(
        self,
        settings: WeatherSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not settings.enabled:
            raise ValueError("Weather must be explicitly enabled.")
        self.settings = settings
        self.transport = transport
        self.clock = clock
        self.spec = ProviderSpec(
            ProviderKey("weather"),
            "Open-Meteo",
            (Capability.WEATHER,),
            interval_seconds=900,
            timeout_seconds=15,
        )

    async def collect(self) -> Snapshot:
        params: dict[str, str | float | int] = {
            "latitude": self.settings.latitude,
            "longitude": self.settings.longitude,
            "current": (
                "temperature_2m,apparent_temperature,relative_humidity_2m,"
                "weather_code,wind_speed_10m"
            ),
            "hourly": "temperature_2m,precipitation_probability",
            "timezone": self.settings.timezone,
            "timeformat": "unixtime",
            "forecast_days": 3,
            "temperature_unit": self.settings.temperature_unit,
            "wind_speed_unit": "kmh" if self.settings.temperature_unit == "celsius" else "mph",
        }
        async with httpx.AsyncClient(
            transport=self.transport,
            timeout=10,
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": "Dayboard-weather", "Accept-Encoding": "identity"},
        ) as client:
            async with client.stream("GET", ENDPOINT, params=params) as response:
                if response.status_code == 429:
                    retry = response.headers.get("retry-after", "60")
                    try:
                        delay = float(retry)
                        if not math.isfinite(delay):
                            raise ValueError
                    except ValueError:
                        try:
                            delay = (parsedate_to_datetime(retry) - self.clock()).total_seconds()
                        except (TypeError, ValueError, OverflowError):
                            delay = 60
                    raise RateLimited(retry_after=max(0, delay))
                if response.status_code != 200:
                    raise ValueError("Weather service did not return a successful reading.")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise ValueError("Weather response exceeded its size limit.")
        try:
            data = json.loads(body)
            return parse_weather(data, self.settings, self.clock())
        except (KeyError, TypeError, OverflowError, RecursionError):
            raise ValueError("Invalid weather response.") from None
