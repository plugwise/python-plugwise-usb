"""Plugwise Sense node object."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
import logging
from typing import Any, Final

from ..api import NodeEvent, NodeFeature, NodeType, SenseStatistics
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
SENSE_HUMIDITY_LIMIT: Final = 65535
SENSE_TEMPERATURE_MULTIPLIER: Final = 175.72
SENSE_TEMPERATURE_OFFSET: Final = 46.85
SENSE_TEMPERATURE_LIMIT: Final = 65535

SENSE_FEATURES: Final = (
    NodeFeature.INFO,
    NodeFeature.SENSE,
)

# Default firmware if not known
DEFAULT_FIRMWARE: Final = datetime(2010, 12, 3, 10, 17, 7, tzinfo=UTC)


class PlugwiseSense(NodeSED):
    """Plugwise Sense node."""

    def __init__(
        self,
        mac: str,
        node_type: NodeType,
        controller: StickController,
        loaded_callback: Callable[[NodeEvent, str], Awaitable[None]],
    ):
        """Initialize Scan Device."""
        super().__init__(mac, node_type, controller, loaded_callback)

        self._sense_statistics = SenseStatistics()

        self._sense_subscription: Callable[[], None] | None = None

    async def load(self) -> None:
        """Load and activate Sense node features."""
        if self._loaded:
            return

        _LOGGER.debug("Loading Sense node %s", self._node_info.mac)
        await super().load()

        self._setup_protocol(SENSE_FIRMWARE_SUPPORT, SENSE_FEATURES)
        await self.initialize()
        await self._loaded_callback(NodeEvent.LOADED, self.mac)

    @raise_not_loaded
    async def initialize(self) -> None:
        """Initialize Sense node."""
        if self._initialized:
            return

        self._sense_subscription = await self._message_subscribe(
            self._sense_report,
            self._mac_in_bytes,
            (SENSE_REPORT_ID,),
        )
        await super().initialize()

    async def unload(self) -> None:
        """Unload node."""
        self._loaded = False
        if self._sense_subscription is not None:
            self._sense_subscription()
        await super().unload()

    # region Caching
    async def _load_defaults(self) -> None:
        """Load default configuration settings."""
        await super()._load_defaults()
        self._sense_statistics = SenseStatistics(
            temperature=0.0,
            humidity=0.0,
        )
        if self._node_info.model is None:
            self._node_info.model = "Sense"
            self._sed_node_info_update_task_scheduled = True
        if self._node_info.name is None:
            self._node_info.name = f"Sense {self._node_info.mac[-5:]}"
            self._sed_node_info_update_task_scheduled = True
        if self._node_info.firmware is None:
            self._node_info.firmware = DEFAULT_FIRMWARE
            self._sed_node_info_update_task_scheduled = True

    # endregion

    # region properties

    @property
    @raise_not_loaded
    def sense_statistics(self) -> SenseStatistics:
        """Sense Statistics."""
        return self._sense_statistics

    # end region

    async def _sense_report(self, response: PlugwiseResponse) -> bool:
        """Process sense report message to extract current temperature and humidity values."""
        if not isinstance(response, SenseReportResponse):
            raise MessageError(
                f"Invalid response message type ({response.__class__.__name__}) received, expected SenseReportResponse"
            )
        report_received = False
        await self._available_update_state(True, response.timestamp)
        if response.temperature.value != SENSE_TEMPERATURE_LIMIT:
            self._sense_statistics.temperature = float(
                SENSE_TEMPERATURE_MULTIPLIER * (response.temperature.value / 65536)
                - SENSE_TEMPERATURE_OFFSET
            )
            report_received = True

        if response.humidity.value != SENSE_HUMIDITY_LIMIT:
            self._sense_statistics.humidity = float(
                SENSE_HUMIDITY_MULTIPLIER * (response.humidity.value / 65536)
                - SENSE_HUMIDITY_OFFSET
            )
            report_received = True

        if report_received:
            await self.publish_feature_update_to_subscribers(
                NodeFeature.SENSE, self._sense_statistics
            )

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

            match feature:
                case NodeFeature.PING:
                    states[NodeFeature.PING] = await self.ping_update()
                case NodeFeature.SENSE:
                    states[NodeFeature.SENSE] = self._sense_statistics
                case _:
                    state_result = await super().get_state((feature,))
                    states[feature] = state_result[feature]

        if NodeFeature.AVAILABLE not in states:
            states[NodeFeature.AVAILABLE] = self.available_state

        return states
