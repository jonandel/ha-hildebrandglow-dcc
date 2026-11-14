"""Tests for HourlyStatisticsImporter against a real recorder."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.hildebrandglow_dcc import hourly_statistics
from custom_components.hildebrandglow_dcc.hourly_statistics import (
    GlowmarktApiError,
    HourlyStatisticsImporter,
)
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant

UTC = timezone.utc
H = timedelta(hours=1)
NOW = datetime(2026, 11, 14, 22, 33, tzinfo=UTC)
RESOURCE_ID = "11111111-2222-3333-4444-555555555555"
STAT_ID = f"hildebrandglow_dcc:electricity_consumption_{RESOURCE_ID.replace('-', '_')}"
COST_STAT_ID = (
    f"hildebrandglow_dcc:electricity_consumption_cost_{RESOURCE_ID.replace('-', '_')}"
)


class FakeGlowmarkt:
    """Serves readings from an in-memory {utc hour: value} map, like the API."""

    def __init__(self, units: str = "kWh") -> None:
        """Start with no data."""
        self.data: dict[datetime, float] = {}
        self.units = units
        self.requests: list[tuple[datetime, datetime]] = []
        self.fail = False

    async def fetch(self, hass, client, resource_id, start, end):
        """Stand-in for async_fetch_hourly."""
        self.requests.append((start, end))
        if self.fail:
            raise GlowmarktApiError("boom", 503)
        readings = []
        hour = start
        while hour < end:
            readings.append((hour, self.data.get(hour)))
            hour += H
        return readings, self.units


@pytest.fixture
async def london(recorder_mock, hass: HomeAssistant):
    """A hass instance with the recorder, in the UK time zone."""
    await hass.config.async_set_time_zone("Europe/London")
    return hass


def make_importer(
    hass, lookback_hours=72, backfill_days=3, classifier="electricity.consumption"
):
    """Importer with one resource."""
    importer = HourlyStatisticsImporter(hass, object(), lookback_hours, backfill_days)
    importer.add_resource(
        SimpleNamespace(id=RESOURCE_ID, classifier=classifier), "Smart meter usage"
    )
    return importer


async def stored(hass, stat_id=STAT_ID) -> dict[datetime, tuple[float, float]]:
    """All stored hourly rows as {start: (state, sum)}."""
    await async_wait_recording_done(hass)
    stats = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        NOW - timedelta(days=60),
        None,
        {stat_id},
        "hour",
        None,
        {"state", "sum"},
    )
    return {
        datetime.fromtimestamp(r["start"], UTC): (r["state"], r["sum"])
        for r in stats.get(stat_id, [])
    }


def fill(server: FakeGlowmarkt, start: datetime, end: datetime, value=1.0) -> None:
    """Give every hour in [start, end) the same value."""
    hour = start
    while hour < end:
        server.data[hour] = value
        hour += H


async def run(importer, server, now=NOW):
    """Run one poll with the fake API."""
    with patch.object(hourly_statistics, "async_fetch_hourly", server.fetch):
        await importer.async_run(now)


async def test_first_run_backfills_from_local_midnight(london):
    """With no statistics yet, backfill from local midnight N days ago."""
    hass = london
    server = FakeGlowmarkt()
    backfill_start = datetime(2026, 11, 11, 0, tzinfo=UTC)  # GMT, so local == UTC
    last_data = datetime(2026, 11, 14, 17, tzinfo=UTC)
    fill(server, backfill_start, last_data + H)

    await run(make_importer(hass), server)

    assert server.requests == [(backfill_start, datetime(2026, 11, 14, 22, tzinfo=UTC))]
    rows = await stored(hass)
    assert min(rows) == backfill_start
    assert max(rows) == last_data  # no rows for hours without data yet
    assert len(rows) == (last_data - backfill_start) // H + 1
    assert rows[last_data][1] == pytest.approx(len(rows))


async def test_backfill_start_during_bst(london):
    """Local midnight in summer is 23:00 UTC the previous day."""
    hass = london
    server = FakeGlowmarkt()
    now = datetime(2026, 7, 10, 12, 5, tzinfo=UTC)
    fill(server, datetime(2026, 7, 1, tzinfo=UTC), now)
    await run(make_importer(hass, backfill_days=2), server, now)
    assert server.requests[0][0] == datetime(2026, 7, 7, 23, tzinfo=UTC)


async def test_late_data_is_corrected_on_next_poll(london):
    """Hours whose data arrives late are rewritten and later sums shift."""
    hass = london
    server = FakeGlowmarkt()
    importer = make_importer(hass)
    late_hour = datetime(2026, 11, 14, 10, tzinfo=UTC)
    fill(
        server,
        datetime(2026, 11, 11, tzinfo=UTC),
        datetime(2026, 11, 14, 18, tzinfo=UTC),
    )
    server.data[late_hour] = None  # not arrived yet

    await run(importer, server)
    rows = await stored(hass)
    assert rows[late_hour] == (0.0, rows[late_hour - H][1])
    total_before = rows[max(rows)][1]

    server.data[late_hour] = 5.0
    await run(importer, server, NOW + H)
    rows = await stored(hass)
    assert rows[late_hour][0] == 5.0
    assert rows[late_hour][1] == pytest.approx(rows[late_hour - H][1] + 5.0)
    assert rows[max(rows)][1] == pytest.approx(total_before + 5.0)


async def test_second_poll_only_rereads_lookback(london):
    """After the backfill, each poll reads just the lookback window."""
    hass = london
    server = FakeGlowmarkt()
    importer = make_importer(hass, lookback_hours=24, backfill_days=5)
    fill(server, datetime(2026, 11, 9, tzinfo=UTC), datetime(2026, 11, 15, tzinfo=UTC))

    await run(importer, server)
    await stored(hass)
    await run(importer, server, NOW + H)
    end = datetime(2026, 11, 14, 23, tzinfo=UTC)
    assert server.requests[-1] == (end - 24 * H, end)


async def test_restart_does_not_duplicate_or_reset_sums(london):
    """A new importer (reload/restart) continues the stored running sum."""
    hass = london
    server = FakeGlowmarkt()
    fill(server, datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 11, 15, tzinfo=UTC))

    await run(make_importer(hass, lookback_hours=24), server)
    first = await stored(hass)

    await run(make_importer(hass, lookback_hours=24), server, NOW + H)
    second = await stored(hass)

    # One backfill only: the restart read just the lookback window.
    assert server.requests[-1][0] == datetime(2026, 11, 13, 23, tzinfo=UTC)
    assert len(second) == len(first) + 1
    for hour, (state, total) in first.items():
        assert second[hour] == (state, pytest.approx(total))
    newest = max(second)
    assert second[newest][1] == pytest.approx(len(second))


async def test_raising_backfill_days_imports_older_history(london):
    """Increasing the backfill option rebuilds from the new, earlier start."""
    hass = london
    server = FakeGlowmarkt()
    fill(server, datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 11, 15, tzinfo=UTC))

    await run(make_importer(hass, backfill_days=3), server)
    assert min(await stored(hass)) == datetime(2026, 11, 11, tzinfo=UTC)

    await run(make_importer(hass, backfill_days=10), server, NOW + H)
    rows = await stored(hass)
    assert min(rows) == datetime(2026, 11, 4, tzinfo=UTC)
    assert rows[max(rows)][1] == pytest.approx(len(rows))


async def test_gap_after_downtime_is_filled(london):
    """If polling stopped for longer than the lookback, read back to the last row."""
    hass = london
    server = FakeGlowmarkt()
    fill(server, datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 11, 20, tzinfo=UTC))
    importer = make_importer(hass, lookback_hours=24, backfill_days=3)
    await run(importer, server)
    last = max(await stored(hass))

    later = NOW + timedelta(days=4)
    await run(importer, server, later)
    assert server.requests[-1][0] == last + H
    rows = await stored(hass)
    hours = sorted(rows)
    assert hours == [hours[0] + i * H for i in range(len(hours))]
    assert rows[hours[-1]][1] == pytest.approx(len(hours))


async def test_failed_poll_writes_nothing(london):
    """An API failure leaves existing rows untouched; the next poll recovers."""
    hass = london
    server = FakeGlowmarkt()
    importer = make_importer(hass)
    fill(
        server,
        datetime(2026, 11, 11, tzinfo=UTC),
        datetime(2026, 11, 14, 18, tzinfo=UTC),
    )
    await run(importer, server)
    before = await stored(hass)

    server.fail = True
    await run(importer, server, NOW + H)
    assert await stored(hass) == before

    server.fail = False
    server.data[datetime(2026, 11, 14, 18, tzinfo=UTC)] = 2.0
    await run(importer, server, NOW + 2 * H)
    after = await stored(hass)
    assert max(after) == datetime(2026, 11, 14, 18, tzinfo=UTC)


async def test_meter_without_data_does_not_rebackfill_every_poll(london):
    """A meter with no data is backfilled once per session, then only polled."""
    hass = london
    server = FakeGlowmarkt()
    importer = make_importer(hass, lookback_hours=24, backfill_days=30)
    await run(importer, server)
    await run(importer, server, NOW + H)
    assert server.requests[0][0] == datetime(
        2026, 10, 14, 23, tzinfo=UTC
    )  # 15 Oct, BST
    assert server.requests[1][0] == datetime(2026, 11, 13, 23, tzinfo=UTC)
    assert await stored(hass) == {}


async def test_cost_is_written_in_pounds(london):
    """Pence from Glowmarkt become GBP in the statistic."""
    hass = london
    server = FakeGlowmarkt(units="pence")
    fill(
        server,
        datetime(2026, 11, 11, tzinfo=UTC),
        datetime(2026, 11, 12, tzinfo=UTC),
        25.0,
    )
    await run(make_importer(hass, classifier="electricity.consumption.cost"), server)
    rows = await stored(hass, COST_STAT_ID)
    assert rows[datetime(2026, 11, 11, tzinfo=UTC)][0] == pytest.approx(0.25)
    assert rows[max(rows)][1] == pytest.approx(6.0)


COST_RESOURCE_ID = "66666666-7777-8888-9999-000000000000"
SPLIT_COST_STAT_ID = (
    "hildebrandglow_dcc:electricity_consumption_cost_"
    f"{COST_RESOURCE_ID.replace('-', '_')}"
)


def usage_and_cost(hass, server_by_resource):
    """Importer with a usage and a cost resource, cost registered first."""
    importer = HourlyStatisticsImporter(hass, object(), 72, 3)
    importer.add_resource(
        SimpleNamespace(id=COST_RESOURCE_ID, classifier="electricity.consumption.cost"),
        "Smart meter cost",
    )
    importer.add_resource(
        SimpleNamespace(id=RESOURCE_ID, classifier="electricity.consumption"),
        "Smart meter usage",
    )

    async def fetch(hass, client, resource_id, start, end):
        return await server_by_resource[resource_id].fetch(
            hass, client, resource_id, start, end
        )

    return importer, fetch


async def test_cost_stops_at_last_hour_of_usage(london):
    """Cost hours whose usage has not arrived are left out, not written as 0."""
    hass = london
    usage, cost = FakeGlowmarkt(), FakeGlowmarkt(units="pence")
    start = datetime(2026, 11, 11, tzinfo=UTC)
    last_usage = datetime(2026, 11, 14, 17, tzinfo=UTC)
    fill(usage, start, last_usage + H)
    fill(cost, start, last_usage + H, 25.0)
    fill(cost, last_usage + H, NOW, 0.0)  # Glowmarkt sends 0, not null
    importer, fetch = usage_and_cost(hass, {RESOURCE_ID: usage, COST_RESOURCE_ID: cost})

    with patch.object(hourly_statistics, "async_fetch_hourly", fetch):
        await importer.async_run(NOW)
    assert max(await stored(hass, SPLIT_COST_STAT_ID)) == last_usage

    usage.data[last_usage + H] = 1.0
    cost.data[last_usage + H] = 25.0
    with patch.object(hourly_statistics, "async_fetch_hourly", fetch):
        await importer.async_run(NOW + H)
    rows = await stored(hass, SPLIT_COST_STAT_ID)
    assert max(rows) == last_usage + H
    assert rows[max(rows)][1] == pytest.approx(0.25 * len(rows))


async def test_cost_rows_already_past_usage_keep_sums_consistent(london):
    """Zero cost rows written before the limit existed are rewritten, not left stale."""
    hass = london
    usage, cost = FakeGlowmarkt(), FakeGlowmarkt(units="pence")
    start = datetime(2026, 11, 11, tzinfo=UTC)
    last_usage = datetime(2026, 11, 14, 17, tzinfo=UTC)
    fill(usage, start, last_usage + H)
    fill(cost, start, last_usage + H, 25.0)
    fill(cost, last_usage + H, NOW, 0.0)
    # Cost on its own has no usage to stop at, so it runs to the last hour.
    cost_only = HourlyStatisticsImporter(hass, object(), 72, 3)
    cost_only.add_resource(
        SimpleNamespace(id=COST_RESOURCE_ID, classifier="electricity.consumption.cost"),
        "Smart meter cost",
    )
    with patch.object(hourly_statistics, "async_fetch_hourly", cost.fetch):
        await cost_only.async_run(NOW)
    assert max(await stored(hass, SPLIT_COST_STAT_ID)) == NOW.replace(minute=0) - H

    usage.data[last_usage + H] = 1.0
    cost.data[last_usage + H] = 25.0
    importer, fetch = usage_and_cost(hass, {RESOURCE_ID: usage, COST_RESOURCE_ID: cost})
    with patch.object(hourly_statistics, "async_fetch_hourly", fetch):
        await importer.async_run(NOW + H)
    rows = await stored(hass, SPLIT_COST_STAT_ID)
    sums = [rows[hour][1] for hour in sorted(rows)]
    assert sums == sorted(sums)  # never decreases
    assert sums[-1] == pytest.approx(0.25 * ((last_usage + H - start) // H + 1))
