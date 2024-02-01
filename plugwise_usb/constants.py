"""Plugwise Stick constants."""
from __future__ import annotations

import datetime as dt
from enum import Enum, auto
import logging
from typing import Final

LOGGER = logging.getLogger(__name__)

# Cache folder name
CACHE_DIR: Final = ".plugwise-cache"
CACHE_SEPARATOR: str = ";"

# Copied homeassistant.consts
ATTR_NAME: Final = "name"
ATTR_STATE: Final = "state"
ATTR_STATE_CLASS: Final = "state_class"
ATTR_UNIT_OF_MEASUREMENT: Final = "unit_of_measurement"
DEGREE: Final = "°"
ELECTRIC_POTENTIAL_VOLT: Final = "V"
ENERGY_KILO_WATT_HOUR: Final = "kWh"
ENERGY_WATT_HOUR: Final = "Wh"
PERCENTAGE: Final = "%"
POWER_WATT: Final = "W"
PRESET_AWAY: Final = "away"
PRESSURE_BAR: Final = "bar"
SIGNAL_STRENGTH_DECIBELS_MILLIWATT: Final = "dBm"
TEMP_CELSIUS: Final = "°C"
TEMP_KELVIN: Final = "°K"
TIME_MILLISECONDS: Final = "ms"
UNIT_LUMEN: Final = "lm"
VOLUME_CUBIC_METERS: Final = "m³"
VOLUME_CUBIC_METERS_PER_HOUR: Final = "m³/h"

LOCAL_TIMEZONE = dt.datetime.now(dt.timezone.utc).astimezone().tzinfo
UTF8: Final = "utf-8"

# Time
DAY_IN_HOURS: Final = 24
WEEK_IN_HOURS: Final = 168
DAY_IN_MINUTES: Final = 1440
HOUR_IN_MINUTES: Final = 60
DAY_IN_SECONDS: Final = 86400
HOUR_IN_SECONDS: Final = 3600
MINUTE_IN_SECONDS: Final = 60
SECOND_IN_NANOSECONDS: Final = 1000000000

# Plugwise message identifiers
MESSAGE_FOOTER: Final = b"\x0d\x0a"
MESSAGE_HEADER: Final = b"\x05\x05\x03\x03"

# Max timeout in seconds
STICK_ACCEPT_TIME_OUT: Final = 6  # Stick accept respond.
STICK_TIME_OUT: Final = 15  # Stick responds with timeout messages after 10s.
QUEUE_TIME_OUT: Final = 45  # Total seconds to wait for queue
NODE_TIME_OUT: Final = 20
DISCOVERY_TIME_OUT: Final = 45
REQUEST_TIMEOUT: Final = 0.5
MAX_RETRIES: Final = 3

# Default sleep between sending messages
SLEEP_TIME: Final = 0.01

# plugwise year information is offset from y2k
PLUGWISE_EPOCH: Final = 2000
PULSES_PER_KW_SECOND: Final = 468.9385193

# Energy log memory addresses
LOGADDR_OFFSET: Final = 278528  # = b"00044000"
LOGADDR_MAX: Final = 65535  # TODO: Determine last log address, not used yet

# Max seconds the internal clock of plugwise nodes
# are allowed to drift in seconds
MAX_TIME_DRIFT: Final = 5

# Duration updates of node states
NODE_CACHE: Final = dt.timedelta(seconds=5)

# Minimal time between power updates in seconds
MINIMAL_POWER_UPDATE: Final = 5

# Hardware models based
HW_MODELS: Final[dict[str, str]] = {
    "038500": "Stick",
    "070085": "Stick",
    "120002": "Stick Legrand",
    "120041": "Circle+ Legrand type E",
    "120000": "Circle+ Legrand type F",
    "090000": "Circle+ type B",
    "090007": "Circle+ type B",
    "090088": "Circle+ type E",
    "070073": "Circle+ type F",
    "090048": "Circle+ type G",
    "120049": "Stealth M+",
    "090188": "Stealth+",
    "120040": "Circle Legrand type E",
    "120001": "Circle Legrand type F",
    "090079": "Circle type B",
    "090087": "Circle type E",
    "070140": "Circle type F",
    "090093": "Circle type G",
    "100025": "Circle",
    "120048": "Stealth M",
    "120029": "Stealth Legrand",
    "090011": "Stealth",
    "001200": "Stealth",
    "080007": "Scan",
    "110028": "Scan Legrand",
    "070030": "Sense",
    "120006": "Sense Legrand",
    "070051": "Switch",
    "080029": "Switch",
}


class MotionSensitivity(Enum):
    """Motion sensitivity levels for Scan devices"""

    HIGH = auto()
    MEDIUM = auto()
    OFF = auto()
