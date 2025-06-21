"""Plugwise Stick constants."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Final

LOGGER = logging.getLogger(__name__)

# Cache folder name
CACHE_DIR: Final = ".plugwise-cache"
CACHE_KEY_SEPARATOR: str = ";"
CACHE_DATA_SEPARATOR: str = "|"

LOCAL_TIMEZONE = dt.datetime.now(dt.UTC).astimezone().tzinfo
UTF8: Final = "utf-8"

# Value limits
MAX_UINT_2: Final = 255  # 8-bit unsigned integer max
MAX_UINT_4: Final = 65535  # 16-bit unsigned integer max

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
# Stick responds with timeout messages within 10s.
STICK_TIME_OUT: Final = 11
# In bigger networks a response from a Node could take up a while, so lets use 15 seconds.
NODE_TIME_OUT: Final = 15

MAX_RETRIES: Final = 3
SUPPRESS_INITIALIZATION_WARNINGS: Final = 10  # Minutes to suppress (expected) communication warning messages after initialization

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
    "120041": "Circle + Legrand type E",
    "120000": "Circle + Legrand type F",
    "090000": "Circle + type B",
    "090007": "Circle + type B",
    "090088": "Circle + type E",
    "070073": "Circle + type F",
    "090048": "Circle + type G",
    "090188": "Stealth +",
    "120049": "Stealth M+",
    "120029": "Stealth Legrand",
    "100025": "Circle",
    "120040": "Circle Legrand type E",
    "120001": "Circle Legrand type F",
    "090079": "Circle type B",
    "090087": "Circle type E",
    "070140": "Circle type F",
    "090093": "Circle type G",
    "090011": "Stealth",
    "001200": "Stealth",
    "120048": "Stealth M",
    "080007": "Scan",
    "110028": "Scan Legrand",
    "070030": "Sense",
    "120006": "Sense Legrand",
    "070051": "Switch",
    "080029": "Switch",
}

TYPE_MODEL: Final[dict[int, tuple[str]]] = {
    0: ("Stick",),
    1: ("Circle", "Stealth"),
    3: ("Switch",),
    5: ("Sense",),
    6: ("Scan",),
    7: ("Celsius",),
    8: ("Celsius",),
    9: ("Stealth",),
}

# Energy logging intervals
DEFAULT_CONS_INTERVAL: Final[int] = 60
NO_PRODUCTION_INTERVAL: Final[int] = 0

# Energy Node types
ENERGY_NODE_TYPES: tuple[int] = (1, 2, 9)
