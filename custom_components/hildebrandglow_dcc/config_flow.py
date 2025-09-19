"""Config flow for Hildebrand Glow (DCC) integration."""

from __future__ import annotations

import logging
from typing import Any

from glowmarkt import BrightClient
import requests
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult

from .const import CONF_DAILY_INTERVAL, CONF_TARIFF_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("username"): str,
        vol.Required("password"): str,
    }
)

OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_DAILY_INTERVAL): vol.All(vol.Coerce(int), vol.Range(min=1)),
        vol.Optional(CONF_TARIFF_INTERVAL): vol.All(vol.Coerce(int), vol.Range(min=1)),
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
    try:
        glowmarkt = await hass.async_add_executor_job(
            BrightClient, data["username"], data["password"]
        )
    except (requests.Timeout, requests.exceptions.ConnectionError, ValueError) as ex:
        _LOGGER.error("Authentication failed: %s", ex)
        raise ValueError("Authentication failed") from ex

    _LOGGER.debug("Successful Post to %sauth", glowmarkt.url)

    # Return title of the entry to be added
    return {"title": "Hildebrand Glow (DCC)"}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Hildebrand Glow (DCC)."""

    VERSION = 6

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA
            )

        errors = {}

        try:
            info = await validate_input(self.hass, user_input)
        except requests.Timeout as ex:
            _LOGGER.debug("Timeout: %s", ex)
            errors["base"] = "timeout_connect"
        except requests.exceptions.ConnectionError as ex:
            _LOGGER.debug("Cannot connect: %s", ex)
            errors["base"] = "cannot_connect"
        except ValueError:
            _LOGGER.debug("Authentication Failed")
            errors["base"] = "invalid_auth"
        except Exception as ex:
            _LOGGER.exception("Unexpected exception: %s", ex)
            errors["base"] = "unknown"
        else:
            return self.async_create_entry(
                title=info["title"],
                data=user_input,
                options={CONF_DAILY_INTERVAL: 15, CONF_TARIFF_INTERVAL: 60},
            )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        """Get the options flow for this handler.

        The Home Assistant core calls this method, passing the config_entry.
        The OptionsFlowHandler is then instantiated and the framework
        internally handles providing the config_entry to it.
        """
        return OptionsFlowHandler()

    async def async_migrate_entry(self, config_entry: config_entries.ConfigEntry):
        """Migrate old entry."""
        _LOGGER.debug("Migrating from version %s", config_entry.version)

        # In this simple case, we are just bumping the version and the data structure has not changed - with the exception of version 1.1.5 and 1.1.6 preview, which
        # introduced the polling frequency values - which were then remove, and added as options in the Options workflow handler....
        # However v1.3.0 bumped the version config_flow number to 6, so we need to maintain that number or higher going forwards.
        # Home Assistant requires this method to exist to prevent a migration error when the version number changes.

        # When moving from a new version to an old one, the existing entry's data is
        # incompatible. To handle a downgrade, you would need to add logic here to
        # transform the new data back to the old format.
        # Example:
        # if config_entry.version == 2:
        #     new_data = {**config_entry.data}
        #     # Add new logic here to transform the data.
        #     config_entry.version = 1
        #     self.hass.config_entries.async_update_entry(config_entry, data=new_data)

        # For a simple version bump with no data change, just return True.

        _LOGGER.info(
            "Migration successful for config entry version %s", config_entry.version
        )
        return True


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle an options flow for Hildebrand Glow (DCC)."""

    async def async_step_init(self, user_input=None):
        """Handle the options flow."""
        errors = {}

        if user_input is not None:
            daily_interval = user_input.get(CONF_DAILY_INTERVAL)
            tariff_interval = user_input.get(CONF_TARIFF_INTERVAL)

            daily_interval = int(daily_interval) if daily_interval is not None else 1
            tariff_interval = int(tariff_interval) if tariff_interval is not None else 1

            if daily_interval < 5 or tariff_interval < 5:
                errors["base"] = (
                    "Intervals of less than 5 minutes are not allowed to protect the Hildebrand Glow API from being overloaded."
                )

            if not errors:
                return self.async_create_entry(data=user_input)

        data_schema = self.add_suggested_values_to_schema(
            OPTIONS_SCHEMA,
            self.config_entry.options,
        )

        return self.async_show_form(
            step_id="init", data_schema=data_schema, errors=errors
        )
