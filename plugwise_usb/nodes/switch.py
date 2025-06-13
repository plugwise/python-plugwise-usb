"""Plugwise switch node object."""

from __future__ import annotations

from asyncio import gather
from collections.abc import Awaitable, Callable
from datetime import datetime
import logging
from typing import Any, Final

from ..api import NodeEvent, NodeFeature
from ..connection import StickController
from ..exceptions import MessageError, NodeError
from ..messages.responses import (
    NODE_SWITCH_GROUP_ID,
    NodeSwitchGroupResponse,
    PlugwiseResponse,
)
from ..nodes.sed import NodeSED
from .helpers import raise_not_loaded
from .helpers.firmware import SWITCH_FIRMWARE_SUPPORT

_LOGGER = logging.getLogger(__name__)

CACHE_SWITCH_STATE: Final = "switch_state"
CACHE_SWITCH_TIMESTAMP: Final = "switch_timestamp"


class PlugwiseSwitch(NodeSED):
    """Plugwise Switch node."""

    def __init__(
        self,
        mac: str,
        address: int,
        controller: StickController,
        loaded_callback: Callable[[NodeEvent, str], Awaitable[None]],
    ):
        """Initialize Scan Device."""
        super().__init__(mac, address, controller, loaded_callback)
        self._switch_subscription: Callable[[], None] | None = None
        self._switch_state: bool | None = None

    async def load(self) -> bool:
        """Load and activate Switch node features."""
        if self._loaded:
            return True
        if self._cache_enabled:
            _LOGGER.debug("Load Switch node %s from cache", self._node_info.mac)
            await self._load_from_cache()
        else:
            self._load_defaults()
        self._loaded = True
        self._setup_protocol(
            SWITCH_FIRMWARE_SUPPORT,
            (
                NodeFeature.BATTERY,
                NodeFeature.INFO,
                NodeFeature.PING,
                NodeFeature.SWITCH,
            ),
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

        self._switch_subscription = await self._message_subscribe(
            self._switch_group,
            self._mac_in_bytes,
            (NODE_SWITCH_GROUP_ID,),
        )
        await super().initialize()
        return True

    async def unload(self) -> None:
        """Unload node."""
        if self._switch_subscription is not None:
            self._switch_subscription()
        await super().unload()

    # region Properties

    @property
    @raise_not_loaded
    def switch(self) -> bool:
        """Current state of switch."""
        return bool(self._switch_state)

    # endregion

    async def _switch_group(self, response: PlugwiseResponse) -> bool:
        """Switch group request from Switch."""
        if not isinstance(response, NodeSwitchGroupResponse):
            raise MessageError(
                f"Invalid response message type ({response.__class__.__name__}) received, expected NodeSwitchGroupResponse"
            )
        await gather(
            self._available_update_state(True, response.timestamp),
            self._switch_state_update(response.switch_state, response.timestamp),
        )
        return True

    async def _switch_state_update(
        self, switch_state: bool, timestamp: datetime
    ) -> None:
        """Process switch state update."""
        _LOGGER.debug(
            "_switch_state_update for %s: %s -> %s",
            self.name,
            self._switch_state,
            switch_state,
        )
        state_update = False
        # Update cache
        self._set_cache(CACHE_SWITCH_STATE, str(switch_state))
        # Check for a state change
        if self._switch_state != switch_state:
            self._switch_state = switch_state
            state_update = True

        self._set_cache(CACHE_SWITCH_TIMESTAMP, timestamp)
        if state_update:
            await gather(
                *[
                    self.publish_feature_update_to_subscribers(
                        NodeFeature.SWITCH, self._switch_state
                    ),
                    self.save_cache(),
                ]
            )

    @raise_not_loaded
    async def get_state(self, features: tuple[NodeFeature]) -> dict[NodeFeature, Any]:
        """Update latest state for given feature."""
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

            match feature:
                case NodeFeature.SWITCH:
                    states[NodeFeature.SWITCH] = self._switch_state
                case _:
                    state_result = await super().get_state((feature,))
                    states[feature] = state_result[feature]

        if NodeFeature.AVAILABLE not in states:
            states[NodeFeature.AVAILABLE] = self.available_state

        return states
