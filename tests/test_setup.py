"""End-to-end setup tests with a fake Glowmarkt client."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.hildebrandglow_dcc import hourly_statistics, sensor
from custom_components.hildebrandglow_dcc.const import (
    CONF_BACKFILL_DAYS,
    CONF_LOOKBACK_HOURS,
    CONF_STATISTICS_INTERVAL,
    DOMAIN,
)
from homeassistant.components.recorder.statistics import list_statistic_ids
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

H = timedelta(hours=1)
CLASSIFIERS = {
    "electricity.consumption": "aaaaaaaa-0000-0000-0000-000000000001",
    "electricity.consumption.cost": "aaaaaaaa-0000-0000-0000-000000000002",
    "gas.consumption": "aaaaaaaa-0000-0000-0000-000000000003",
    "gas.consumption.cost": "aaaaaaaa-0000-0000-0000-000000000004",
}


class FakeResource:
    """A pyglowmarkt Resource returning fixed half-hourly readings."""

    def __init__(self, classifier, resource_id):
        """Store identity."""
        self.classifier = classifier
        self.id = resource_id
        self.periods = []

    def catchup(self):
        """No-op."""
        return {}

    def get_readings(self, t_from, t_to, period, func="sum", nulls=False):
        """Two half-hours of 0.5 (kWh or pence) each."""
        self.periods.append((t_from, period))
        return [
            [t_from, SimpleNamespace(value=0.5)],
            [t_from + timedelta(minutes=30), SimpleNamespace(value=0.5)],
        ]

    def get_tariff(self):
        """A flat tariff."""
        rates = SimpleNamespace(
            standing_charge=SimpleNamespace(value=44.71),
            rate=SimpleNamespace(value=24.9),
        )
        return SimpleNamespace(current_rates=rates)


@pytest.fixture
async def setup(recorder_mock, hass: HomeAssistant, enable_custom_integrations):
    """Set up the integration in Europe/London with a fake client."""
    await hass.config.async_set_time_zone("Europe/London")
    resources = [FakeResource(c, rid) for c, rid in CLASSIFIERS.items()]
    virtual_entity = SimpleNamespace(
        id="ve-1", name=None, get_resources=lambda: resources
    )
    client = SimpleNamespace(
        url="https://api.glowmarkt.com/api/v0-1/",
        get_virtual_entities=lambda: [virtual_entity],
    )

    async def immediate_refresh(coordinator, delay=5):
        await coordinator.async_refresh()

    async def fake_fetch(hass, client, resource_id, start, end):
        readings, hour = [], start
        while hour < end:
            readings.append((hour, 2.0))
            hour += H
        units = (
            "pence" if resource_id.endswith("2") or resource_id.endswith("4") else "kWh"
        )
        return readings, units

    entry = MockConfigEntry(
        domain=DOMAIN,
        version=6,
        data={"username": "user@example.com", "password": "not-a-real-password"},
        options={CONF_BACKFILL_DAYS: 2},
    )
    entry.add_to_hass(hass)
    with (
        patch("custom_components.hildebrandglow_dcc.BrightClient", return_value=client),
        patch.object(sensor, "_delayed_first_refresh", immediate_refresh),
        patch.object(hourly_statistics, "async_fetch_hourly", fake_fetch),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield hass, entry, resources


async def test_entities_keep_unique_ids(setup):
    """Existing sensors and their unique IDs are unchanged."""
    hass, entry, _ = setup
    assert entry.state is ConfigEntryState.LOADED
    registry = er.async_get(hass)
    unique_ids = {
        e.unique_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    elec = CLASSIFIERS["electricity.consumption"]
    elec_cost = CLASSIFIERS["electricity.consumption.cost"]
    assert {
        f"{elec}_usage_today",
        f"{elec}_standing_charge",
        f"{elec}_rate",
        f"{elec_cost}_cost_today",
    } <= unique_ids


async def test_daily_sensors_sum_half_hours_from_local_midnight(setup):
    """Usage/cost sum PT30M readings from local midnight; cost has last_reset."""
    hass, entry, resources = setup
    # Next scheduled refresh, now that the entities are listening.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=16))
    await hass.async_block_till_done(wait_background_tasks=True)
    registry = er.async_get(hass)
    usage_id = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{CLASSIFIERS['electricity.consumption']}_usage_today"
    )
    cost_id = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{CLASSIFIERS['electricity.consumption.cost']}_cost_today"
    )
    assert hass.states.get(usage_id).state == "1.0"
    cost = hass.states.get(cost_id)
    assert cost.state == "0.01"  # 1.0p -> £0.01
    midnight = dt_util.start_of_local_day()
    assert cost.attributes["last_reset"] == midnight.isoformat()
    assert cost.attributes["state_class"] == "total"

    t_from, period = resources[0].periods[0]
    assert period == "PT30M"
    assert t_from == midnight


async def test_statistics_imported_after_setup(setup):
    """The importer runs shortly after setup and writes all four statistics."""
    hass, entry, _ = setup
    async_fire_time_changed(
        hass,
        dt_util.utcnow() + timedelta(seconds=sensor.STATISTICS_FIRST_RUN_DELAY + 1),
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    await async_wait_recording_done(hass)

    stats = await hass.async_add_executor_job(list_statistic_ids, hass, None, None)
    ours = {s["statistic_id"]: s for s in stats if s["source"] == DOMAIN}
    expected = {
        hourly_statistics.statistic_id(c, rid) for c, rid in CLASSIFIERS.items()
    }
    assert set(ours) == expected
    elec = ours[
        hourly_statistics.statistic_id(
            "electricity.consumption", CLASSIFIERS["electricity.consumption"]
        )
    ]
    assert elec["statistics_unit_of_measurement"] == "kWh"
    assert elec["name"] == "Smart electricity meter usage"
    gas_cost = ours[
        hourly_statistics.statistic_id(
            "gas.consumption.cost", CLASSIFIERS["gas.consumption.cost"]
        )
    ]
    assert gas_cost["statistics_unit_of_measurement"] == "GBP"
    assert gas_cost["name"] == "Smart gas meter cost"


async def test_options_flow_saves_statistics_options(setup):
    """The options flow accepts and stores the new options."""
    hass, entry, _ = setup
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"
    with patch(
        "custom_components.hildebrandglow_dcc.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "daily_interval": 15,
                "tariff_interval": 60,
                CONF_STATISTICS_INTERVAL: 30,
                CONF_LOOKBACK_HOURS: 96,
                CONF_BACKFILL_DAYS: 200,
            },
        )
        await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert entry.options[CONF_BACKFILL_DAYS] == 200
    assert entry.options[CONF_LOOKBACK_HOURS] == 96
    assert entry.options[CONF_STATISTICS_INTERVAL] == 30


async def test_unload(setup):
    """The entry unloads cleanly, cancelling the scheduled import."""
    hass, entry, _ = setup
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
