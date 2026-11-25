"""Config entries made by the original HandyHat integration (version 1) keep working."""

from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry
import requests

from custom_components.hildebrandglow_dcc import async_migrate_entry
from custom_components.hildebrandglow_dcc.config_flow import ConfigFlow
from custom_components.hildebrandglow_dcc.const import DOMAIN
from homeassistant.components.recorder import Recorder
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

USAGE_ID = "11111111-2222-4333-8444-555555555555"
COST_ID = "66666666-7777-4888-9999-aaaaaaaaaaaa"


def _handyhat_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        title="Hildebrand Glow (DCC)",
        data={"username": "user@example.com", "password": "secret"},
    )
    entry.add_to_hass(hass)
    return entry


def _register_handyhat_sensors(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Register the entities exactly as HandyHat v1.0.3 named them."""
    registry = er.async_get(hass)
    for unique_id, object_id, device_class in (
        (USAGE_ID, "electric_usage_today", "energy"),
        (COST_ID, "electric_cost_today", "monetary"),
        (f"{USAGE_ID}-tariff", "electric_standing_charge", "monetary"),
        (f"{USAGE_ID}-rate", "electric_rate", None),
    ):
        registry.async_get_or_create(
            "sensor",
            DOMAIN,
            unique_id,
            config_entry=entry,
            suggested_object_id=object_id,
            original_device_class=device_class,
        )


async def test_handyhat_entry_loads_instead_of_failing_migration(
    recorder_mock: Recorder, hass: HomeAssistant, enable_custom_integrations: None
) -> None:
    """A version 1 entry reaches setup; it used to stop at 'Migration handler not found'."""
    entry = _handyhat_entry(hass)
    with patch(
        "custom_components.hildebrandglow_dcc.BrightClient",
        side_effect=requests.exceptions.ConnectionError("offline"),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert entry.version == ConfigFlow.VERSION


async def test_handyhat_sensors_keep_their_entity_ids(hass: HomeAssistant) -> None:
    """Old unique IDs move to this integration's scheme, so history and dashboards stay."""
    entry = _handyhat_entry(hass)
    _register_handyhat_sensors(hass, entry)

    assert await async_migrate_entry(hass, entry)

    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id("sensor", DOMAIN, f"{USAGE_ID}_usage_today")
        == "sensor.electric_usage_today"
    )
    assert (
        registry.async_get_entity_id("sensor", DOMAIN, f"{COST_ID}_cost_today")
        == "sensor.electric_cost_today"
    )
    assert (
        registry.async_get_entity_id("sensor", DOMAIN, f"{USAGE_ID}_standing_charge")
        == "sensor.electric_standing_charge"
    )
    assert (
        registry.async_get_entity_id("sensor", DOMAIN, f"{USAGE_ID}_rate")
        == "sensor.electric_rate"
    )
    assert registry.async_get_entity_id("sensor", DOMAIN, USAGE_ID) is None


async def test_existing_new_style_sensor_is_not_overwritten(
    hass: HomeAssistant,
) -> None:
    """If the new unique ID is already registered, the old entity is left alone."""
    entry = _handyhat_entry(hass)
    _register_handyhat_sensors(hass, entry)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{USAGE_ID}_usage_today",
        config_entry=entry,
        suggested_object_id="new_usage",
    )

    assert await async_migrate_entry(hass, entry)

    assert (
        registry.async_get_entity_id("sensor", DOMAIN, f"{USAGE_ID}_usage_today")
        == "sensor.new_usage"
    )
    assert (
        registry.async_get_entity_id("sensor", DOMAIN, USAGE_ID)
        == "sensor.electric_usage_today"
    )


async def test_current_entries_are_untouched(hass: HomeAssistant) -> None:
    """Entries already at the current version keep their unique IDs."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=ConfigFlow.VERSION,
        data={"username": "u", "password": "p"},
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor", DOMAIN, f"{USAGE_ID}_usage_today", config_entry=entry
    )

    assert await async_migrate_entry(hass, entry)

    assert (
        registry.async_get_entity_id("sensor", DOMAIN, f"{USAGE_ID}_usage_today")
        is not None
    )
    assert entry.version == ConfigFlow.VERSION
