"""Plugwise Sense node object."""

from __future__ import annotations

from asyncio import gather
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
import logging
from typing import Any, Final

from ..api import (
    NodeEvent,
    NodeFeature,
    NodeType,
    SenseHysteresisConfig,
    SenseStatistics,
)
from ..connection import StickController
from ..exceptions import MessageError, NodeError
from ..messages.requests import SenseConfigureHysteresisRequest
from ..messages.responses import (
    NODE_SWITCH_GROUP_ID,
    SENSE_REPORT_ID,
    NodeAckResponseType,
    NodeSwitchGroupResponse,
    PlugwiseResponse,
    SenseReportResponse,
)
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
    NodeFeature.SENSE_HYSTERESIS,
    NodeFeature.SWITCH,
)

# Default firmware if not known
DEFAULT_FIRMWARE: Final = datetime(2010, 12, 3, 10, 17, 7, tzinfo=UTC)

CACHE_SENSE_HYSTERESIS_HUMIDITY_ENABLED = "humidity_enabled"
CACHE_SENSE_HYSTERESIS_HUMIDITY_UPPER_BOUND = "humidity_upper_bound"
CACHE_SENSE_HYSTERESIS_HUMIDITY_LOWER_BOUND = "humidity_lower_bound"
CACHE_SENSE_HYSTERESIS_HUMIDITY_DIRECTION = "humidity_direction"
CACHE_SENSE_HYSTERESIS_TEMPERATURE_ENABLED = "temperature_enabled"
CACHE_SENSE_HYSTERESIS_TEMPERATURE_UPPER_BOUND = "temperature_upper_bound"
CACHE_SENSE_HYSTERESIS_TEMPERATURE_LOWER_BOUND = "temperature_lower_bound"
CACHE_SENSE_HYSTERESIS_TEMPERATURE_DIRECTION = "temperature_direction"
CACHE_SENSE_HYSTERESIS_DIRTY = "sense_hysteresis_dirty"

DEFAULT_SENSE_HYSTERESIS_HUMIDITY_ENABLED: Final = False
DEFAULT_SENSE_HYSTERESIS_HUMIDITY_UPPER_BOUND: Final = 24.0
DEFAULT_SENSE_HYSTERESIS_HUMIDITY_LOWER_BOUND: Final = 24.0
DEFAULT_SENSE_HYSTERESIS_HUMIDITY_DIRECTION: Final = True
DEFAULT_SENSE_HYSTERESIS_TEMPERATURE_ENABLED: Final = False
DEFAULT_SENSE_HYSTERESIS_TEMPERATURE_UPPER_BOUND: Final = 50.0
DEFAULT_SENSE_HYSTERESIS_TEMPERATURE_LOWER_BOUND: Final = 50.0
DEFAULT_SENSE_HYSTERESIS_TEMPERATURE_DIRECTION: Final = True


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
        self._hysteresis_config = SenseHysteresisConfig()
        self._sense_subscription: Callable[[], None] | None = None
        self._unsubscribe_switch_group: Callable[[], None] | None = None

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
        self._unsubscribe_switch_group = await self._message_subscribe(
            self._switch_group,
            self._mac_in_bytes,
            (NODE_SWITCH_GROUP_ID,),
        )
        await super().initialize()

    async def unload(self) -> None:
        """Unload node."""
        self._loaded = False
        if self._sense_subscription is not None:
            self._sense_subscription()
        if self._unsubscribe_switch_group is not None:
            self._unsubscribe_switch_group()
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

    async def _load_from_cache(self) -> bool:
        """Load states from previous cached information. Returns True if successful."""
        super_load_success = True
        if not await super()._load_from_cache():
            super_load_success = False
        dirty = False
        if (humidity_enabled := self._humidity_enabled_from_cache()) is None:
            dirty = True
            humidity_enabled = DEFAULT_SENSE_HYSTERESIS_HUMIDITY_ENABLED
        if (humidity_upper_bound := self._humidity_upper_bound_from_cache()) is None:
            dirty = True
            humidity_upper_bound = DEFAULT_SENSE_HYSTERESIS_HUMIDITY_UPPER_BOUND
        if (humidity_lower_bound := self._humidity_lower_bound_from_cache()) is None:
            dirty = True
            humidity_lower_bound = DEFAULT_SENSE_HYSTERESIS_HUMIDITY_LOWER_BOUND
        if (humidity_direction := self._humidity_direction_from_cache()) is None:
            dirty = True
            humidity_direction = DEFAULT_SENSE_HYSTERESIS_HUMIDITY_DIRECTION
        if (temperature_enabled := self._temperature_enabled_from_cache()) is None:
            dirty = True
            temperature_enabled = DEFAULT_SENSE_HYSTERESIS_TEMPERATURE_ENABLED
        if (
            temperature_upper_bound := self._temperature_upper_bound_from_cache()
        ) is None:
            dirty = True
            temperature_upper_bound = DEFAULT_SENSE_HYSTERESIS_TEMPERATURE_UPPER_BOUND
        if (
            temperature_lower_bound := self._temperature_lower_bound_from_cache()
        ) is None:
            dirty = True
            temperature_lower_bound = DEFAULT_SENSE_HYSTERESIS_TEMPERATURE_LOWER_BOUND
        if (temperature_direction := self._temperature_direction_from_cache()) is None:
            dirty = True
            temperature_direction = DEFAULT_SENSE_HYSTERESIS_TEMPERATURE_DIRECTION
        dirty |= self._sense_hysteresis_dirty_from_cache()

        self._hysteresis_config = SenseHysteresisConfig(
            humidity_enabled=humidity_enabled,
            humidity_upper_bound=humidity_upper_bound,
            humidity_lower_bound=humidity_lower_bound,
            humidity_direction=humidity_direction,
            temperature_enabled=temperature_enabled,
            temperature_upper_bound=temperature_upper_bound,
            temperature_lower_bound=temperature_lower_bound,
            temperature_direction=temperature_direction,
            dirty=dirty,
        )
        if dirty:
            await self._sense_configure_update()
        return super_load_success

    def _humidity_enabled_from_cache(self) -> bool | None:
        """Load humidity hysteresis enabled from cache."""
        return self._get_cache_as_bool(CACHE_SENSE_HYSTERESIS_HUMIDITY_ENABLED)

    def _humidity_upper_bound_from_cache(self) -> float | None:
        """Load humidity upper bound from cache."""
        if (
            humidity_upper_bound := self._get_cache(
                CACHE_SENSE_HYSTERESIS_HUMIDITY_UPPER_BOUND
            )
        ) is not None:
            return float(humidity_upper_bound)
        return None

    def _humidity_lower_bound_from_cache(self) -> float | None:
        """Load humidity lower bound from cache."""
        if (
            humidity_lower_bound := self._get_cache(
                CACHE_SENSE_HYSTERESIS_HUMIDITY_LOWER_BOUND
            )
        ) is not None:
            return float(humidity_lower_bound)
        return None

    def _humidity_direction_from_cache(self) -> bool | None:
        """Load humidity hysteresis switch direction from cache."""
        return self._get_cache_as_bool(CACHE_SENSE_HYSTERESIS_HUMIDITY_DIRECTION)

    def _temperature_enabled_from_cache(self) -> bool | None:
        """Load temperature hysteresis enabled from cache."""
        return self._get_cache_as_bool(CACHE_SENSE_HYSTERESIS_TEMPERATURE_ENABLED)

    def _temperature_upper_bound_from_cache(self) -> float | None:
        """Load temperature upper bound from cache."""
        if (
            temperature_upper_bound := self._get_cache(
                CACHE_SENSE_HYSTERESIS_TEMPERATURE_UPPER_BOUND
            )
        ) is not None:
            return float(temperature_upper_bound)
        return None

    def _temperature_lower_bound_from_cache(self) -> float | None:
        """Load temperature lower bound from cache."""
        if (
            temperature_lower_bound := self._get_cache(
                CACHE_SENSE_HYSTERESIS_TEMPERATURE_LOWER_BOUND
            )
        ) is not None:
            return float(temperature_lower_bound)
        return None

    def _temperature_direction_from_cache(self) -> bool | None:
        """Load Temperature hysteresis switch direction from cache."""
        return self._get_cache_as_bool(CACHE_SENSE_HYSTERESIS_TEMPERATURE_DIRECTION)

    def _sense_hysteresis_dirty_from_cache(self) -> bool:
        """Load sense hysteresis dirty from cache."""
        if (dirty := self._get_cache_as_bool(CACHE_SENSE_HYSTERESIS_DIRTY)) is not None:
            return dirty
        return True

    # endregion

    # region properties

    @property
    @raise_not_loaded
    def sense_statistics(self) -> SenseStatistics:
        """Sense Statistics."""
        return self._sense_statistics

    @property
    def hysteresis_config(self) -> SenseHysteresisConfig:
        """Sense Hysteresis Configuration."""
        return SenseHysteresisConfig(
            humidity_enabled=self.humidity_enabled,
            humidity_upper_bound=self.humidity_upper_bound,
            humidity_lower_bound=self.humidity_lower_bound,
            humidity_direction=self.humidity_direction,
            temperature_enabled=self.temperature_enabled,
            temperature_upper_bound=self.temperature_upper_bound,
            temperature_lower_bound=self.temperature_lower_bound,
            temperature_direction=self.temperature_direction,
            dirty=self.dirty,
        )

    @property
    def humidity_enabled(self) -> bool:
        """Humidity hysteresis enabled flag."""
        if self._hysteresis_config.humidity_enabled is not None:
            return self._hysteresis_config.humidity_enabled
        return DEFAULT_SENSE_HYSTERESIS_HUMIDITY_ENABLED

    @property
    def humidity_upper_bound(self) -> float:
        """Humidity upper bound value."""
        if self._hysteresis_config.humidity_upper_bound is not None:
            return self._hysteresis_config.humidity_upper_bound
        return DEFAULT_SENSE_HYSTERESIS_HUMIDITY_UPPER_BOUND

    @property
    def humidity_lower_bound(self) -> float:
        """Humidity lower bound value."""
        if self._hysteresis_config.humidity_lower_bound is not None:
            return self._hysteresis_config.humidity_lower_bound
        return DEFAULT_SENSE_HYSTERESIS_HUMIDITY_LOWER_BOUND

    @property
    def humidity_direction(self) -> bool:
        """Humidity hysteresis switch direction."""
        if self._hysteresis_config.humidity_direction is not None:
            return self._hysteresis_config.humidity_direction
        return DEFAULT_SENSE_HYSTERESIS_HUMIDITY_DIRECTION

    @property
    def temperature_enabled(self) -> bool:
        """Temperature hysteresis enabled flag."""
        if self._hysteresis_config.temperature_enabled is not None:
            return self._hysteresis_config.temperature_enabled
        return DEFAULT_SENSE_HYSTERESIS_TEMPERATURE_ENABLED

    @property
    def temperature_upper_bound(self) -> float:
        """Temperature upper bound value."""
        if self._hysteresis_config.temperature_upper_bound is not None:
            return self._hysteresis_config.temperature_upper_bound
        return DEFAULT_SENSE_HYSTERESIS_TEMPERATURE_UPPER_BOUND

    @property
    def temperature_lower_bound(self) -> float:
        """Temperature lower bound value."""
        if self._hysteresis_config.temperature_lower_bound is not None:
            return self._hysteresis_config.temperature_lower_bound
        return DEFAULT_SENSE_HYSTERESIS_TEMPERATURE_LOWER_BOUND

    @property
    def temperature_direction(self) -> bool:
        """Temperature hysteresis switch direction."""
        if self._hysteresis_config.temperature_direction is not None:
            return self._hysteresis_config.temperature_direction
        return DEFAULT_SENSE_HYSTERESIS_TEMPERATURE_DIRECTION

    # end region

    # region Configuration actions

    async def set_hysteresis_humidity_enabled(self, state: bool) -> bool:
        """Configure if humidity hysteresis should be enabled or not.

        Configuration request will be queued and will be applied the next time when node is awake for maintenance.
        """
        _LOGGER.debug(
            "set_hysteresis_humidity_enabled | Device %s | %s -> %s",
            self.name,
            self._hysteresis_config.humidity_enabled,
            state,
        )
        if self._hysteresis_config.humidity_enabled == state:
            return False
        self._hysteresis_config = replace(
            self._hysteresis_config,
            humidity_enabled=state,
            dirty=True,
        )
        await self._sense_configure_update()
        return True

    async def set_hysteresis_humidity_upper_bound(self, upper_bound: float) -> bool:
        """Configure humidity hysteresis upper bound.

        Configuration request will be queued and will be applied the next time when node is awake for maintenance.
        """
        _LOGGER.debug(
            "set_hysteresis_humidity_upper_bound | Device %s | %s -> %s",
            self.name,
            self._hysteresis_config.humidity_upper_bound,
            upper_bound,
        )
        if upper_bound < 1 or upper_bound > 128:
            raise ValueError(
                "Invalid humidity upper bound {upper_bound}. It must be between 1 and 128 percent."
            )
        if upper_bound < self._hysteresis_config.humidity_lower_bound:
            raise ValueError(
                "Invalid humidity upper bound {upper_bound}. It must be equal or above the lower bound {self._hysteresis_config.humidity_lower_bound}."
            )
        if self._hysteresis_config.humidity_upper_bound == upper_bound:
            return False
        self._hysteresis_config = replace(
            self._hysteresis_config,
            upper_bound=upper_bound,
            dirty=True,
        )
        await self._sense_configure_update()
        return True

    async def set_hysteresis_humidity_lower_bound(self, lower_bound: float) -> bool:
        """Configure humidity hysteresis lower bound.

        Configuration request will be queued and will be applied the next time when node is awake for maintenance.
        """
        _LOGGER.debug(
            "set_hysteresis_humidity_lower_bound | Device %s | %s -> %s",
            self.name,
            self._hysteresis_config.humidity_lower_bound,
            lower_bound,
        )
        if lower_bound < 1 or lower_bound > 128:
            raise ValueError(
                "Invalid humidity lower bound {lower_bound}. It must be between 1 and 128 percent."
            )
        if lower_bound > self._hysteresis_config.humidity_upper_bound:
            raise ValueError(
                "Invalid humidity lower bound {lower_bound}. It must be equal or above the lower bound {self._hysteresis_config.humidity_lower_bound}."
            )
        if self._hysteresis_config.humidity_lower_bound == lower_bound:
            return False
        self._hysteresis_config = replace(
            self._hysteresis_config,
            lower_bound=lower_bound,
            dirty=True,
        )
        await self._sense_configure_update()
        return True

    async def set_hysteresis_humidity_direction(self, state: bool) -> bool:
        """Configure humitidy hysteresis to switch on or off on increase or decreasing direction.

        Configuration request will be queued and will be applied the next time when node is awake for maintenance.
        """
        _LOGGER.debug(
            "set_hysteresis_humidity_direction | Device %s | %s -> %s",
            self.name,
            self._hysteresis_config.humidity_direction,
            state,
        )
        if self._hysteresis_config.humidity_direction == state:
            return False
        self._hysteresis_config = replace(
            self._hysteresis_config,
            humidity_direction=state,
            dirty=True,
        )
        await self._sense_configure_update()
        return True

    async def set_hysteresis_temperature_enabled(self, state: bool) -> bool:
        """Configure if temperature hysteresis should be enabled or not.

        Configuration request will be queued and will be applied the next time when node is awake for maintenance.
        """
        _LOGGER.debug(
            "set_hysteresis_temperature_enabled | Device %s | %s -> %s",
            self.name,
            self._hysteresis_config.temperature_enabled,
            state,
        )
        if self._hysteresis_config.temperature_enabled == state:
            return False
        self._hysteresis_config = replace(
            self._hysteresis_config,
            temperature_enabled=state,
            dirty=True,
        )
        await self._sense_configure_update()
        return True

    async def set_hysteresis_temperature_upper_bound(self, upper_bound: float) -> bool:
        """Configure temperature hysteresis upper bound.

        Configuration request will be queued and will be applied the next time when node is awake for maintenance.
        """
        _LOGGER.debug(
            "set_hysteresis_temperature_upper_bound | Device %s | %s -> %s",
            self.name,
            self._hysteresis_config.temperature_upper_bound,
            upper_bound,
        )
        if upper_bound < 1 or upper_bound > 128:
            raise ValueError(
                "Invalid temperature upper bound {upper_bound}. It must be between 1 and 128 percent."
            )
        if upper_bound < self._hysteresis_config.temperature_lower_bound:
            raise ValueError(
                "Invalid temperature upper bound {upper_bound}. It must be equal or above the lower bound {self._hysteresis_config.temperature_lower_bound}."
            )
        if self._hysteresis_config.temperature_upper_bound == upper_bound:
            return False
        self._hysteresis_config = replace(
            self._hysteresis_config,
            upper_bound=upper_bound,
            dirty=True,
        )
        await self._sense_configure_update()
        return True

    async def set_hysteresis_temperature_lower_bound(self, lower_bound: float) -> bool:
        """Configure temperature hysteresis lower bound.

        Configuration request will be queued and will be applied the next time when node is awake for maintenance.
        """
        _LOGGER.debug(
            "set_hysteresis_temperature_lower_bound | Device %s | %s -> %s",
            self.name,
            self._hysteresis_config.temperature_lower_bound,
            lower_bound,
        )
        if lower_bound < 1 or lower_bound > 128:
            raise ValueError(
                "Invalid temperature lower bound {lower_bound}. It must be between 1 and 128 percent."
            )
        if lower_bound > self._hysteresis_config.temperature_upper_bound:
            raise ValueError(
                "Invalid temperature lower bound {lower_bound}. It must be equal or below the upper bound {self._hysteresis_config.temperature_upper_bound}."
            )
        if self._hysteresis_config.temperature_lower_bound == lower_bound:
            return False
        self._hysteresis_config = replace(
            self._hysteresis_config,
            lower_bound=lower_bound,
            dirty=True,
        )
        await self._sense_configure_update()
        return True

    async def set_hysteresis_temperature_direction(self, state: bool) -> bool:
        """Configure humitidy hysteresis to switch on or off on increase or decreasing direction.

        Configuration request will be queued and will be applied the next time when node is awake for maintenance.
        """
        _LOGGER.debug(
            "set_hysteresis_temperature_direction | Device %s | %s -> %s",
            self.name,
            self._hysteresis_config.temperature_direction,
            state,
        )
        if self._hysteresis_config.temperature_direction == state:
            return False
        self._hysteresis_config = replace(
            self._hysteresis_config,
            temperature_direction=state,
            dirty=True,
        )
        await self._sense_configure_update()
        return True

    # end region

    async def _switch_group(self, response: PlugwiseResponse) -> bool:
        """Switch group request from Sense.

        turn on/off based on hysteresis and direction.
        """
        if not isinstance(response, NodeSwitchGroupResponse):
            raise MessageError(
                f"Invalid response message type ({response.__class__.__name__}) received, expected NodeSwitchGroupResponse"
            )
        _LOGGER.warning("%s received %s", self.name, response)
        await gather(
            self._available_update_state(True, response.timestamp),
            self._hysteresis_state_update(response.switch_state, response.timestamp),
        )
        return True

    async def _hysteresis_state_update(
        self, switch_state: bool, switch_group: int, timestamp: datetime
    ) -> None:
        """Process hysteresis state update."""
        _LOGGER.debug(
            "_hysteresis_state_update for %s: %s",
            self.name,
            switch_state,
        )
        if switch_group == 1:
            self._sense_statistics.temperature_state = switch_state
        if switch_group == 2:
            self._sense_statistics.humidity_state = switch_state

        await self.publish_feature_update_to_subscribers(
            NodeFeature.SENSE, self._sense_statistics
        )

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

    async def _run_awake_tasks(self) -> None:
        """Execute all awake tasks."""
        await super()._run_awake_tasks()
        if self._hysteresis_config.dirty:
            await self._configure_sense_humidity_task()
            await self._configure_sense_temperature_task()

    async def _configure_sense_humidity_task(self) -> bool:
        """Configure Sense humidity hysteresis device settings. Returns True if successful."""
        if not self._hysteresis_config.dirty:
            return True
        # Set value to -1 for disabled
        humidity_lower_bound = 2621
        humidity_upper_bound = 2621
        if self.humidity_enabled:
            if self.humidity_lower_bound > self.humidity_upper_bound:
                raise ValueError(
                    "Invalid humidity lower bound {self.humidity_lower_bound}. It must be equal or below the upper bound {self.humidity_upper_bound}."
                )
            humidity_lower_bound = int(
                (self.humidity_lower_bound + SENSE_HUMIDITY_OFFSET)
                * 65536
                / SENSE_HUMIDITY_MULTIPLIER
            )
            humidity_upper_bound = int(
                (self.humidity_upper_bound + SENSE_HUMIDITY_OFFSET)
                * 65536
                / SENSE_HUMIDITY_MULTIPLIER
            )
        request = SenseConfigureHysteresisRequest(
            self._send,
            self._mac_in_bytes,
            False,
            humidity_lower_bound,
            humidity_upper_bound,
            self.humidity_direction,
        )
        if (response := await request.send()) is None:
            _LOGGER.warning(
                "No response from %s to configure humidity hysteresis settings request",
                self.name,
            )
            return False
        if response.node_ack_type == NodeAckResponseType.SENSE_BOUNDARIES_FAILED:
            _LOGGER.warning(
                "Failed to configure humidity hysteresis settings for %s", self.name
            )
            return False
        if response.node_ack_type == NodeAckResponseType.SENSE_BOUNDARIES_ACCEPTED:
            _LOGGER.debug(
                "Successful configure humidity hysteresis settings for %s", self.name
            )
            self._hysteresis_config = replace(self._hysteresis_config, dirty=False)
            await self._sense_configure_update()
            return True

        _LOGGER.warning(
            "Unexpected response ack type %s for %s",
            response.node_ack_type,
            self.name,
        )
        return False

    async def _configure_sense_temperature_task(self) -> bool:
        """Configure Sense temperature hysteresis device settings. Returns True if successful."""
        if not self._hysteresis_config.dirty:
            return True
        # Set value to -1 for disabled
        temperature_lower_bound = 17099
        temperature_upper_bound = 17099
        if self.temperature_enabled:
            if self.temperature_lower_bound > self.temperature_upper_bound:
                raise ValueError(
                    "Invalid temperature lower bound {self.temperature_lower_bound}. It must be equal or below the upper bound {self.temperature_upper_bound}."
                )
            temperature_lower_bound = int(
                (self.temperature_lower_bound + SENSE_TEMPERATURE_OFFSET)
                * 65536
                / SENSE_TEMPERATURE_MULTIPLIER
            )
            temperature_upper_bound = int(
                (self.temperature_upper_bound + SENSE_TEMPERATURE_OFFSET)
                * 65536
                / SENSE_TEMPERATURE_MULTIPLIER
            )
        request = SenseConfigureHysteresisRequest(
            self._send,
            self._mac_in_bytes,
            False,
            temperature_lower_bound,
            temperature_upper_bound,
            self.temperature_direction,
        )
        if (response := await request.send()) is None:
            _LOGGER.warning(
                "No response from %s to configure temperature hysteresis settings request",
                self.name,
            )
            return False
        if response.node_ack_type == NodeAckResponseType.SENSE_BOUNDARIES_FAILED:
            _LOGGER.warning(
                "Failed to configure temperature hysteresis settings for %s", self.name
            )
            return False
        if response.node_ack_type == NodeAckResponseType.SENSE_BOUNDARIES_ACCEPTED:
            _LOGGER.debug(
                "Successful configure temperature hysteresis settings for %s", self.name
            )
            self._hysteresis_config = replace(self._hysteresis_config, dirty=False)
            await self._sense_configure_update()
            return True

        _LOGGER.warning(
            "Unexpected response ack type %s for %s",
            response.node_ack_type,
            self.name,
        )
        return False

    async def _sense_configure_update(self) -> None:
        """Push sense configuration update to cache."""
        self._set_cache(CACHE_SENSE_HYSTERESIS_HUMIDITY_ENABLED, self.humidity_enabled)
        self._set_cache(
            CACHE_SENSE_HYSTERESIS_HUMIDITY_UPPER_BOUND, self.humidity_upper_bound
        )
        self._set_cache(
            CACHE_SENSE_HYSTERESIS_HUMIDITY_LOWER_BOUND, self.humidity_lower_bound
        )
        self._set_cache(
            CACHE_SENSE_HYSTERESIS_HUMIDITY_DIRECTION, self.humidity_direction
        )
        self._set_cache(
            CACHE_SENSE_HYSTERESIS_TEMPERATURE_ENABLED, self.temperature_enabled
        )
        self._set_cache(
            CACHE_SENSE_HYSTERESIS_TEMPERATURE_UPPER_BOUND, self.temperature_upper_bound
        )
        self._set_cache(
            CACHE_SENSE_HYSTERESIS_TEMPERATURE_LOWER_BOUND, self.temperature_lower_bound
        )
        self._set_cache(
            CACHE_SENSE_HYSTERESIS_TEMPERATURE_DIRECTION, self.temperature_direction
        )
        self._set_cache(CACHE_SENSE_HYSTERESIS_DIRTY, self.dirty)
        await gather(
            self.publish_feature_update_to_subscribers(
                NodeFeature.SENSE_HYSTERESIS,
                self._hysteresis_config,
            ),
            self.save_cache(),
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
                    f"Update of feature '{feature.name}' is not supported for {self.mac}"
                )

            match feature:
                case NodeFeature.PING:
                    states[NodeFeature.PING] = await self.ping_update()
                case NodeFeature.SENSE:
                    states[NodeFeature.SENSE] = self._sense_statistics
                case NodeFeature.SENSE_HYSTERESIS:
                    states[NodeFeature.SENSE_HYSTERESIS] = self.hysteresis_config
                case _:
                    state_result = await super().get_state((feature,))
                    if feature in state_result:
                        states[feature] = state_result[feature]

        if NodeFeature.AVAILABLE not in states:
            states[NodeFeature.AVAILABLE] = self.available_state

        return states
