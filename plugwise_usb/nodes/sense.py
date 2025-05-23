"""Plugwise Sense node object."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import logging
from typing import Any, Final

from ..api import NodeEvent, NodeFeature
from ..connection import StickController
from ..exceptions import MessageError, NodeError
from ..messages.responses import SENSE_REPORT_ID, PlugwiseResponse, SenseReportResponse
from ..nodes.sed import NodeSED
from .helpers import raise_not_loaded
from .helpers.firmware import SENSE_FIRMWARE_SUPPORT

_LOGGER = logging.getLogger(__name__)


# Sense calculations
SENSE_HUMIDITY_MULTIPLIER: Final = 125
SENSE_HUMIDITY_OFFSET: Final = 6
SENSE_TEMPERATURE_MULTIPLIER: Final = 175.72
SENSE_TEMPERATURE_OFFSET: Final = 46.85

SENSE_FEATURES: Final = (
    NodeFeature.INFO,
    NodeFeature.TEMPERATURE,
    NodeFeature.HUMIDITY,
)


class PlugwiseSense(NodeSED):
    """Plugwise Sense node."""

    def __init__(
        self,
        mac: str,
        address: int,
        controller: StickController,
        loaded_callback: Callable[[NodeEvent, str], Awaitable[None]],
    ):
        """Initialize Scan Device."""
        super().__init__(mac, address, controller, loaded_callback)

        self._humidity: float | None = None
        self._temperature: float | None = None

        self._sense_subscription: Callable[[], None] | None = None

    async def load(self) -> bool:
        """Load and activate Sense node features."""
        if self._loaded:
            return True

        self._node_info.is_battery_powered = True
        if self._cache_enabled:
            _LOGGER.debug("Loading Sense node %s from cache", self._node_info.mac)
            if await self._load_from_cache():
                self._loaded = True
                self._setup_protocol(
                    SENSE_FIRMWARE_SUPPORT,
                    (NodeFeature.INFO, NodeFeature.TEMPERATURE, NodeFeature.HUMIDITY),
                )
                if await self.initialize():
                    await self._loaded_callback(NodeEvent.LOADED, self.mac)
                    return True

        _LOGGER.debug("Loading of Sense node %s failed", self._node_info.mac)
        return False

    @raise_not_loaded
    async def initialize(self) -> bool:
        """Initialize Sense node."""
        if self._initialized:
            return True

        self._sense_subscription = await self._message_subscribe(
            self._sense_report,
            self._mac_in_bytes,
            (SENSE_REPORT_ID,),
        )
        await super().initialize()
        return True

    async def unload(self) -> None:
        """Unload node."""
        self._loaded = False
        if self._sense_subscription is not None:
            self._sense_subscription()
        await super().unload()

    async def _sense_report(self, response: PlugwiseResponse) -> bool:
        """Process sense report message to extract current temperature and humidity values."""
        if not isinstance(response, SenseReportResponse):
            raise MessageError(
                f"Invalid response message type ({response.__class__.__name__}) received, expected SenseReportResponse"
            )
        report_received = False
        await self._available_update_state(True, response.timestamp)
        if response.temperature.value != 65535:
            self._temperature = int(
                SENSE_TEMPERATURE_MULTIPLIER * (response.temperature.value / 65536)
                - SENSE_TEMPERATURE_OFFSET
            )
            await self.publish_feature_update_to_subscribers(
                NodeFeature.TEMPERATURE, self._temperature
            )
            report_received = True

        if response.humidity.value != 65535:
            self._humidity = int(
                SENSE_HUMIDITY_MULTIPLIER * (response.humidity.value / 65536)
                - SENSE_HUMIDITY_OFFSET
            )
            await self.publish_feature_update_to_subscribers(
                NodeFeature.HUMIDITY, self._humidity
            )
            report_received = True
    
        return report_received

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
                    f"Update of feature '{feature.name}' is not supported for {self.mac}"
                )
            if feature == NodeFeature.TEMPERATURE:
                states[NodeFeature.TEMPERATURE] = self._temperature
            elif feature == NodeFeature.HUMIDITY:
                states[NodeFeature.HUMIDITY] = self._humidity
            elif feature == NodeFeature.PING:
                states[NodeFeature.PING] = await self.ping_update()
            else:
                state_result = await super().get_state((feature,))
                states[feature] = state_result[feature]
        if NodeFeature.AVAILABLE not in states:
            states[NodeFeature.AVAILABLE] = self.available_state
        return states
