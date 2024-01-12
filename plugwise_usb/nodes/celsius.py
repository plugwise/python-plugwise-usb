"""
Plugwise Celsius node object.

TODO: Finish node
"""
from __future__ import annotations

from datetime import datetime
import logging
from typing import Final

from ..api import NodeFeature
from ..nodes.sed import NodeSED

_LOGGER = logging.getLogger(__name__)

# Minimum and maximum supported (custom) zigbee protocol version based
# on utc timestamp of firmware
# Extracted from "Plugwise.IO.dll" file of Plugwise source installation
FIRMWARE_CELSIUS: Final = {
    # Celsius Proto
    datetime(2013, 9, 25, 15, 9, 44): ("2.0", "2.6"),

    datetime(2013, 10, 11, 15, 15, 58): ("2.0", "2.6"),
    datetime(2013, 10, 17, 10, 13, 12): ("2.0", "2.6"),
    datetime(2013, 11, 19, 17, 35, 48): ("2.0", "2.6"),
    datetime(2013, 12, 5, 16, 25, 33): ("2.0", "2.6"),
    datetime(2013, 12, 11, 10, 53, 55): ("2.0", "2.6"),
    datetime(2014, 1, 30, 8, 56, 21): ("2.0", "2.6"),
    datetime(2014, 2, 3, 10, 9, 27): ("2.0", "2.6"),
    datetime(2014, 3, 7, 16, 7, 42): ("2.0", "2.6"),
    datetime(2014, 3, 24, 11, 12, 23): ("2.0", "2.6"),

    # MSPBootloader Image - Required to allow
    # a MSPBootload image for OTA update
    datetime(2014, 4, 14, 15, 45, 26): (
        "2.0",
        "2.6",
    ),

    # CelsiusV Image
    datetime(2014, 7, 23, 19, 24, 18): ("2.0", "2.6"),

    # CelsiusV Image
    datetime(2014, 9, 12, 11, 36, 40): ("2.0", "2.6"),

    # New Flash Update
    datetime(2017, 7, 11, 16, 2, 50): ("2.0", "2.6"),
}
CELSIUS_FEATURES: Final = (
    NodeFeature.INFO,
    NodeFeature.TEMPERATURE,
    NodeFeature.HUMIDITY,
)


class PlugwiseCelsius(NodeSED):
    """provides interface to the Plugwise Celsius nodes"""

    async def load(self) -> bool:
        """Load and activate node features."""
        if self._loaded:
            return True
        self._node_info.battery_powered = True
        if self._cache_enabled:
            _LOGGER.debug(
                "Load Celsius node %s from cache", self._node_info.mac
            )
            if await self._load_from_cache():
                self._loaded = True
                self._load_features()
                return await self.initialize()

        _LOGGER.debug("Load of Celsius node %s failed", self._node_info.mac)
        return False
