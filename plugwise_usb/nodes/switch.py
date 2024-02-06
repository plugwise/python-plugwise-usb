"""Plugwise switch node object."""

from __future__ import annotations

from collections.abc import Callable
import logging

from ..api import NodeEvent, NodeFeature
from ..exceptions import MessageError
from ..messages.responses import NODE_SWITCH_GROUP_ID, NodeSwitchGroupResponse
from ..nodes.sed import NodeSED
from .helpers import raise_not_loaded
from .helpers.firmware import SWITCH_FIRMWARE_SUPPORT

_LOGGER = logging.getLogger(__name__)


class PlugwiseSwitch(NodeSED):
    """Plugwise Switch node."""

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
                self._setup_protocol(
                    SWITCH_FIRMWARE_SUPPORT,
                    (NodeFeature.INFO, NodeFeature.SWITCH),
                )
                return await self.initialize()

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
        await self._loaded_callback(NodeEvent.LOADED, self.mac)
        return True

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
                await self.publish_feature_update_to_subscribers(
                    NodeFeature.SWITCH, False
                )
        elif message.power_state.value == 1:
            if self._switch_state is None or not self._switch:
                self._switch_state = True
                await self.publish_feature_update_to_subscribers(
                    NodeFeature.SWITCH, True
                )
        else:
            raise MessageError(
                f"Unknown power_state '{message.power_state.value}' " +
                f"received from {self.mac}"
            )
