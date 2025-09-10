"""Constants for the Hildebrand Glow (DCC) integration."""

DOMAIN = "hildebrandglow_dcc"

# Config Flow
CONF_DAILY_INTERVAL = "daily_refresh_interval_minutes"
CONF_TARIFF_INTERVAL = "tariff_refresh_interval_minutes"

# Virtual Entity Classifiers
ELEC_CONSUMPTION_CLASSIFIER = "electricity.consumption"
GAS_CONSUMPTION_CLASSIFIER = "gas.consumption"
ELEC_COST_CLASSIFIER = "electricity.consumption.cost"
GAS_COST_CLASSIFIER = "gas.consumption.cost"

# Device Types
ELECTRIC_METER = "electric_meter"
GAS_METER = "gas_meter"
