import asyncio

import httpx
import pytest

from dayboard.core.providers import RateLimited
from dayboard.integrations.geocoding import Geocoder


def results():
    return {
        "results": [
            {
                "name": "Springfield",
                "admin1": "Illinois",
                "country": "United States",
                "latitude": 39.8,
                "longitude": -89.6,
                "timezone": "America/Chicago",
            },
            {
                "name": "Springfield",
                "admin1": "Massachusetts",
                "country": "United States",
                "latitude": 42.1,
                "longitude": -72.6,
                "timezone": "America/New_York",
            },
        ]
    }


async def test_search_is_fixed_https_bounded_and_keeps_disambiguation():
    def handler(request):
        assert request.url.host == "geocoding-api.open-meteo.com"
        assert request.url.scheme == "https"
        assert request.url.params["name"] == "Springfield"
        assert request.url.params["count"] == "5"
        return httpx.Response(200, json=results())

    choices = await Geocoder(transport=httpx.MockTransport(handler)).search("Springfield")
    assert len(choices) == 2
    assert "Illinois" in choices[0].label
    assert "Massachusetts" in choices[1].label
    assert choices[0].timezone == "America/Chicago"


async def test_empty_results_are_a_valid_empty_choice_list():
    geocoder = Geocoder(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    assert await geocoder.search("NoSuchCity") == ()


@pytest.mark.parametrize("query", ["", "x", "x" * 101, "city\x1b[31m"])
async def test_invalid_queries_never_make_requests(query):
    def forbidden(request):
        raise AssertionError("Invalid query must not be sent.")

    with pytest.raises(ValueError):
        await Geocoder(transport=httpx.MockTransport(forbidden)).search(query)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(302, headers={"location": "http://unsafe.invalid"}),
        httpx.Response(200, content=b"x" * 300000),
        httpx.Response(200, json={"results": [{}]}),
        httpx.Response(200, json={"results": results()["results"] * 3}),
        httpx.Response(200, content=b"not json"),
    ],
)
async def test_unsafe_or_invalid_responses_fail_closed(response):
    with pytest.raises(ValueError):
        await Geocoder(transport=httpx.MockTransport(lambda r: response)).search("Paris")


async def test_rate_limit_does_not_retry_automatically():
    geocoder = Geocoder(
        transport=httpx.MockTransport(lambda r: httpx.Response(429, headers={"retry-after": "120"}))
    )
    with pytest.raises(RateLimited) as error:
        await geocoder.search("Paris")
    assert error.value.retry_after == 120


async def test_cancellation_cancels_the_inflight_lookup():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def handler(request):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    task = asyncio.create_task(Geocoder(transport=httpx.MockTransport(handler)).search("Paris"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


async def test_reported_rate_limit_prevents_immediate_repeat_requests():
    calls = []
    stamp = [100.0]

    def handler(request):
        calls.append("request")
        return httpx.Response(429, headers={"retry-after": "120"})

    geocoder = Geocoder(transport=httpx.MockTransport(handler), clock=lambda: stamp[0])
    with pytest.raises(RateLimited):
        await geocoder.search("Paris")
    stamp[0] = 110
    with pytest.raises(RateLimited) as error:
        await geocoder.search("Berlin")
    assert error.value.retry_after == 110
    assert len(calls) == 1
