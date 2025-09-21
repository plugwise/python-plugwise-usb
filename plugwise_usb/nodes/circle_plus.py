"""Plugwise Circle+ node."""

from __future__ import annotations

from datetime import UTC, datetime
import logging

from ..api import NodeEvent, NodeFeature
from ..constants import MAX_TIME_DRIFT
from ..messages.requests import (
    CirclePlusAllowJoiningRequest,
    CirclePlusRealTimeClockGetRequest,
    CirclePlusRealTimeClockSetRequest,
)
from ..messages.responses import NodeResponseType
from .circle import PlugwiseCircle
from .helpers import raise_not_loaded
from .helpers.firmware import CIRCLE_PLUS_FIRMWARE_SUPPORT

_LOGGER = logging.getLogger(__name__)

FEATURES_CIRCLE_PLUS = (
    NodeFeature.RELAY,
    NodeFeature.RELAY_INIT,
    NodeFeature.RELAY_LOCK,
    NodeFeature.ENERGY,
    NodeFeature.POWER,
    NodeFeature.CIRCLE,
    NodeFeature.CIRCLEPLUS,
)


class PlugwiseCirclePlus(PlugwiseCircle):
    """Plugwise Circle+ node."""

    async def load(self) -> bool:
        """Load and activate Circle+ node features."""
        if self._loaded:
            return True
        if self._cache_enabled:
            _LOGGER.debug("Loading Circle+ node %s from cache", self._mac_in_str)
            if await self._load_from_cache():
                self._loaded = True
        if not self._loaded:
            _LOGGER.debug("Retrieving info for Circle+ node %s", self._mac_in_str)

            # Check if node is online
            if (
                not self._available
                and not await self.is_online()
                or await self.node_info_update() is None
            ):
                _LOGGER.warning(
                    "Failed to load Circle+ node %s because it is not online or not responding",
                    self._mac_in_str,
                )
                return False

        self._loaded = True

        self._setup_protocol(CIRCLE_PLUS_FIRMWARE_SUPPORT, FEATURES_CIRCLE_PLUS)
        if not await self.initialize():
            return False

        await self._loaded_callback(NodeEvent.LOADED, self.mac)
        return True

    async def clock_synchronize(self) -> bool:
        """Synchronize realtime clock. Returns true if successful."""
        request = CirclePlusRealTimeClockGetRequest(
            self._send, self._mac_in_bytes
        )
        if (response := await request.send()) is None:
            _LOGGER.debug(
                "No response for async_realtime_clock_synchronize() for %s", self.mac
            )
            await self._available_update_state(False)
            return False
        await self._available_update_state(True, response.timestamp)

        dt_now = datetime.now(tz=UTC)
        _LOGGER.debug("HOI dt_now weekday=%s", dt_now.weekday())
        _LOGGER.debug("HOI circle+ day_of_week=%s", response.day_of_week.value)
        days_diff = (response.day_of_week.value - dt_now.weekday()) % 7
        circle_plus_timestamp: datetime = dt_now.replace(
            day=(dt_now.day + days_diff), 
            hour=response.time.value.hour,
            minute=response.time.value.minute,
            second=response.time.value.second,
            microsecond=0,
            tzinfo=UTC,
        )
        _LOGGER.debug("HOI circle+ clock=%s", circle_plus_timestamp)
        _LOGGER.debug("HOI response timestamp=%s", response.timestamp)
        clock_offset = (
            response.timestamp.replace(microsecond=0) - circle_plus_timestamp
        )
        if abs(clock_offset.total_seconds()) < MAX_TIME_DRIFT:
            return True
        _LOGGER.info(
            "Reset realtime clock of node %s because time has drifted %s seconds while max drift is set to %s seconds)",
            self._node_info.mac,
            str(int(abs(clock_offset.total_seconds()))),
            str(MAX_TIME_DRIFT),
        )
        set_request = CirclePlusRealTimeClockSetRequest(
            self._send, self._mac_in_bytes, datetime.now(tz=UTC)
        )
        if (node_response := await set_request.send()) is not None:
            return node_response.ack_id == NodeResponseType.CLOCK_ACCEPTED
        _LOGGER.warning(
            "Failed to (re)set the internal realtime clock of %s",
            self.name,
        )
        return False

    @raise_not_loaded
    async def enable_auto_join(self) -> bool:
        """Enable auto-join on the Circle+.

        Returns:
           bool: True if the request was acknowledged, False otherwise.

        """
        _LOGGER.info("Enabling auto-join for CirclePlus")
        request = CirclePlusAllowJoiningRequest(self._send, True)
        if (response := await request.send()) is None:
            return False

        # JOIN_ACCEPTED is the ACK for enable=True
        return NodeResponseType(response.ack_id) == NodeResponseType.JOIN_ACCEPTED
