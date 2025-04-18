"""Plugwise Stick constants."""
from __future__ import annotations

import datetime as dt
from enum import Enum, auto
import logging
from typing import Final

LOGGER = logging.getLogger(__name__)

# Cache folder name
CACHE_DIR: Final = ".plugwise-cache"
CACHE_KEY_SEPARATOR: str = ";"
CACHE_DATA_SEPARATOR: str = "|"

LOCAL_TIMEZONE = dt.datetime.now(dt.UTC).astimezone().tzinfo
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
STICK_TIME_OUT: Final = 11  # Stick responds with timeout messages within 10s.
NODE_TIME_OUT: Final = 15  # In bigger networks a response from a node could take up a while, so lets use 15 seconds.
MAX_RETRIES: Final = 3

# plugwise year information is offset from y2k
PLUGWISE_EPOCH: Final = 2000
PULSES_PER_KW_SECOND: Final = 468.9385193

# Energy log memory addresses
LOGADDR_OFFSET: Final = 278528  # = b"00044000"
LOGADDR_MAX: Final = 6016  # last address for energy log

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
    """Motion sensitivity levels for Scan devices."""

    HIGH = auto()
    MEDIUM = auto()
    OFF = auto()
