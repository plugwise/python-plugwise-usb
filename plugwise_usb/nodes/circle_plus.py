"""Plugwise Circle+ node object."""

from __future__ import annotations

from datetime import datetime, UTC
import logging
from typing import Final

from .helpers import raise_not_loaded
from ..api import NodeFeature
from ..constants import MAX_TIME_DRIFT
from ..messages.requests import (
    CirclePlusRealTimeClockGetRequest,
    CirclePlusRealTimeClockSetRequest,
)
from ..messages.responses import (
    CirclePlusRealTimeClockResponse,
    NodeResponse,
    NodeResponseType,
)
from .circle import CIRCLE_FEATURES, PlugwiseCircle

_LOGGER = logging.getLogger(__name__)

# Minimum and maximum supported (custom) zigbee protocol version based
# on utc timestamp of firmware
# Extracted from "Plugwise.IO.dll" file of Plugwise source installation
CIRCLE_PLUS_FIRMWARE: Final = {
    datetime(2008, 8, 26, 15, 46, tzinfo=UTC): ("1.0", "1.1"),
    datetime(2009, 9, 8, 14, 0, 32, tzinfo=UTC): ("2.0", "2.4"),
    datetime(2010, 4, 27, 11, 54, 15, tzinfo=UTC): ("2.0", "2.4"),
    datetime(2010, 8, 4, 12, 56, 59, tzinfo=UTC): ("2.0", "2.4"),
    datetime(2010, 8, 17, 7, 37, 57, tzinfo=UTC): ("2.0", "2.4"),
    datetime(2010, 8, 31, 10, 9, 18, tzinfo=UTC): ("2.0", "2.4"),
    datetime(2010, 10, 7, 14, 49, 29, tzinfo=UTC): ("2.0", "2.4"),
    datetime(2010, 11, 1, 13, 24, 49, tzinfo=UTC): ("2.0", "2.4"),
    datetime(2011, 3, 25, 17, 37, 55, tzinfo=UTC): ("2.0", "2.5"),
    datetime(2011, 5, 13, 7, 17, 7, tzinfo=UTC): ("2.0", "2.5"),
    datetime(2011, 6, 27, 8, 47, 37, tzinfo=UTC): ("2.0", "2.5"),
    # Legrand
    datetime(2011, 11, 3, 12, 55, 23, tzinfo=UTC): ("2.0", "2.6"),
    # Radio Test
    datetime(2012, 4, 19, 14, 3, 55, tzinfo=UTC): ("2.0", "2.5"),
    # SMA firmware 2015-06-16
    datetime(2015, 6, 18, 14, 42, 54, tzinfo=UTC): (
        "2.0",
        "2.6",
    ),
    # New Flash Update
    datetime(2017, 7, 11, 16, 5, 57, tzinfo=UTC): (
        "2.0",
        "2.6",
    ),
}


class PlugwiseCirclePlus(PlugwiseCircle):
    """provides interface to the Plugwise Circle+ nodes"""

    async def async_load(self) -> bool:
        """Load and activate Circle+ node features."""
        if self._loaded:
            return True
        if self._cache_enabled:
            _LOGGER.debug(
                "Load Circle node %s from cache", self._node_info.mac
            )
            if await self._async_load_from_cache():
                self._loaded = True
                self._load_features()
                return await self.async_initialize()
            _LOGGER.warning(
                "Load Circle+ node %s from cache failed",
                self._node_info.mac,
            )
        else:
            _LOGGER.debug("Load Circle+ node %s", self._node_info.mac)

        # Check if node is online
        if not self._available and not await self.async_is_online():
            _LOGGER.warning(
                "Failed to load Circle+ node %s because it is not online",
                self._node_info.mac
            )
            return False

        # Get node info
        if not await self.async_node_info_update():
            _LOGGER.warning(
                "Failed to load Circle+ node %s because it is not responding"
                + " to information request",
                self._node_info.mac
            )
            return False
        self._loaded = True
        self._load_features()
        return await self.async_initialize()

    @raise_not_loaded
    async def async_initialize(self) -> bool:
        """Initialize node."""
        if self._initialized:
            return True
        self._initialized = True
        if not self._available:
            self._initialized = False
            return False
        if not self._calibration and not await self.async_calibration_update():
            self._initialized = False
            return False
        if not await self.async_realtime_clock_synchronize():
            self._initialized = False
            return False
        if (
            NodeFeature.RELAY_INIT in self._features and
            self._relay_init_state is None and
            not await self.async_relay_init_update()
        ):
            self._initialized = False
            return False
        self._initialized = True
        return True

    def _load_features(self) -> None:
        """Enable additional supported feature(s)"""
        self._setup_protocol(CIRCLE_PLUS_FIRMWARE)
        self._features += CIRCLE_FEATURES
        if (
            self._node_protocols is not None and
            "2.6" in self._node_protocols
        ):
            self._features += (NodeFeature.RELAY_INIT,)
        self._node_info.features = self._features

    async def async_realtime_clock_synchronize(self) -> bool:
        """Synchronize realtime clock."""
        clock_response: CirclePlusRealTimeClockResponse | None = (
            await self._send(
                CirclePlusRealTimeClockGetRequest(self._mac_in_bytes)
            )
        )
        if clock_response is None:
            _LOGGER.debug(
                "No response for async_realtime_clock_synchronize() for %s",
                self.mac
            )
            self._available_update_state(False)
            return False
        self._available_update_state(True)

        _dt_of_circle: datetime = datetime.utcnow().replace(
            hour=clock_response.time.value.hour,
            minute=clock_response.time.value.minute,
            second=clock_response.time.value.second,
            microsecond=0,
            tzinfo=UTC,
        )
        clock_offset = (
            clock_response.timestamp.replace(microsecond=0) - _dt_of_circle
        )
        if (clock_offset.seconds < MAX_TIME_DRIFT) or (
            clock_offset.seconds > -(MAX_TIME_DRIFT)
        ):
            return True
        _LOGGER.info(
            "Reset realtime clock of node %s because time has drifted"
            + " %s seconds while max drift is set to %s seconds)",
            self._node_info.mac,
            str(clock_offset.seconds),
            str(MAX_TIME_DRIFT),
        )
        node_response: NodeResponse | None = await self._send(
            CirclePlusRealTimeClockSetRequest(
                self._mac_in_bytes,
                datetime.utcnow()
            ),
        )
        if node_response is None:
            return False
        if node_response.ack_id == NodeResponseType.CLOCK_ACCEPTED:
            return True
        return False
