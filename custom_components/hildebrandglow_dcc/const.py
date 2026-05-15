"""Constants for the Hildebrand Glow (DCC) integration."""

DOMAIN = "hildebrandglow_dcc"

# Configuration Entry Keys
CONF_DAILY_INTERVAL = "daily_interval"
CONF_TARIFF_INTERVAL = "tariff_interval"

# Resource Classifiers - Electricity
ELEC_CONSUMPTION_CLASSIFIER = "electricity.consumption"
ELEC_EXPORT_CLASSIFIER = "electricity.export"
ELEC_IMPORT_REACTIVE_CLASSIFIER = "electricity.import.reactive"
ELEC_EXPORT_REACTIVE_CLASSIFIER = "electricity.export.reactive"

# Resource Classifiers - Gas
GAS_CONSUMPTION_CLASSIFIER = "gas.consumption"

# Note: Cost and Tariff resources are derived from these base classifiers 
# in the sensor logic (e.g., electricity.consumption.cost)
