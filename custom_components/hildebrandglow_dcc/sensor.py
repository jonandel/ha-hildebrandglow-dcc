"""Platform for sensor integration."""

from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections.abc import Callable
from datetime import datetime, time, timedelta
import logging

import requests
from requests.exceptions import ConnectionError, HTTPError, Timeout

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DAILY_INTERVAL,
    CONF_TARIFF_INTERVAL,
    DOMAIN,
    ELEC_CONSUMPTION_CLASSIFIER,
    ELEC_EXPORT_CLASSIFIER,
    ELEC_EXPORT_REACTIVE_CLASSIFIER,
    ELEC_IMPORT_REACTIVE_CLASSIFIER,
)

_LOGGER = logging.getLogger(__name__)

# --- COORDINATOR CLASSES ---

class DataCoordinator(DataUpdateCoordinator):
    """Data update coordinator for daily usage and cost sensors."""

    def __init__(self, hass: HomeAssistant, glowmarkt_resource, daily_interval):
        """
        Initialize daily data coordinator.
        
        This matches your original working signature (hass, resource, interval).
        Keeping this stable ensures Pass 1 and Pass 2 discovery works 
        without 'hosing' the integration setup.
        """
        self.resource = glowmarkt_resource
        super().__init__(
            hass,
            _LOGGER,
            name=f"Daily Data {glowmarkt_resource.classifier}",
            update_interval=timedelta(minutes=daily_interval),
        )

    async def _async_update_data(self):
        """
        Fetch data from the daily usage helper.
        
        The telemetry logic is placed inside the 'daily_data' helper 
        so that this class remains compatible with the standard 
        Home Assistant setup calls.
        """
        try:
            value = await daily_data(self.hass, self.resource)
            # Returning None if the API fails ensures the sensor 
            # shows 'Unknown' or 'Unavailable' rather than 0.
            return value
        except Exception as ex:
            _LOGGER.exception("Unexpected exception in coordinator for %s: %s", self.resource.id, ex)
            raise UpdateFailed(f"Error fetching data: {ex}") from ex

class TariffCoordinator(DataUpdateCoordinator):
    """Data update coordinator for the tariff sensors."""

    def __init__(self, hass: HomeAssistant, resource, tariff_interval) -> None:
        """Initialize tariff coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"Tariff Data {resource.classifier}",
            update_interval=timedelta(minutes=tariff_interval),
        )
        self.resource = resource

    async def _async_update_data(self):
        """Fetch data from tariff API endpoint."""
        try:
            tariff = await tariff_data(self.hass, self.resource)
            if tariff is None:
                raise UpdateFailed(f"No tariff data received for {self.resource.classifier}")
            return tariff
        except Exception as ex:
            _LOGGER.exception("Error fetching tariff data: %s", ex)
            raise UpdateFailed(f"Failed to fetch tariff data: {ex}") from ex

# --- HELPER FUNCTIONS ---

def supply_type(resource) -> str:
    """Return supply type."""
    if resource.classifier == ELEC_EXPORT_REACTIVE_CLASSIFIER:
        return "electricity export reactive"
    if resource.classifier == ELEC_IMPORT_REACTIVE_CLASSIFIER:
        return "electricity import reactive"
    if resource.classifier == ELEC_EXPORT_CLASSIFIER:
        return "electricity export"
    if "electricity.consumption" in resource.classifier:
        return "electricity"
    if "gas.consumption" in resource.classifier:
        return "gas"
    return "unknown"

def device_name(resource, virtual_entity) -> str:
    """Return device name."""
    supply = supply_type(resource)
    if virtual_entity.name is not None:
        name = f"{virtual_entity.name} smart {supply} meter"
    else:
        name = f"Smart {supply} meter"
    return name

async def daily_data(hass: HomeAssistant, resource) -> float:
    """Get Sum for the day with telemetry that doesn't break the coordinator."""
    now = dt_util.utcnow()
    utc_offset = -int(dt_util.now().utcoffset().total_seconds() / 60)

    try:
        await hass.async_add_executor_job(resource.catchup)
    except Exception:
        pass

    t_from = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(minutes=utc_offset)
    t_to = now.replace(second=0, microsecond=0)

    try:
        readings = await hass.async_add_executor_job(
            resource.get_readings, t_from, t_to, "P1D", "sum", utc_offset
        )
        
        # --- SAFE TELEMETRY ---
        # We derive the 'English' name from the resource's known attributes 
        # and the supply_type helper we already have.
        sensor_type = supply_type(resource)
        
        # If resource.id matches a known GUID, we can label the meter
        meter_label = "HVR" if "e1c26e53" in resource.id or "f205ed21" in resource.id else "MH"
        
        _LOGGER.info(
            "POLL [%s] | %s | RAW: %s", 
            meter_label, sensor_type, readings
        )
        
        if readings and readings[0][1].value is not None:
            v = float(readings[0][1].value)
            if len(readings) > 1 and readings[1][1].value is not None:
                v += float(readings[1][1].value)
            return v
            
    except Exception as ex:
        _LOGGER.error("Fetch failed for %s: %s", resource.classifier, ex)
        return None
            
    except Exception as ex:
        _LOGGER.error("Fetch failed for %s (%s): %s", ve_name, resource.classifier, ex)
        return None

async def tariff_data(hass: HomeAssistant, resource):
    """Get tariff data from the API."""
    try:
        return await hass.async_add_executor_job(resource.get_tariff)
    except Exception:
        return None

async def _delayed_first_refresh(coordinator: DataUpdateCoordinator, delay: int = 5):
    """Perform first refresh after a delay."""
    await asyncio.sleep(delay)
    await coordinator.async_request_refresh()

# --- SENSOR BASE CLASS ---

class GlowDCCSensor(CoordinatorEntity, SensorEntity, ABC):
    """Base class. Groups sensors under the 'parent' meter if self.meter is set."""

    def __init__(self, coordinator: DataUpdateCoordinator, resource, virtual_entity) -> None:
        super().__init__(coordinator)
        self.resource = resource
        self.virtual_entity = virtual_entity
        self.meter = None  # Will be set to the parent Usage sensor for grouping

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information (Restored for grouping)."""
        target_res = self.meter.resource if (self.meter and self.meter.resource) else self.resource
        return DeviceInfo(
            identifiers={(DOMAIN, target_res.id)},
            manufacturer="Hildebrand",
            model="Glow (DCC)",
            name=device_name(target_res, self.virtual_entity),
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self.coordinator.data is not None:
            self._update_native_value(self.coordinator.data)
        self.async_write_ha_state()

    @abstractmethod
    def _update_native_value(self, data):
        pass

# --- SENSOR CLASSES ---

class Usage(GlowDCCSensor):
    """Sensor object for daily usage."""
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_has_entity_name = True
    _attr_name = "Usage (today)"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator, resource, virtual_entity) -> None:
        super().__init__(coordinator, resource, virtual_entity)
        self._attr_unique_id = f"{resource.id}_usage_today"
        self._attr_state_class = SensorStateClass.TOTAL if resource.classifier == ELEC_CONSUMPTION_CLASSIFIER else SensorStateClass.TOTAL_INCREASING

    @callback
    def _update_native_value(self, data: float) -> None:
        # BETA FIX: Floor negative values at zero to prevent dashboard spikes
        self._attr_native_value = round(max(0, data), 2)

class UsageRolling(RestoreEntity, GlowDCCSensor):
    """Cumulative sensor that bootstraps without doubling up."""
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator, resource, virtual_entity) -> None:
        super().__init__(coordinator, resource, virtual_entity)
        self._attr_name = "Usage (cumulative rolling)"
        self._attr_unique_id = f"{resource.id}_usage_rolling"
        self._acc = 0.0
        self._prev = 0.0
        self._bootstrapped = False # New flag to prevent doubling

    async def async_added_to_hass(self) -> None:
        """Restore state from the database."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            try:
                # This is our total lifetime 'Peak'
                self._acc = float(last_state.state)
                _LOGGER.debug("Restored rolling accumulator for %s to %s", self.entity_id, self._acc)
            except (ValueError, TypeError):
                self._acc = 0.0

    @callback
    def _update_native_value(self, data: float) -> None:
        current_data = max(0.0, data)

        # FIRST RUN LOGIC: 
        # If this is the first poll after a restart/re-add, 
        # we don't add 'data' to '_acc' because 'data' is already 
        # PART of the restored '_acc' value.
        if not self._bootstrapped:
            # We set our 'baseline' for today without adding to the total
            self._acc = self._acc - current_data 
            self._bootstrapped = True
            _LOGGER.debug("Bootstrapped %s: adjusted baseline to %s", self.entity_id, self._acc)

        # Standard midnight reset detection
        if current_data < self._prev and self._prev > 0:
            self._acc += self._prev
        
        self._prev = current_data
        self._attr_native_value = round(self._acc + current_data, 3)

class ExportUsage(GlowDCCSensor):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_has_entity_name = True
    _attr_name = "Export (today)"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator, resource, virtual_entity) -> None:
        super().__init__(coordinator, resource, virtual_entity)
        self._attr_unique_id = f"{resource.id}_export_today"

    @callback
    def _update_native_value(self, data: float) -> None:
        # BETA FIX: Floor negative values at zero
        self._attr_native_value = round(max(0, data), 2)

class ReactiveEnergyToday(GlowDCCSensor):
    _attr_native_unit_of_measurement = "kVArh"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, resource, virtual_entity) -> None:
        super().__init__(coordinator, resource, virtual_entity)
        self._attr_name = "Reactive import (today)" if resource.classifier == ELEC_IMPORT_REACTIVE_CLASSIFIER else "Reactive export (today)"
        self._attr_unique_id = f"{resource.id}_reactive_{resource.classifier}_today"

    @callback
    def _update_native_value(self, data: float) -> None:
        self._attr_native_value = round(max(0, data), 2)

class Cost(GlowDCCSensor):
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_has_entity_name = True
    _attr_name = "Cost (today)"
    _attr_native_unit_of_measurement = "GBP"
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, coordinator, resource, virtual_entity) -> None:
        super().__init__(coordinator, resource, virtual_entity)
        self._attr_unique_id = f"{resource.id}_cost_today"

    @callback
    def _update_native_value(self, data: float) -> None:
        self._attr_native_value = round(data / 100, 2)

class Standing(CoordinatorEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_has_entity_name = True
    _attr_name = "Standing charge"
    _attr_native_unit_of_measurement = "GBP"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, resource, virtual_entity) -> None:
        super().__init__(coordinator)
        self.resource = resource
        self.virtual_entity = virtual_entity
        self.meter = None
        self._attr_unique_id = f"{resource.id}_standing_charge"

    @callback
    def _handle_coordinator_update(self) -> None:
        if self.coordinator.data:
            try:
                val = self.coordinator.data.current_rates.standing_charge.value
                if val is not None:
                    self._attr_native_value = round(float(val) / 100, 4)
                    self.async_write_ha_state()
            except Exception: pass

    @property
    def device_info(self) -> DeviceInfo:
        target = self.meter.resource if (self.meter and self.meter.resource) else self.resource
        return DeviceInfo(identifiers={(DOMAIN, target.id)}, name=device_name(target, self.virtual_entity), manufacturer="Hildebrand", model="Glow (DCC)")

class Rate(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Rate"
    _attr_native_unit_of_measurement = "GBP/kWh"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, resource, virtual_entity) -> None:
        super().__init__(coordinator)
        self.resource = resource
        self.virtual_entity = virtual_entity
        self.meter = None
        self._attr_unique_id = f"{resource.id}_rate"

    @callback
    def _handle_coordinator_update(self) -> None:
        if self.coordinator.data:
            try:
                val = self.coordinator.data.current_rates.rate.value
                if val is not None:
                    self._attr_native_value = round(float(val) / 100, 4)
                    self.async_write_ha_state()
            except Exception: pass

    @property
    def device_info(self) -> DeviceInfo:
        target = self.meter.resource if (self.meter and self.meter.resource) else self.resource
        return DeviceInfo(identifiers={(DOMAIN, target.id)}, name=device_name(target, self.virtual_entity), manufacturer="Hildebrand", model="Glow (DCC)")

# --- ASYNC SETUP ENTRY ---

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: Callable) -> bool:
    glowmarkt = hass.data[DOMAIN][entry.entry_id]["client"]
    daily_int = hass.data[DOMAIN][entry.entry_id].get(CONF_DAILY_INTERVAL, 15)
    tariff_int = hass.data[DOMAIN][entry.entry_id].get(CONF_TARIFF_INTERVAL, 60)

    virtual_entities = await hass.async_add_executor_job(glowmarkt.get_virtual_entities)
    
    entities = []
    primary_meters = {}

    for ve in virtual_entities:
        resources = await hass.async_add_executor_job(ve.get_resources)
        
        # Pass 1: Setup Main Meters
        for res in resources:
            if res.classifier in (ELEC_CONSUMPTION_CLASSIFIER, "gas.consumption"):
                d_coord = DataCoordinator(hass, res, daily_int)
                t_coord = TariffCoordinator(hass, res, tariff_int)
                
                usage = Usage(d_coord, res, ve)
                rolling = UsageRolling(d_coord, res, ve)
                rolling.meter = usage
                
                primary_meters[res.classifier] = usage
                
                entities.extend([usage, rolling])
                
                s_charge = Standing(t_coord, res, ve)
                r_charge = Rate(t_coord, res, ve)
                s_charge.meter = usage
                r_charge.meter = usage
                entities.extend([s_charge, r_charge])
                
                hass.async_create_task(_delayed_first_refresh(d_coord, 5))
                hass.async_create_task(_delayed_first_refresh(t_coord, 10))

        # Pass 2: Secondary Sensors
        for res in resources:
            if res.classifier in (ELEC_CONSUMPTION_CLASSIFIER, "gas.consumption"): continue
            
            parent = primary_meters.get("electricity.consumption") if "electricity" in res.classifier else primary_meters.get("gas.consumption")
            
            d_coord = DataCoordinator(hass, res, daily_int)
            new_s = None
            
            if res.classifier == ELEC_EXPORT_CLASSIFIER:
                new_s = ExportUsage(d_coord, res, ve)
            elif "reactive" in res.classifier:
                new_s = ReactiveEnergyToday(d_coord, res, ve)
            elif "cost" in res.classifier:
                new_s = Cost(d_coord, res, ve)
            
            if new_s:
                new_s.meter = parent
                entities.append(new_s)
                hass.async_create_task(_delayed_first_refresh(d_coord, 8))

    async_add_entities(entities)
    return True
