"""Plugwise Circle+ node object."""

from __future__ import annotations

from datetime import datetime, UTC
import logging

from .helpers import raise_not_loaded
from .helpers.firmware import CIRCLE_PLUS_FIRMWARE_SUPPORT
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
from .circle import PlugwiseCircle

_LOGGER = logging.getLogger(__name__)


class PlugwiseCirclePlus(PlugwiseCircle):
    """provides interface to the Plugwise Circle+ nodes"""

    async def load(self) -> bool:
        """Load and activate Circle+ node features."""
        if self._loaded:
            return True
        if self._cache_enabled:
            _LOGGER.debug(
                "Load Circle node %s from cache", self._node_info.mac
            )
            if await self._load_from_cache():
                self._loaded = True
                self._setup_protocol(
                    CIRCLE_PLUS_FIRMWARE_SUPPORT,
                    (
                        NodeFeature.RELAY_INIT,
                        NodeFeature.ENERGY,
                        NodeFeature.POWER,
                    ),
                )
                return await self.initialize()
            _LOGGER.warning(
                "Load Circle+ node %s from cache failed",
                self._node_info.mac,
            )
        else:
            _LOGGER.debug("Load Circle+ node %s", self._node_info.mac)

        # Check if node is online
        if not self._available and not await self.is_online():
            _LOGGER.warning(
                "Failed to load Circle+ node %s because it is not online",
                self._node_info.mac
            )
            return False

        # Get node info
        if not await self.node_info_update():
            _LOGGER.warning(
                "Failed to load Circle+ node %s because it is not responding"
                + " to information request",
                self._node_info.mac
            )
            return False
        self._loaded = True
        self._setup_protocol(
            CIRCLE_PLUS_FIRMWARE_SUPPORT,
            (
                NodeFeature.RELAY_INIT,
                NodeFeature.ENERGY,
                NodeFeature.POWER,
            ),
        )
        return await self.initialize()

    @raise_not_loaded
    async def initialize(self) -> bool:
        """Initialize node."""
        if self._initialized:
            return True
        self._initialized = True
        if not self._available:
            self._initialized = False
            return False
        if not self._calibration and not await self.calibration_update():
            self._initialized = False
            return False
        if not await self.realtime_clock_synchronize():
            self._initialized = False
            return False
        if (
            NodeFeature.RELAY_INIT in self._features and
            self._relay_init_state is None
        ):
            if (state := await self._relay_init_get()) is not None:
                self._relay_init_state = state
            else:
                _LOGGER.debug(
                    "Failed to initialized node %s, relay init",
                    self.mac
                )
                return False
        self._initialized = True
        return True

    async def realtime_clock_synchronize(self) -> bool:
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
            await self._available_update_state(False)
            return False
        await self._available_update_state(True)

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
