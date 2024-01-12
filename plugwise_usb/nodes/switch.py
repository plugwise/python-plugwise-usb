"""Plugwise switch node object."""

from __future__ import annotations
from collections.abc import Callable

from datetime import datetime, UTC
import logging
from typing import Final

from .helpers import raise_not_loaded
from ..api import NodeFeature
from ..exceptions import MessageError
from ..messages.responses import NODE_SWITCH_GROUP_ID, NodeSwitchGroupResponse
from ..nodes.sed import NodeSED

_LOGGER = logging.getLogger(__name__)

# Minimum and maximum supported (custom) zigbee protocol version based
# on utc timestamp of firmware
# Extracted from "Plugwise.IO.dll" file of Plugwise source installation
SWITCH_FIRMWARE: Final = {
    datetime(2009, 9, 8, 14, 7, 4, tzinfo=UTC): ("2.0", "2.4"),
    datetime(2010, 1, 16, 14, 7, 13, tzinfo=UTC): ("2.0", "2.4"),
    datetime(2010, 4, 27, 11, 59, 31, tzinfo=UTC): ("2.0", "2.4"),
    datetime(2010, 8, 4, 14, 15, 25, tzinfo=UTC): ("2.0", "2.4"),
    datetime(2010, 8, 17, 7, 44, 24, tzinfo=UTC): ("2.0", "2.4"),
    datetime(2010, 8, 31, 10, 23, 32, tzinfo=UTC): ("2.0", "2.4"),
    datetime(2010, 10, 7, 14, 29, 55, tzinfo=UTC): ("2.0", "2.4"),
    datetime(2010, 11, 1, 13, 41, 30, tzinfo=UTC): ("2.0", "2.4"),
    datetime(2011, 3, 25, 17, 46, 41, tzinfo=UTC): ("2.0", "2.5"),
    datetime(2011, 5, 13, 7, 26, 54, tzinfo=UTC): ("2.0", "2.5"),
    datetime(2011, 6, 27, 9, 4, 10, tzinfo=UTC): ("2.0", "2.5"),

    # Legrand
    datetime(2011, 11, 3, 13, 10, 18, tzinfo=UTC): ("2.0", "2.6"),

    # Radio Test
    datetime(2012, 4, 19, 14, 10, 48, tzinfo=UTC): (
        "2.0",
        "2.5",
    ),

    # New Flash Update
    datetime(2017, 7, 11, 16, 11, 10, tzinfo=UTC): (
        "2.0",
        "2.6",
    ),
}
SWITCH_FEATURES: Final = (NodeFeature.INFO, NodeFeature.SWITCH)


class PlugwiseSwitch(NodeSED):
    """provides interface to the Plugwise Switch nodes"""

    _switch_subscription: Callable[[], None] | None = None
    _switch_state: bool | None = None

    async def load(self) -> bool:
        """Load and activate Switch node features."""
        if self._loaded:
            return True
        self._node_info.battery_powered = True
        if self._cache_enabled:
            _LOGGER.debug(
                "Load Switch node %s from cache", self._node_info.mac
            )
            if await self._load_from_cache():
                self._loaded = True
                self._load_features()
                return True

        _LOGGER.debug("Load of Switch node %s failed", self._node_info.mac)
        return False

    @raise_not_loaded
    async def initialize(self) -> bool:
        """Initialize Switch node."""
        if self._initialized:
            return True
        if not await super().initialize():
            return False
        self._switch_subscription = self._message_subscribe(
            b"0056",
            self._switch_group,
            self._mac_in_bytes,
            NODE_SWITCH_GROUP_ID,
        )
        self._initialized = True
        return True

    def _load_features(self) -> None:
        """Enable additional supported feature(s)"""
        self._setup_protocol(SWITCH_FIRMWARE)
        self._features += SWITCH_FEATURES
        self._node_info.features = self._features

    async def unload(self) -> None:
        """Unload node."""
        if self._switch_subscription is not None:
            self._switch_subscription()
        await super().unload()

    async def _switch_group(self, message: NodeSwitchGroupResponse) -> None:
        """Switch group request from Switch."""
        if message.power_state.value == 0:
            if self._switch is None or self._switch:
                self._switch = False
                await self.publish_event(NodeFeature.SWITCH, False)
        elif message.power_state.value == 1:
            if self._switch_state is None or not self._switch:
                self._switch_state = True
                await self.publish_event(NodeFeature.SWITCH, True)
        else:
            raise MessageError(
                f"Unknown power_state '{message.power_state.value}' " +
                f"received from {self.mac}"
            )
