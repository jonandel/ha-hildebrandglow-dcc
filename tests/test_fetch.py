"""Tests for Glowmarkt fetching: chunking, parameters, retries and back-off."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import requests

from custom_components.hildebrandglow_dcc import hourly_statistics
from custom_components.hildebrandglow_dcc.hourly_statistics import (
    GlowmarktApiError,
    async_fetch_hourly,
)
from homeassistant.core import HomeAssistant

UTC = timezone.utc
START = datetime(2026, 11, 1, tzinfo=UTC)


class FakeResponse:
    """Minimal requests.Response stand-in."""

    def __init__(self, status=200, body=None, headers=None):
        """Store the canned response."""
        self.status_code = status
        self._body = body if body is not None else {"units": "kWh", "data": []}
        self.headers = headers or {}

    def json(self):
        """Return the body."""
        return self._body


class FakeSession:
    """Returns queued responses (or raises queued exceptions) in order."""

    def __init__(self, responses):
        """Queue responses."""
        self.responses = list(responses)
        self.calls = []

    def get(self, url, headers=None, params=None, timeout=None):
        """Record the call and pop the next response."""
        self.calls.append({"url": url, "headers": headers, "params": params})
        item = self.responses.pop(0) if self.responses else FakeResponse()
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    """Looks like pyglowmarkt.BrightClient to the fetch code."""

    url = "https://api.glowmarkt.com/api/v0-1/"
    application = "app-id"

    def __init__(self, responses):
        """Use a fake session."""
        self.session = FakeSession(responses)
        self.token = "old-token"
        self.auth_calls = 0

    def authenticate(self):
        """Pretend to log in again."""
        self.auth_calls += 1
        return "new-token"


@pytest.fixture
def sleeps():
    """Record back-off sleeps instead of waiting."""
    delays = []

    async def fake_sleep(delay):
        delays.append(delay)

    with patch.object(hourly_statistics.asyncio, "sleep", fake_sleep):
        yield delays


def ok(data, units="kWh"):
    """A 200 response with readings."""
    return FakeResponse(200, {"units": units, "data": data})


async def test_chunks_and_params(hass: HomeAssistant, sleeps):
    """Long ranges are split into 10-day chunks with an inclusive 'to'."""
    client = FakeClient([ok([[int(START.timestamp()), 1.5]]), ok([]), ok([])])
    end = START + timedelta(days=25)
    readings, units = await async_fetch_hourly(hass, client, "res-1", START, end)

    params = [call["params"] for call in client.session.calls]
    assert [(p["from"], p["to"]) for p in params] == [
        ("2026-11-01T00:00:00", "2026-11-10T23:30:00"),
        ("2026-11-11T00:00:00", "2026-11-20T23:30:00"),
        ("2026-11-21T00:00:00", "2026-11-25T23:30:00"),
    ]
    assert all(p["period"] == "PT1H" and p["offset"] == 0 for p in params)
    assert all(p["nulls"] == 1 and p["function"] == "sum" for p in params)
    assert client.session.calls[0]["url"].endswith("resource/res-1/readings")
    assert client.session.calls[0]["headers"]["token"] == "old-token"
    assert readings == [(START, 1.5)]
    assert units == "kWh"
    # Polite spacing between chunk requests, none before the first.
    assert sleeps == [hourly_statistics.REQUEST_SPACING] * 2


async def test_nulls_are_kept(hass: HomeAssistant, sleeps):
    """None (not yet available) is passed through, distinct from zero."""
    t = int(START.timestamp())
    client = FakeClient([ok([[t, 0], [t + 3600, None]])])
    readings, _ = await async_fetch_hourly(
        hass, client, "r", START, START + timedelta(hours=2)
    )
    assert readings == [(START, 0), (START + timedelta(hours=1), None)]


async def test_429_backs_off_and_honours_retry_after(hass: HomeAssistant, sleeps):
    """A 429 is retried after the longer of our delay and Retry-After."""
    client = FakeClient([FakeResponse(429, headers={"Retry-After": "45"}), ok([])])
    await async_fetch_hourly(hass, client, "r", START, START + timedelta(hours=1))
    assert sleeps == [45]
    assert len(client.session.calls) == 2


async def test_5xx_retries_then_raises(hass: HomeAssistant, sleeps):
    """Persistent server errors raise after every back-off step."""
    client = FakeClient([FakeResponse(503)] * 10)
    with pytest.raises(GlowmarktApiError) as err:
        await async_fetch_hourly(hass, client, "r", START, START + timedelta(hours=1))
    assert err.value.status == 503
    assert sleeps == list(hourly_statistics.RETRY_DELAYS)
    assert len(client.session.calls) == len(hourly_statistics.RETRY_DELAYS) + 1


async def test_network_error_is_retried(hass: HomeAssistant, sleeps):
    """Timeouts and connection errors are retried like a 5xx."""
    client = FakeClient([requests.ConnectionError("down"), requests.Timeout(), ok([])])
    await async_fetch_hourly(hass, client, "r", START, START + timedelta(hours=1))
    assert len(client.session.calls) == 3


async def test_401_reauthenticates_once(hass: HomeAssistant, sleeps):
    """An expired token triggers one re-login and the request is repeated."""
    client = FakeClient([FakeResponse(401), ok([])])
    await async_fetch_hourly(hass, client, "r", START, START + timedelta(hours=1))
    assert client.auth_calls == 1
    assert client.token == "new-token"
    assert client.session.calls[1]["headers"]["token"] == "new-token"
    assert sleeps == []


async def test_client_error_is_not_retried(hass: HomeAssistant, sleeps):
    """Other 4xx responses fail at once."""
    client = FakeClient([FakeResponse(400)])
    with pytest.raises(GlowmarktApiError) as err:
        await async_fetch_hourly(hass, client, "r", START, START + timedelta(hours=1))
    assert err.value.status == 400
    assert len(client.session.calls) == 1


async def test_failure_in_later_chunk_raises(hass: HomeAssistant, sleeps):
    """A failed chunk fails the whole fetch, so nothing partial is written."""
    client = FakeClient([ok([]), FakeResponse(400)])
    with pytest.raises(GlowmarktApiError):
        await async_fetch_hourly(hass, client, "r", START, START + timedelta(days=15))
