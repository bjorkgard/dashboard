from datetime import UTC, datetime

import httpx
import pytest

from dayboard.core.models import Quality
from dayboard.core.providers import RateLimited
from dayboard.integrations.weather import WeatherProvider
from dayboard.settings import WeatherSettings

NOW = datetime(2026, 11, 11, 12, tzinfo=UTC)
SETTINGS = WeatherSettings(True, 59.3, 18.1, "Example", "Europe/Stockholm")


def payload():
    return {
        "current": {
            "time": NOW.timestamp(),
            "temperature_2m": 12.5,
            "apparent_temperature": 10,
            "relative_humidity_2m": 70,
            "weather_code": 2,
            "wind_speed_10m": 8,
        },
        "hourly": {
            "time": [NOW.timestamp(), NOW.timestamp() + 3600],
            "temperature_2m": [12.5, 13],
            "precipitation_probability": [10, 30],
        },
    }


async def test_weather_requests_only_fixed_https_host_and_reports_model_observation():
    def handler(request):
        assert request.url.host == "api.open-meteo.com"
        assert request.url.scheme == "https"
        assert request.url.params["latitude"] == "59.3"
        assert request.url.params["timeformat"] == "unixtime"
        return httpx.Response(200, json=payload())

    provider = WeatherProvider(SETTINGS, transport=httpx.MockTransport(handler), clock=lambda: NOW)
    reading = await provider.collect()
    assert reading.observed_at == NOW
    assert reading.quality == Quality.DERIVED
    values = {metric.id: metric for metric in reading.metrics}
    assert values["temperature"].value == 12.5
    assert values["temperature"].unit == "°C"
    assert "Open-Meteo" in values["source"].value
    assert "13:00" in values["outlook"].value  # configured local timezone
    assert provider.spec.interval_seconds == 900


async def test_null_weather_values_stay_unknown():
    data = payload()
    data["current"]["temperature_2m"] = None
    provider = WeatherProvider(
        SETTINGS,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=data)),
        clock=lambda: NOW,
    )
    reading = await provider.collect()
    assert next(m.value for m in reading.metrics if m.id == "temperature") is None


async def test_fahrenheit_is_requested_not_relabelled():
    def handler(request):
        assert request.url.params["temperature_unit"] == "fahrenheit"
        assert request.url.params["wind_speed_unit"] == "mph"
        return httpx.Response(200, json=payload())

    config = WeatherSettings(True, 1, 2, "Example", "UTC", "fahrenheit")
    reading = await WeatherProvider(
        config, transport=httpx.MockTransport(handler), clock=lambda: NOW
    ).collect()
    assert next(m.unit for m in reading.metrics if m.id == "temperature") == "°F"


async def test_rate_limit_respects_retry_after():
    provider = WeatherProvider(
        SETTINGS,
        transport=httpx.MockTransport(
            lambda r: httpx.Response(429, headers={"retry-after": "120"})
        ),
    )
    with pytest.raises(RateLimited) as error:
        await provider.collect()
    assert error.value.retry_after == 120


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(302, headers={"location": "http://unsafe.invalid"}),
        httpx.Response(200, content=b"x" * 300_000),
        httpx.Response(200, json={"current": {"time": "not a timestamp"}}),
        httpx.Response(200, content=b'{"current":{"time":NaN}}'),
    ],
)
async def test_redirects_oversized_or_malformed_weather_are_rejected(response):
    provider = WeatherProvider(SETTINGS, transport=httpx.MockTransport(lambda r: response))
    with pytest.raises(ValueError):
        await provider.collect()


def test_disabled_settings_cannot_create_collector():
    with pytest.raises(ValueError):
        WeatherProvider(WeatherSettings())


async def test_reported_temperature_unit_must_match_requested_unit():
    data = payload()
    data["current_units"] = {"temperature_2m": "°F"}
    provider = WeatherProvider(
        SETTINGS,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=data)),
        clock=lambda: NOW,
    )
    with pytest.raises(ValueError):
        await provider.collect()
