"""Plugwise switch node object."""

from __future__ import annotations

from collections.abc import Callable
import logging

from ..api import NodeEvent, NodeFeature
from ..exceptions import MessageError
from ..messages.responses import (
    NODE_SWITCH_GROUP_ID,
    NodeSwitchGroupResponse,
    PlugwiseResponse,
)
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
            _LOGGER.debug("Load Switch node %s from cache", self._node_info.mac)
            if await self._load_from_cache():
                self._loaded = True
                self._setup_protocol(
                    SWITCH_FIRMWARE_SUPPORT,
                    (NodeFeature.INFO, NodeFeature.SWITCH),
                )
                if await self.initialize():
                    await self._loaded_callback(NodeEvent.LOADED, self.mac)
                    return True
        _LOGGER.debug("Load of Switch node %s failed", self._node_info.mac)
        return False

    @raise_not_loaded
    async def initialize(self) -> bool:
        """Initialize Switch node."""
        if self._initialized:
            return True
        self._switch_subscription = self._message_subscribe(
            self._switch_group,
            self._mac_in_bytes,
            (NODE_SWITCH_GROUP_ID,),
        )
        return await super().initialize()

    async def unload(self) -> None:
        """Unload node."""
        self._loaded = False
        if self._switch_subscription is not None:
            self._switch_subscription()
        await super().unload()

    async def _switch_group(self, response: PlugwiseResponse) -> bool:
        """Switch group request from Switch."""
        if not isinstance(response, NodeSwitchGroupResponse):
            raise MessageError(
                f"Invalid response message type ({response.__class__.__name__}) received, expected NodeSwitchGroupResponse"
            )
        # Switch on
        if response.switch_state:
            if self._switch_state is None or not self._switch:
                self._switch_state = True
                await self.publish_feature_update_to_subscribers(
                    NodeFeature.SWITCH, True
                )
            return True
        # Switch off
        if self._switch is None or self._switch:
            self._switch = False
            await self.publish_feature_update_to_subscribers(
                NodeFeature.SWITCH, False
            )
        return True
