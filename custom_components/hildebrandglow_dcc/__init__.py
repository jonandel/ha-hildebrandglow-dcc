"""The Hildebrand Glow (DCC) integration."""

from __future__ import annotations

import logging

from glowmarkt import BrightClient
import requests

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN

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

    # Set API object
    hass.data[DOMAIN][entry.entry_id] = glowmarkt
    _LOGGER.debug("API object stored in hass.data. Forwarding setup to platforms...")

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.debug("Finished async_setup_entry successfully.")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Starting async_unload_entry for %s", DOMAIN)
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
        _LOGGER.debug("Unload successful.")
    else:
        _LOGGER.error("Unload failed.")

    return unload_ok
