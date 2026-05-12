"""Constants for the Hildebrand Glow (DCC) integration."""

DOMAIN = "hildebrandglow_dcc"

# Virtual Entity Classifiers
ELEC_CONSUMPTION_CLASSIFIER = "electricity.consumption"
ELEC_EXPORT_CLASSIFIER = "electricity.export"
ELEC_EXPORT_REACTIVE_CLASSIFIER = "electricity.export.reactive"
ELEC_IMPORT_REACTIVE_CLASSIFIER = "electricity.import.reactive"
GAS_CONSUMPTION_CLASSIFIER = "gas.consumption"
ELEC_COST_CLASSIFIER = "electricity.consumption.cost"
GAS_COST_CLASSIFIER = "gas.consumption.cost"

# Device Types
ELECTRIC_METER = "electric_meter"
GAS_METER = "gas_meter"

# ADD THESE LINES
CONF_DAILY_INTERVAL = "daily_interval"
CONF_TARIFF_INTERVAL = "tariff_interval"
