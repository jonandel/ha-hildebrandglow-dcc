"""Tests for the pure statistics-building helpers."""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from custom_components.hildebrandglow_dcc.hourly_statistics import (
    build_hourly_statistics,
    floor_hour,
    statistic_id,
    statistic_metadata,
)
from homeassistant.components.recorder.models import StatisticMeanType

UTC = timezone.utc
LONDON = ZoneInfo("Europe/London")
H = timedelta(hours=1)


def utc(*args) -> datetime:
    """Build a UTC datetime."""
    return datetime(*args, tzinfo=UTC)


def hourly(start: datetime, values) -> list[tuple[datetime, float | None]]:
    """Readings at consecutive hours from start."""
    return [(start + i * H, value) for i, value in enumerate(values)]


def test_running_sum_from_start_sum():
    """Rows carry the cumulative sum on from the row before the window."""
    start = utc(2026, 11, 10, 0)
    rows = build_hourly_statistics(
        hourly(start, [1.0, 2.0, 0.5]), start, start + 3 * H, start_sum=100.0
    )
    assert [r["start"] for r in rows] == [start, start + H, start + 2 * H]
    assert [r["state"] for r in rows] == [1.0, 2.0, 0.5]
    assert [r["sum"] for r in rows] == [101.0, 103.0, 103.5]


def test_late_data_overwrites_earlier_zeros_and_resums():
    """A later poll corrects hours that had no data and shifts later sums."""
    start = utc(2026, 11, 10, 0)
    end = start + 4 * H
    # First poll: hour 1 was still missing (genuine zero for hour 2).
    first = build_hourly_statistics(
        hourly(start, [1.0, None, 0.0, 2.0]), start, end, start_sum=10.0
    )
    assert [r["state"] for r in first] == [1.0, 0.0, 0.0, 2.0]
    assert [r["sum"] for r in first] == [11.0, 11.0, 11.0, 13.0]

    # Second poll: hour 1 has arrived. Same window, same start sum.
    second = build_hourly_statistics(
        hourly(start, [1.0, 3.0, 0.0, 2.0]), start, end, start_sum=10.0
    )
    assert [r["start"] for r in second] == [r["start"] for r in first]
    assert [r["state"] for r in second] == [1.0, 3.0, 0.0, 2.0]
    assert [r["sum"] for r in second] == [11.0, 14.0, 14.0, 16.0]


def test_trailing_hours_without_data_are_not_written():
    """Hours after the last one with data are left for a later poll."""
    start = utc(2026, 11, 14, 15)
    rows = build_hourly_statistics(
        hourly(start, [0.5, 0.2, None, None, None]), start, start + 5 * H, 0.0
    )
    assert [r["start"] for r in rows] == [start, start + H]


def test_gap_inside_window_is_written_as_zero():
    """Missing hours between hours with data keep the rows contiguous."""
    start = utc(2026, 11, 10, 0)
    readings = [(start, 1.0), (start + 3 * H, 2.0)]  # hours 1 and 2 absent
    rows = build_hourly_statistics(readings, start, start + 4 * H, 0.0)
    assert [r["start"] for r in rows] == [start + i * H for i in range(4)]
    assert [r["state"] for r in rows] == [1.0, 0.0, 0.0, 2.0]
    assert rows[-1]["sum"] == 3.0


def test_rows_start_at_window_start_even_if_first_hours_empty():
    """The first row is always the window start, so the next poll finds it."""
    start = utc(2026, 11, 10, 0)
    rows = build_hourly_statistics(
        hourly(start, [None, None, 4.0]), start, start + 3 * H, 7.0
    )
    assert rows[0]["start"] == start
    assert rows[0]["sum"] == 7.0
    assert rows[-1]["sum"] == 11.0


def test_no_data_returns_no_rows():
    """Nothing is written when no hour has data."""
    start = utc(2026, 11, 10, 0)
    assert (
        build_hourly_statistics(hourly(start, [None, None]), start, start + 2 * H, 5.0)
        == []
    )
    assert build_hourly_statistics([], start, start + 2 * H, 5.0) == []


def test_never_writes_at_or_after_window_end_or_before_start():
    """Readings outside [start, end) are ignored."""
    start = utc(2026, 11, 10, 0)
    end = start + 2 * H
    readings = hourly(start - H, [9.0, 1.0, 1.0, 9.0])
    rows = build_hourly_statistics(readings, start, end, 0.0)
    assert [r["start"] for r in rows] == [start, start + H]
    assert rows[-1]["sum"] == 2.0


def test_pence_to_pounds():
    """Cost readings in pence are scaled to GBP."""
    start = utc(2026, 11, 10, 0)
    rows = build_hourly_statistics(
        hourly(start, [8.2668, 6.1503]), start, start + 2 * H, 1.5, scale=0.01
    )
    assert rows[0]["state"] == pytest.approx(0.082668)
    assert rows[1]["sum"] == pytest.approx(1.5 + 0.144171)


def test_half_hour_readings_aggregate_to_hours():
    """Finer readings are summed into their hour."""
    start = utc(2026, 11, 10, 0)
    half = timedelta(minutes=30)
    readings = [
        (start, 1.367),
        (start + half, 1.333),
        (start + H, 1.519),
        (start + H + half, None),
    ]
    rows = build_hourly_statistics(readings, start, start + 2 * H, 0.0)
    assert [r["state"] for r in rows] == [pytest.approx(2.7), pytest.approx(1.519)]


def _daily_totals(rows) -> dict:
    totals = defaultdict(float)
    counts = defaultdict(int)
    for row in rows:
        day = row["start"].astimezone(LONDON).date()
        totals[day] += row["state"]
        counts[day] += 1
    return totals, counts


@pytest.mark.parametrize(
    ("day", "hours"),
    [
        (datetime(2026, 3, 29).date(), 23),  # clocks go forward
        (datetime(2026, 10, 25).date(), 25),  # clocks go back
    ],
)
def test_clock_change_days(day, hours):
    """Local days with 23 or 25 hours get every UTC hour exactly once."""
    local_midnight = datetime.combine(day, datetime.min.time(), tzinfo=LONDON)
    start = local_midnight.astimezone(UTC) - 24 * H  # day before, local midnight
    end = (local_midnight + timedelta(days=2)).astimezone(UTC)
    n = int((end - start) / H)
    rows = build_hourly_statistics(hourly(start, [1.0] * n), start, end, 0.0)
    totals, counts = _daily_totals(rows)
    assert counts[day] == hours
    assert totals[day] == pytest.approx(hours)
    assert rows[-1]["sum"] == pytest.approx(n)
    starts = [r["start"] for r in rows]
    assert starts == sorted(set(starts))


def test_floor_hour_converts_to_utc():
    """floor_hour returns an aware UTC hour."""
    when = datetime(2026, 7, 1, 0, 45, tzinfo=LONDON)  # 23:45 UTC the day before
    assert floor_hour(when) == utc(2026, 6, 30, 23)


@pytest.mark.parametrize(
    ("classifier", "resource_id", "expected"),
    [
        (
            "electricity.consumption",
            "0A1B2C3D-aaaa-4bbb-8ccc-123456789abc",
            "hildebrandglow_dcc:electricity_consumption_0a1b2c3d_aaaa_4bbb_8ccc_123456789abc",
        ),
        (
            "gas.consumption.cost",
            "-x--y-",
            "hildebrandglow_dcc:gas_consumption_cost_x_y",
        ),
    ],
)
def test_statistic_id(classifier, resource_id, expected):
    """Statistic IDs are valid, lowercase and unique per resource."""
    from homeassistant.components.recorder.statistics import valid_statistic_id

    stat_id = statistic_id(classifier, resource_id)
    assert stat_id == expected
    assert valid_statistic_id(stat_id)


def test_statistic_metadata():
    """Metadata matches what HA 2026.11 requires for external statistics."""
    usage = statistic_metadata("hildebrandglow_dcc:x", "Meter usage", "gas.consumption")
    assert usage["source"] == "hildebrandglow_dcc"
    assert usage["has_sum"] is True
    assert usage["mean_type"] is StatisticMeanType.NONE
    assert usage["unit_of_measurement"] == "kWh"
    assert usage["unit_class"] == "energy"
    assert "has_mean" not in usage

    cost = statistic_metadata(
        "hildebrandglow_dcc:y", "Meter cost", "electricity.consumption.cost"
    )
    assert cost["unit_of_measurement"] == "GBP"
    assert cost["unit_class"] is None
