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
        request = CirclePlusRealTimeClockGetRequest(self._send, self._mac_in_bytes)
        if (response := await request.send()) is None:
            _LOGGER.warning(
                "No response for clock_synchronize() for %s", self._mac_in_str
            )
            await self._available_update_state(False)
            return False
        await self._available_update_state(True, response.timestamp)

        dt_now = datetime.now(tz=UTC)
        dt_now_date = dt_now.replace(hour=0, minute=0, second=0, microsecond=0)
        response_date = datetime(
            response.date.value.year,
            response.date.value.month,
            response.date.value.day,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
            tzinfo=UTC,
        )
        if dt_now_date != response_date:
            _LOGGER.info(
                "Sync clock of node %s because time has drifted %s days",
                self._mac_in_str,
                int(abs((dt_now_date - response_date).days)),
            )
            return await self._send_clock_set_req()

        circle_plus_timestamp: datetime = dt_now.replace(
            hour=response.time.value.hour,
            minute=response.time.value.minute,
            second=response.time.value.second,
            microsecond=0,
            tzinfo=UTC,
        )
        clock_offset = response.timestamp.replace(microsecond=0) - circle_plus_timestamp
        if abs(clock_offset.total_seconds()) < MAX_TIME_DRIFT:
            return True

        _LOGGER.info(
            "Sync clock of node %s because time drifted %s seconds",
            self._mac_in_str,
            int(abs(clock_offset.total_seconds())),
        )
        return await self._send_clock_set_req()

    async def _send_clock_set_req(self) -> bool:
        """Send CirclePlusRealTimeClockSetRequest."""
        set_request = CirclePlusRealTimeClockSetRequest(
            self._send, self._mac_in_bytes, datetime.now(tz=UTC)
        )
        if (node_response := await set_request.send()) is not None:
            return node_response.ack_id == NodeResponseType.CLOCK_ACCEPTED
        _LOGGER.warning("Failed to sync the clock of %s", self.name)
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
