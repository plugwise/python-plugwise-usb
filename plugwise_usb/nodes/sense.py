"""Plugwise Sense node object."""
from __future__ import annotations

from asyncio import create_task
from collections.abc import Callable
from datetime import datetime, UTC
import logging
from typing import Any, Final

from .helpers import raise_not_loaded
from ..api import NodeFeature
from ..exceptions import NodeError
from ..messages.responses import SENSE_REPORT_ID, SenseReportResponse
from ..nodes.sed import NodeSED

_LOGGER = logging.getLogger(__name__)


# Sense calculations
SENSE_HUMIDITY_MULTIPLIER: Final = 125
SENSE_HUMIDITY_OFFSET: Final = 6
SENSE_TEMPERATURE_MULTIPLIER: Final = 175.72
SENSE_TEMPERATURE_OFFSET: Final = 46.85

# Minimum and maximum supported (custom) zigbee protocol version based
# on utc timestamp of firmware
# Extracted from "Plugwise.IO.dll" file of Plugwise source installation
SENSE_FIRMWARE: Final = {

    # pre - internal test release - fixed version
    datetime(2010, 12, 3, 10, 17, 7): (
        "2.0",
        "2.5",
    ),

    # Proto release, with reset and join bug fixed
    datetime(2011, 1, 11, 14, 19, 36): (
        "2.0",
        "2.5",
    ),

    datetime(2011, 3, 4, 14, 52, 30, tzinfo=UTC): ("2.0", "2.5"),
    datetime(2011, 3, 25, 17, 43, 2, tzinfo=UTC): ("2.0", "2.5"),
    datetime(2011, 5, 13, 7, 24, 26, tzinfo=UTC): ("2.0", "2.5"),
    datetime(2011, 6, 27, 8, 58, 19, tzinfo=UTC): ("2.0", "2.5"),

    # Legrand
    datetime(2011, 11, 3, 13, 7, 33, tzinfo=UTC): ("2.0", "2.6"),

    # Radio Test
    datetime(2012, 4, 19, 14, 10, 48, tzinfo=UTC): (
        "2.0",
        "2.5",
    ),

    # New Flash Update
    datetime(2017, 7, 11, 16, 9, 5, tzinfo=UTC): (
        "2.0",
        "2.6",
    ),
}
SENSE_FEATURES: Final = (
    NodeFeature.INFO,
    NodeFeature.TEMPERATURE,
    NodeFeature.HUMIDITY,
)


class PlugwiseSense(NodeSED):
    """provides interface to the Plugwise Sense nodes"""

    _sense_subscription: Callable[[], None] | None = None

    async def load(self) -> bool:
        """Load and activate Sense node features."""
        if self._loaded:
            return True
        self._node_info.battery_powered = True
        if self._cache_enabled:
            _LOGGER.debug(
                "Load Sense node %s from cache", self._node_info.mac
            )
            if await self._load_from_cache():
                self._loaded = True
                self._load_features()
                return True

        _LOGGER.debug("Load of Sense node %s failed", self._node_info.mac)
        return False

    @raise_not_loaded
    async def initialize(self) -> bool:
        """Initialize Sense node."""
        if self._initialized:
            return True
        if not await super().initialize():
            return False
        self._sense_subscription = self._message_subscribe(
            self._sense_report,
            self._mac_in_bytes,
            SENSE_REPORT_ID,
        )
        self._initialized = True
        return True

    def _load_features(self) -> None:
        """Enable additional supported feature(s)"""
        self._setup_protocol(SENSE_FIRMWARE)
        self._features += SENSE_FEATURES
        self._node_info.features = self._features

    async def unload(self) -> None:
        """Unload node."""
        if self._sense_subscription is not None:
            self._sense_subscription()
        await super().unload()

    async def _sense_report(self, message: SenseReportResponse) -> None:
        """
        process sense report message to extract
        current temperature and humidity values.
        """
        self._available_update_state(True)
        if message.temperature.value != 65535:
            self._temperature = int(
                SENSE_TEMPERATURE_MULTIPLIER * (
                    message.temperature.value / 65536
                )
                - SENSE_TEMPERATURE_OFFSET
            )
            create_task(
                self.publish_event(NodeFeature.TEMPERATURE, self._temperature)
            )

        if message.humidity.value != 65535:
            self._humidity = int(
                SENSE_HUMIDITY_MULTIPLIER * (message.humidity.value / 65536)
                - SENSE_HUMIDITY_OFFSET
            )
            create_task(
                self.publish_event(NodeFeature.HUMIDITY, self._humidity)
            )

    async def get_state(
        self, features: tuple[NodeFeature]
    ) -> dict[NodeFeature, Any]:
        """Update latest state for given feature."""
        if not self._loaded:
            if not await self.load():
                _LOGGER.warning(
                    "Unable to update state because load node %s failed",
                    self.mac
                )
        states: dict[NodeFeature, Any] = {}
        for feature in features:
            _LOGGER.debug(
                "Updating node %s - feature '%s'",
                self._node_info.mac,
                feature,
            )
            if feature not in self._features:
                raise NodeError(
                    f"Update of feature '{feature.name}' is "
                    + f"not supported for {self.mac}"
                )
            if feature == NodeFeature.TEMPERATURE:
                states[NodeFeature.TEMPERATURE] = self._temperature
            elif feature == NodeFeature.HUMIDITY:
                states[NodeFeature.HUMIDITY] = self._humidity
            elif feature == NodeFeature.PING:
                states[NodeFeature.PING] = await self.ping_update()
            else:
                state_result = await super().get_state([feature])
                states[feature] = state_result[feature]

        return states
