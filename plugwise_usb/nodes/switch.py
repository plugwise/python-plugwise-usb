"""Plugwise switch node object."""

from __future__ import annotations

from asyncio import gather
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
import logging
from typing import Any, Final

from ..api import NodeEvent, NodeFeature, NodeType, SwitchGroup
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

# Switch Features
SWITCH_FEATURES: Final = (NodeFeature.SWITCH,)

# Default firmware if not known
DEFAULT_FIRMWARE: Final = datetime(2009, 9, 8, 14, 7, 4, tzinfo=UTC)


class PlugwiseSwitch(NodeSED):
    """Plugwise Switch node."""

    def __init__(
        self,
        mac: str,
        address: int,
        node_type: NodeType,
        controller: StickController,
        loaded_callback: Callable[[NodeEvent, str], Awaitable[None]],
    ):
        """Initialize Scan Device."""
        super().__init__(mac, address, node_type, controller, loaded_callback)
        self._switch_subscription: Callable[[], None] | None = None
        self._switch = SwitchGroup()

    async def load(self) -> bool:
        """Load and activate Switch node features."""
        if self._loaded:
            return True

        _LOGGER.debug("Loading Switch node %s", self._node_info.mac)
        if not await super().load():
            _LOGGER.warning("Load Switch base node failed")
            return False

        self._setup_protocol(SWITCH_FIRMWARE_SUPPORT, SWITCH_FEATURES)
        if await self.initialize():
            await self._loaded_callback(NodeEvent.LOADED, self.mac)
            return True

        _LOGGER.warning("Load Switch node %s failed", self._node_info.mac)
        return False

    @raise_not_loaded
    async def initialize(self) -> bool:
        """Initialize Switch node."""
        if self._initialized:
            return True

        self._switch_subscription = await self._message_subscribe(
            self._switch_response,
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
        return bool(self._switch.state)

    # endregion

    async def _switch_response(self, response: PlugwiseResponse) -> bool:
        """Switch group request from Switch."""
        if not isinstance(response, NodeSwitchGroupResponse):
            raise MessageError(
                f"Invalid response message type ({response.__class__.__name__}) received, expected NodeSwitchGroupResponse"
            )
        await gather(
            self._available_update_state(True, response.timestamp),
            self._switch_state_update(
                response.switch_state, response.switch_group, response.timestamp
            ),
        )
        return True

    async def _switch_state_update(
        self, switch_state: bool, switch_group: int, timestamp: datetime
    ) -> None:
        """Process switch state update."""
        _LOGGER.debug(
            "_switch_state_update for %s: %s",
            self.name,
            switch_state,
        )
        self._switch = replace(
            self._switch, state=switch_state, group=switch_group, timestamp=timestamp
        )

        await self.publish_feature_update_to_subscribers(
            NodeFeature.SWITCH, self._switch
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
                    states[NodeFeature.SWITCH] = self._switch
                case _:
                    state_result = await super().get_state((feature,))
                    states[feature] = state_result[feature]

        if NodeFeature.AVAILABLE not in states:
            states[NodeFeature.AVAILABLE] = self.available_state

        return states
