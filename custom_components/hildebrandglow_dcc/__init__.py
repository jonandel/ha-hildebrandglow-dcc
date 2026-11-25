"""The Hildebrand Glow (DCC) integration."""

from __future__ import annotations

import logging

from glowmarkt import BrightClient
import requests

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er

from .config_flow import ConfigFlow
from .const import CONF_DAILY_INTERVAL, CONF_TARIFF_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Hildebrand Glow (DCC) from a config entry."""
    _LOGGER.debug("Starting async_setup_entry for %s", DOMAIN)
    hass.data.setdefault(DOMAIN, {})
    # Authenticate with the API
    try:
        _LOGGER.debug("Authenticating with Glowmarkt API...")
        glowmarkt = await hass.async_add_executor_job(
            BrightClient, entry.data["username"], entry.data["password"]
        )
    except requests.exceptions.HTTPError as ex:
        _LOGGER.error(
            "HTTP error during API authentication: Status Code %s - %s",
            ex.response.status_code,
            ex,
        )
        raise ConfigEntryNotReady(f"HTTP Error: {ex.response.status_code}") from ex
    except requests.Timeout as ex:
        _LOGGER.error("Timeout during API authentication: %s", ex)
        raise ConfigEntryNotReady(f"Timeout: {ex}") from ex
    except requests.exceptions.ConnectionError as ex:
        _LOGGER.error("Connection error during API authentication: %s", ex)
        raise ConfigEntryNotReady(f"Cannot connect: {ex}") from ex
    except Exception as ex:  # pylint: disable=broad-except
        _LOGGER.exception("Unexpected exception during API authentication: %s", ex)
        raise ConfigEntryNotReady(f"Unexpected exception: {ex}") from ex
    else:
        _LOGGER.debug("Successful authentication. API object created.")

    # Set API object and config options, using options flow values if they exist
    hass.data[DOMAIN][entry.entry_id] = {
        "client": glowmarkt,
        CONF_DAILY_INTERVAL: entry.options.get(
            CONF_DAILY_INTERVAL, entry.data.get(CONF_DAILY_INTERVAL, 15)
        ),
        CONF_TARIFF_INTERVAL: entry.options.get(
            CONF_TARIFF_INTERVAL, entry.data.get(CONF_TARIFF_INTERVAL, 60)
        ),
    }
    _LOGGER.debug(
        "API object and config stored in hass.data. Forwarding setup to platforms..."
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    _LOGGER.debug("Finished async_setup_entry successfully.")
    return True


def _current_unique_id(entity: er.RegistryEntry) -> str | None:
    """Return the unique ID this integration uses for a HandyHat-named entity."""
    old = entity.unique_id
    if old.endswith("-rate"):
        return f"{old.removesuffix('-rate')}_rate"
    if old.endswith("-tariff"):
        return f"{old.removesuffix('-tariff')}_standing_charge"
    if "_" in old:
        return None
    if entity.original_device_class == "monetary":
        return f"{old}_cost_today"
    if entity.original_device_class == "energy":
        return f"{old}_usage_today"
    return None


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Bring entries from older versions, including HandyHat's version 1, up to date.

    HandyHat named sensors by bare resource ID ("<id>", "<id>-rate", "<id>-tariff").
    Moving them to this integration's IDs keeps their entity IDs, history and
    Energy dashboard settings.
    """
    if entry.version >= ConfigFlow.VERSION:
        return True
    _LOGGER.debug("Migrating config entry from version %s", entry.version)
    registry = er.async_get(hass)

    @callback
    def _migrate(entity: er.RegistryEntry) -> dict[str, str] | None:
        new = _current_unique_id(entity)
        if new is None:
            return None
        if registry.async_get_entity_id(entity.domain, DOMAIN, new):
            _LOGGER.warning(
                "Not migrating %s: unique ID %s is already in use",
                entity.entity_id,
                new,
            )
            return None
        return {"new_unique_id": new}

    await er.async_migrate_entries(hass, entry.entry_id, _migrate)
    hass.config_entries.async_update_entry(entry, version=ConfigFlow.VERSION)
    _LOGGER.info("Migrated config entry to version %s", ConfigFlow.VERSION)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Starting async_unload_entry for %s", DOMAIN)
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
        _LOGGER.debug("Unload successful.")
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)
