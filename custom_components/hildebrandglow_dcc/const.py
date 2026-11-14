"""Constants for the Hildebrand Glow (DCC) integration."""

DOMAIN = "hildebrandglow_dcc"

# Virtual Entity Classifiers
ELEC_CONSUMPTION_CLASSIFIER = "electricity.consumption"
GAS_CONSUMPTION_CLASSIFIER = "gas.consumption"
ELEC_COST_CLASSIFIER = "electricity.consumption.cost"
GAS_COST_CLASSIFIER = "gas.consumption.cost"

# Device Types
ELECTRIC_METER = "electric_meter"
GAS_METER = "gas_meter"

# Options
CONF_DAILY_INTERVAL = "daily_interval"
CONF_TARIFF_INTERVAL = "tariff_interval"
CONF_STATISTICS_INTERVAL = "statistics_interval"
CONF_LOOKBACK_HOURS = "lookback_hours"
CONF_BACKFILL_DAYS = "backfill_days"

DEFAULT_DAILY_INTERVAL = 15
DEFAULT_TARIFF_INTERVAL = 60
# Hourly statistics: poll every hour, re-read the last 3 days on every poll so
# that readings which reach the DCC late are corrected, and backfill 30 days on
# first setup. The DCC keeps roughly 13 months of data.
DEFAULT_STATISTICS_INTERVAL = 60
DEFAULT_LOOKBACK_HOURS = 72
DEFAULT_BACKFILL_DAYS = 30
MAX_LOOKBACK_HOURS = 240
MAX_BACKFILL_DAYS = 400

DEFAULT_OPTIONS = {
    CONF_DAILY_INTERVAL: DEFAULT_DAILY_INTERVAL,
    CONF_TARIFF_INTERVAL: DEFAULT_TARIFF_INTERVAL,
    CONF_STATISTICS_INTERVAL: DEFAULT_STATISTICS_INTERVAL,
    CONF_LOOKBACK_HOURS: DEFAULT_LOOKBACK_HOURS,
    CONF_BACKFILL_DAYS: DEFAULT_BACKFILL_DAYS,
}
