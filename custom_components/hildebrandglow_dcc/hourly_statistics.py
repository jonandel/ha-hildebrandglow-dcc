"""Hourly long-term statistics imported from the Glowmarkt API.

Smart meter readings reach Glowmarkt from the DCC hours (sometimes a day) after
they were taken. A sensor that only reports "today so far" either misses that
late data or books it to the hour it arrived in. Instead, this module writes
external statistics (``hildebrandglow_dcc:<object_id>``) directly into the
recorder, one row per hour, back-dated to the hour the energy was used.

Every poll re-reads a lookback window (72 hours by default) and rewrites every
hour in it, carrying the cumulative sum on from the last row before the window.
Hours that were empty because their data had not arrived yet are corrected on a
later poll, and the sums after them are recalculated.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import re
from typing import Any

import requests

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter

from .const import (
    DOMAIN,
    ELEC_CONSUMPTION_CLASSIFIER,
    ELEC_COST_CLASSIFIER,
    GAS_CONSUMPTION_CLASSIFIER,
    GAS_COST_CLASSIFIER,
)

_LOGGER = logging.getLogger(__name__)

STATISTICS_CLASSIFIERS = (
    ELEC_CONSUMPTION_CLASSIFIER,
    GAS_CONSUMPTION_CLASSIFIER,
    ELEC_COST_CLASSIFIER,
    GAS_COST_CLASSIFIER,
)

HOUR = timedelta(hours=1)
HALF_HOUR = timedelta(minutes=30)
# Glowmarkt limits how much hourly data one request may cover (about 10 days).
CHUNK = timedelta(days=10)
# If no row is found in the hour just before the window, look this far back.
SUM_LOOKUP_FALLBACK = timedelta(days=31)

REQUEST_TIMEOUT = 60
# Pause between consecutive requests, to be polite to the API.
REQUEST_SPACING = 1.0
# Back-off delays (seconds) after a 429, a 5xx or a network error.
RETRY_DELAYS: tuple[float, ...] = (10, 30, 90)
MAX_RETRY_AFTER = 300

_SLUG_RE = re.compile(r"[^a-z0-9]+")


class GlowmarktApiError(Exception):
    """Glowmarkt request failed after retries."""

    def __init__(self, message: str, status: int | None = None) -> None:
        """Initialise with the last HTTP status, if any."""
        super().__init__(message)
        self.status = status


# --- PURE HELPERS ---


def statistic_id(classifier: str, resource_id: str) -> str:
    """Return the external statistic ID for a Glowmarkt resource.

    The resource ID is included so that several meters or installations never
    share a statistic.
    """
    object_id = _SLUG_RE.sub("_", f"{classifier}_{resource_id}".lower()).strip("_")
    return f"{DOMAIN}:{object_id}"


def statistic_metadata(stat_id: str, name: str, classifier: str) -> StatisticMetaData:
    """Return the recorder metadata for a statistic."""
    is_cost = classifier.endswith(".cost")
    metadata: dict[str, Any] = {
        "has_sum": True,
        "mean_type": StatisticMeanType.NONE,
        "name": name,
        "source": DOMAIN,
        "statistic_id": stat_id,
        "unit_of_measurement": "GBP" if is_cost else UnitOfEnergy.KILO_WATT_HOUR,
    }
    # unit_class was added to StatisticMetaData in HA 2025.10 and is required
    # from 2026.11; older versions do not accept it.
    if "unit_class" in (
        StatisticMetaData.__required_keys__ | StatisticMetaData.__optional_keys__
    ):
        metadata["unit_class"] = None if is_cost else EnergyConverter.UNIT_CLASS
    return metadata  # type: ignore[return-value]


def floor_hour(when: datetime) -> datetime:
    """Return the start of the UTC hour containing ``when``."""
    return when.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def build_hourly_statistics(
    readings: Iterable[tuple[datetime, float | None]],
    window_start: datetime,
    window_end: datetime,
    start_sum: float,
    scale: float = 1.0,
) -> list[StatisticData]:
    """Turn readings into hourly statistic rows with a running sum.

    ``readings`` are ``(bucket start, value)`` pairs at hourly or finer
    resolution. ``None`` means "no data yet" (Glowmarkt ``nulls=1``); it is
    distinct from a genuine zero.

    Rows cover every hour from ``window_start`` up to and including the last
    hour that has data, and never reach ``window_end``. Hours inside that range
    without data are written as zero, so rows stay contiguous and a later poll
    can find the sum just before its window. Hours after the last one with
    data are left unwritten rather than shown as zero. ``scale`` converts
    units, e.g. ``0.01`` for pence to pounds.
    """
    window_start = floor_hour(window_start)
    hours: dict[datetime, float | None] = {}
    for when, value in readings:
        hour = floor_hour(when)
        if not window_start <= hour < window_end:
            continue
        if value is None:
            hours.setdefault(hour, None)
        else:
            hours[hour] = (hours.get(hour) or 0.0) + float(value) * scale

    with_data = [hour for hour, value in hours.items() if value is not None]
    if not with_data:
        return []
    last = max(with_data)

    rows: list[StatisticData] = []
    running = float(start_sum)
    hour = window_start
    while hour <= last:
        value = hours.get(hour) or 0.0
        running += value
        rows.append(
            StatisticData(start=hour, state=round(value, 6), sum=round(running, 6))
        )
        hour += HOUR
    return rows


# --- GLOWMARKT FETCHING ---


def _api_time(when: datetime) -> str:
    """Format a datetime as the naive UTC string Glowmarkt expects."""
    return when.astimezone(timezone.utc).replace(tzinfo=None).isoformat()


def _request_readings(
    client, resource_id: str, start: datetime, end: datetime
) -> tuple[int, Any, str | None]:
    """GET hourly readings for [start, end). Runs in the executor.

    pyglowmarkt's ``get_readings`` turns every non-200 response into a bare
    ``RuntimeError``, which hides 429s and 5xxs, so the request is made here on
    the client's own session. ``to`` is inclusive at half-hour resolution, so
    it is set to the last half-hour that belongs to the range.
    """
    params = {
        "from": _api_time(start),
        "to": _api_time(end - HALF_HOUR),
        "period": "PT1H",
        "function": "sum",
        "offset": 0,
        "nulls": 1,
    }
    headers = {
        "Content-Type": "application/json",
        "applicationId": client.application,
        "token": client.token,
    }
    resp = client.session.get(
        f"{client.url}resource/{resource_id}/readings",
        headers=headers,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )
    body = resp.json() if resp.status_code == 200 else None
    return resp.status_code, body, resp.headers.get("Retry-After")


async def _async_request_with_retry(
    hass: HomeAssistant, client, resource_id: str, start: datetime, end: datetime
) -> dict[str, Any]:
    """Request one chunk, backing off on 429, 5xx and network errors."""
    reauthenticated = False
    attempt = 0
    while True:
        status: int | None = None
        retry_after: str | None = None
        try:
            status, body, retry_after = await hass.async_add_executor_job(
                _request_readings, client, resource_id, start, end
            )
        except (requests.Timeout, requests.ConnectionError) as ex:
            _LOGGER.debug("Glowmarkt request failed: %s", ex)
        else:
            if status == 200:
                return body
            if status == 401 and not reauthenticated:
                _LOGGER.debug("Glowmarkt token rejected, re-authenticating")
                reauthenticated = True
                client.token = await hass.async_add_executor_job(client.authenticate)
                continue

        retryable = status is None or status == 429 or status >= 500
        if not retryable or attempt >= len(RETRY_DELAYS):
            raise GlowmarktApiError(
                f"readings request for {_api_time(start)}..{_api_time(end)} "
                f"failed (status {status})",
                status,
            )
        delay = RETRY_DELAYS[attempt]
        if retry_after and retry_after.isdigit():
            delay = min(max(delay, int(retry_after)), MAX_RETRY_AFTER)
        attempt += 1
        _LOGGER.debug("Glowmarkt returned %s; retry %s in %ss", status, attempt, delay)
        await asyncio.sleep(delay)


async def async_fetch_hourly(
    hass: HomeAssistant, client, resource_id: str, start: datetime, end: datetime
) -> tuple[list[tuple[datetime, float | None]], str | None]:
    """Fetch hourly readings for [start, end), split into API-sized chunks.

    Returns the readings and the units Glowmarkt reported ("kWh" or "pence").
    Raises GlowmarktApiError if any chunk fails, so that a partial result is
    never written over good data.
    """
    readings: list[tuple[datetime, float | None]] = []
    units: str | None = None
    chunk_start = start
    while chunk_start < end:
        chunk_end = min(chunk_start + CHUNK, end)
        if chunk_start > start:
            await asyncio.sleep(REQUEST_SPACING)
        body = await _async_request_with_retry(
            hass, client, resource_id, chunk_start, chunk_end
        )
        units = units or body.get("units")
        for epoch, value in body.get("data", []):
            readings.append((datetime.fromtimestamp(epoch, tz=timezone.utc), value))
        chunk_start = chunk_end
    return readings, units


# --- IMPORTER ---


def _is_cost(source: StatisticsSource) -> bool:
    """Return whether a source is a cost resource."""
    return source.resource.classifier.endswith(".cost")


@dataclass
class StatisticsSource:
    """A Glowmarkt resource and the statistic it is imported into."""

    resource: Any
    statistic_id: str
    metadata: StatisticMetaData
    # Earliest hour backfilled since this importer started; avoids refetching
    # the whole backfill window every poll for a meter that has no data.
    backfilled_from: datetime | None = None


class HourlyStatisticsImporter:
    """Import hourly Glowmarkt readings into long-term statistics."""

    def __init__(
        self,
        hass: HomeAssistant,
        client,
        lookback_hours: int,
        backfill_days: int,
    ) -> None:
        """Initialise the importer."""
        self.hass = hass
        self.client = client
        self.lookback = timedelta(hours=lookback_hours)
        self.backfill_days = backfill_days
        self.sources: list[StatisticsSource] = []
        # Last hour with usage data, by usage classifier, from this session.
        self._usage_until: dict[str, datetime] = {}
        self._lock = asyncio.Lock()

    def add_resource(self, resource, name: str) -> None:
        """Register a Glowmarkt resource for import."""
        stat_id = statistic_id(resource.classifier, resource.id)
        if any(source.statistic_id == stat_id for source in self.sources):
            return
        self.sources.append(
            StatisticsSource(
                resource=resource,
                statistic_id=stat_id,
                metadata=statistic_metadata(stat_id, name, resource.classifier),
            )
        )
        _LOGGER.debug("Hourly statistics for %s -> %s", resource.classifier, stat_id)

    async def async_run(self, now: datetime | None = None) -> None:
        """Update every registered statistic. Errors are logged, not raised."""
        if self._lock.locked():
            _LOGGER.debug("Previous hourly statistics import still running")
            return
        async with self._lock:
            # Usage first, so that cost can stop at the last hour of usage data.
            for source in sorted(self.sources, key=_is_cost):
                try:
                    await self.async_import(source, now)
                except GlowmarktApiError as ex:
                    _LOGGER.warning(
                        "Could not update hourly statistics for %s: %s. "
                        "The next poll will fill the gap",
                        source.resource.classifier,
                        ex,
                    )
                    if ex.status == 429:
                        break
                except Exception:  # pylint: disable=broad-except
                    _LOGGER.exception(
                        "Unexpected error updating hourly statistics for %s",
                        source.resource.classifier,
                    )

    async def async_import(
        self, source: StatisticsSource, now: datetime | None = None
    ) -> int:
        """Fetch the window for one source and write it. Returns rows written."""
        now = now or dt_util.utcnow()
        end = floor_hour(now)
        window_start, backfill_start = await self._async_window_start(source, now)
        if window_start >= end:
            return 0
        start_sum = await self._async_sum_before(source.statistic_id, window_start)

        readings, units = await async_fetch_hourly(
            self.hass, self.client, source.resource.id, window_start, end
        )
        if window_start == backfill_start:
            source.backfilled_from = backfill_start

        rows_end = end
        if _is_cost(source) and (limit := await self._async_cost_limit(source)):
            rows_end = min(end, limit + HOUR)
        scale = 0.01 if units == "pence" else 1.0
        rows = build_hourly_statistics(
            readings, window_start, rows_end, start_sum, scale
        )
        if rows and not _is_cost(source):
            self._usage_until[source.resource.classifier] = rows[-1]["start"]
        if not rows:
            _LOGGER.debug(
                "No readings yet for %s between %s and %s",
                source.statistic_id,
                window_start,
                end,
            )
            return 0
        async_add_external_statistics(self.hass, source.metadata, rows)
        _LOGGER.debug(
            "Wrote %s hourly rows to %s (%s .. %s), sum %s -> %s",
            len(rows),
            source.statistic_id,
            rows[0]["start"],
            rows[-1]["start"],
            start_sum,
            rows[-1]["sum"],
        )
        return len(rows)

    async def _async_window_start(
        self, source: StatisticsSource, now: datetime
    ) -> tuple[datetime, datetime]:
        """Return (window start, backfill start) for this poll.

        Backfill from local midnight ``backfill_days`` ago when there is no row
        at that hour: on first setup, after the backfill option is raised, or
        after the statistic was deleted. Otherwise re-read the lookback window,
        extended back to the last stored row if polling stopped for a while.
        """
        local_today = dt_util.as_local(now).date()
        backfill_start = floor_hour(
            dt_util.start_of_local_day(local_today - timedelta(days=self.backfill_days))
        )
        stat_id = source.statistic_id

        if (
            source.backfilled_from is None or backfill_start < source.backfilled_from
        ) and not await self._async_rows(
            stat_id, backfill_start, backfill_start + HOUR
        ):
            return backfill_start, backfill_start

        window_start = floor_hour(now) - self.lookback
        if last := await self._async_last_hour(stat_id):
            window_start = min(window_start, last + HOUR)
        return window_start, backfill_start

    async def _async_cost_limit(self, source: StatisticsSource) -> datetime | None:
        """Return the last hour a cost statistic may be written to, if limited.

        Glowmarkt reports cost as 0, not null, for hours whose usage has not
        arrived yet. Cost rows therefore stop at the last hour of usage data,
        but never short of a row already stored, so running sums stay
        consistent. Without usage data from this session there is no limit.
        """
        usage = source.resource.classifier.removesuffix(".cost")
        if (usage_until := self._usage_until.get(usage)) is None:
            return None
        stored_until = await self._async_last_hour(source.statistic_id)
        return max(usage_until, stored_until) if stored_until else usage_until

    async def _async_last_hour(self, stat_id: str) -> datetime | None:
        """Return the start of the newest stored row, if any."""
        last = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, stat_id, False, {"sum"}
        )
        if rows := last.get(stat_id):
            return dt_util.utc_from_timestamp(rows[0]["start"])
        return None

    async def _async_rows(
        self, stat_id: str, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        """Return stored hourly rows with start in [start, end)."""
        stats = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            start,
            end,
            {stat_id},
            "hour",
            None,
            {"sum"},
        )
        return stats.get(stat_id, [])

    async def _async_sum_before(self, stat_id: str, window_start: datetime) -> float:
        """Return the cumulative sum of the last row before ``window_start``."""
        for span in (HOUR, SUM_LOOKUP_FALLBACK):
            if rows := await self._async_rows(
                stat_id, window_start - span, window_start
            ):
                return float(rows[-1].get("sum") or 0.0)
        return 0.0
