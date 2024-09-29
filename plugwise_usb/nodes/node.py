"""Base class of Plugwise node device."""

from __future__ import annotations

from abc import ABC
from asyncio import Task, create_task
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
import logging
from typing import Any

from ..api import (
    BatteryConfig,
    EnergyStatistics,
    MotionSensitivity,
    MotionState,
    NetworkStatistics,
    NodeEvent,
    NodeFeature,
    NodeInfo,
    NodeType,
    PowerStatistics,
    RelayState,
)
from ..connection import StickController
from ..constants import SUPPRESS_INITIALIZATION_WARNINGS, UTF8
from ..exceptions import NodeError
from ..helpers.util import version_to_model
from ..messages.requests import NodeInfoRequest, NodePingRequest
from ..messages.responses import NodeInfoResponse, NodePingResponse
from .helpers import EnergyCalibration, raise_not_loaded
from .helpers.cache import NodeCache
from .helpers.counter import EnergyCounters
from .helpers.firmware import FEATURE_SUPPORTED_AT_FIRMWARE, SupportedVersions
from .helpers.subscription import FeaturePublisher

_LOGGER = logging.getLogger(__name__)


NODE_FEATURES = (
    NodeFeature.AVAILABLE,
    NodeFeature.INFO,
    NodeFeature.PING,
)


CACHE_FIRMWARE = "firmware"
CACHE_NODE_TYPE = "node_type"
CACHE_HARDWARE = "hardware"
CACHE_NODE_INFO_TIMESTAMP = "node_info_timestamp"


class PlugwiseBaseNode(FeaturePublisher, ABC):
    """Abstract Base Class for a Plugwise node."""

    def __init__(
        self,
        mac: str,
        address: int,
        controller: StickController,
        loaded_callback: Callable[[NodeEvent, str], Awaitable[None]],
    ):
        """Initialize Plugwise base node class."""
        self._loaded_callback = loaded_callback
        self._message_subscribe = controller.subscribe_to_node_responses
        self._features: tuple[NodeFeature, ...] = NODE_FEATURES
        self._last_update = datetime.now(UTC)
        self._node_info = NodeInfo(mac, address)
        self._ping = NetworkStatistics()
        self._power = PowerStatistics()
        self._mac_in_bytes = bytes(mac, encoding=UTF8)
        self._mac_in_str = mac
        self._send = controller.send
        self._cache_enabled: bool = False
        self._cache_save_task: Task[None] | None = None
        self._node_cache = NodeCache(mac, "")
        # Sensors
        self._available: bool = False
        self._humidity: float | None = None
        self._motion: bool | None = None
        self._switch: bool | None = None
        self._temperature: float | None = None
        self._connected: bool = False
        self._initialized: bool = False
        self._initialization_delay_expired: datetime | None = None
        self._loaded: bool = False
        self._node_protocols: SupportedVersions | None = None
        self._node_last_online: datetime | None = None
        # Battery
        self._battery_config = BatteryConfig()
        # Motion
        self._motion = False
        self._motion_state = MotionState()
        self._scan_subscription: Callable[[], None] | None = None
        self._sensitivity_level: MotionSensitivity | None = None
        # Node info
        self._current_log_address: int | None = None
        # Relay
        self._relay: bool | None = None
        self._relay_state: RelayState = RelayState()
        self._relay_init_state: bool | None = None
        # Power & energy
        self._calibration: EnergyCalibration | None = None
        self._energy_counters = EnergyCounters(mac)

    # region Properties

    @property
    def network_address(self) -> int:
        """Zigbee network registration address."""
        return self._node_info.zigbee_address

    @property
    def cache_folder(self) -> str:
        """Return path to cache folder."""
        return self._node_cache.cache_root_directory

    @cache_folder.setter
    def cache_folder(self, cache_folder: str) -> None:
        """Set path to cache folder."""
        self._node_cache.cache_root_directory = cache_folder

    @property
    def cache_folder_create(self) -> bool:
        """Return if cache folder must be create when it does not exists."""
        return self._cache_folder_create

    @cache_folder_create.setter
    def cache_folder_create(self, enable: bool = True) -> None:
        """Enable or disable creation of cache folder."""
        self._cache_folder_create = enable

    @property
    def cache_enabled(self) -> bool:
        """Return usage of cache."""
        return self._cache_enabled

    @cache_enabled.setter
    def cache_enabled(self, enable: bool) -> None:
        """Enable or disable usage of cache."""
        self._cache_enabled = enable

    @property
    def available(self) -> bool:
        """Return network availability state."""
        return self._available

    @property
    def battery_config(self) -> BatteryConfig:
        """Return battery configuration settings."""
        if NodeFeature.BATTERY not in self._features:
            raise NodeError(
                f"Battery configuration settings are not supported for node {self.mac}"
            )
        return self._battery_config

    @property
    def is_battery_powered(self) -> bool:
        """Return if node is battery powered."""
        return self._node_info.is_battery_powered

    @property
    def daylight_mode(self) -> bool:
        """Daylight mode of motion sensor."""
        if NodeFeature.MOTION not in self._features:
            raise NodeError(f"Daylight mode is not supported for node {self.mac}")
        raise NotImplementedError()

    @property
    def energy(self) -> EnergyStatistics | None:
        """Energy statistics."""
        if NodeFeature.POWER not in self._features:
            raise NodeError(f"Energy state is not supported for node {self.mac}")
        raise NotImplementedError()

    @property
    def energy_consumption_interval(self) -> int | None:
        """Interval (minutes) energy consumption counters are locally logged at Circle devices."""
        if NodeFeature.ENERGY not in self._features:
            raise NodeError(f"Energy log interval is not supported for node {self.mac}")
        return self._energy_counters.consumption_interval

    @property
    def energy_production_interval(self) -> int | None:
        """Interval (minutes) energy production counters are locally logged at Circle devices."""
        if NodeFeature.ENERGY not in self._features:
            raise NodeError(f"Energy log interval is not supported for node {self.mac}")
        return self._energy_counters.production_interval

    @property
    def features(self) -> tuple[NodeFeature, ...]:
        """Supported feature types of node."""
        return self._features

    @property
    def node_info(self) -> NodeInfo:
        """Node information."""
        return self._node_info

    @property
    def humidity(self) -> float | None:
        """Humidity state."""
        if NodeFeature.HUMIDITY not in self._features:
            raise NodeError(f"Humidity state is not supported for node {self.mac}")
        return self._humidity

    @property
    def last_update(self) -> datetime:
        """Timestamp of last update."""
        return self._last_update

    @property
    def is_loaded(self) -> bool:
        """Return load status."""
        return self._loaded

    @property
    def name(self) -> str:
        """Return name of node."""
        if self._node_info.name is not None:
            return self._node_info.name
        return self._mac_in_str

    @property
    def mac(self) -> str:
        """Zigbee mac address of node."""
        return self._mac_in_str

    @property
    def maintenance_interval(self) -> int | None:
        """Maintenance interval (seconds) a battery powered node sends it heartbeat."""
        raise NotImplementedError()

    @property
    def motion(self) -> bool | None:
        """Motion detection value."""
        if NodeFeature.MOTION not in self._features:
            raise NodeError(f"Motion state is not supported for node {self.mac}")
        return self._motion

    @property
    def motion_state(self) -> MotionState:
        """Motion detection state."""
        if NodeFeature.MOTION not in self._features:
            raise NodeError(f"Motion state is not supported for node {self.mac}")
        return self._motion_state

    @property
    def motion_reset_timer(self) -> int:
        """Total minutes without motion before no motion is reported."""
        if NodeFeature.MOTION not in self._features:
            raise NodeError(f"Motion reset timer is not supported for node {self.mac}")
        raise NotImplementedError()

    @property
    def ping_stats(self) -> NetworkStatistics:
        """Ping statistics."""
        return self._ping

    @property
    def power(self) -> PowerStatistics:
        """Power statistics."""
        if NodeFeature.POWER not in self._features:
            raise NodeError(f"Power state is not supported for node {self.mac}")
        return self._power

    @property
    def relay_state(self) -> RelayState:
        """State of relay."""
        if NodeFeature.RELAY not in self._features:
            raise NodeError(f"Relay state is not supported for node {self.mac}")
        return self._relay_state

    @property
    def relay(self) -> bool:
        """Relay value."""
        if NodeFeature.RELAY not in self._features:
            raise NodeError(f"Relay value is not supported for node {self.mac}")
        if self._relay is None:
            raise NodeError(f"Relay value is unknown for node {self.mac}")
        return self._relay

    @property
    def relay_init(
        self,
    ) -> bool | None:
        """Request the relay states at startup/power-up."""
        raise NotImplementedError()

    @property
    def sensitivity_level(self) -> MotionSensitivity:
        """Sensitivity level of motion sensor."""
        if NodeFeature.MOTION not in self._features:
            raise NodeError(f"Sensitivity level is not supported for node {self.mac}")
        raise NotImplementedError()

    @property
    def switch(self) -> bool | None:
        """Switch button value."""
        if NodeFeature.SWITCH not in self._features:
            raise NodeError(f"Switch value is not supported for node {self.mac}")
        return self._switch

    @property
    def temperature(self) -> float | None:
        """Temperature value."""
        if NodeFeature.TEMPERATURE not in self._features:
            raise NodeError(f"Temperature state is not supported for node {self.mac}")
        return self._temperature

    # endregion

    def _setup_protocol(
        self,
        firmware: dict[datetime, SupportedVersions],
        node_features: tuple[NodeFeature, ...],
    ) -> None:
        """Determine protocol version based on firmware version and enable supported additional supported features."""
        if self._node_info.firmware is None:
            return
        self._node_protocols = firmware.get(self._node_info.firmware, None)
        if self._node_protocols is None:
            _LOGGER.warning(
                "Failed to determine the protocol version for node %s (%s) based on firmware version %s of list %s",
                self._node_info.mac,
                self.__class__.__name__,
                self._node_info.firmware,
                str(firmware.keys()),
            )
            return
        for feature in node_features:
            if (
                required_version := FEATURE_SUPPORTED_AT_FIRMWARE.get(feature)
            ) is not None:
                if (
                    self._node_protocols.min
                    <= required_version
                    <= self._node_protocols.max
                    and feature not in self._features
                ):
                    self._features += (feature,)
        self._node_info.features = self._features

    async def reconnect(self) -> None:
        """Reconnect node to Plugwise Zigbee network."""
        if await self.ping_update() is not None:
            self._connected = True
            await self._available_update_state(True)

    async def disconnect(self) -> None:
        """Disconnect node from Plugwise Zigbee network."""
        self._connected = False
        await self._available_update_state(False)

    async def configure_motion_reset(self, delay: int) -> bool:
        """Configure the duration to reset motion state."""
        raise NotImplementedError()

    async def scan_calibrate_light(self) -> bool:
        """Request to calibration light sensitivity of Scan device. Returns True if successful."""
        raise NotImplementedError()

    async def scan_configure(
        self,
        motion_reset_timer: int,
        sensitivity_level: MotionSensitivity,
        daylight_mode: bool,
    ) -> bool:
        """Configure Scan device settings. Returns True if successful."""
        raise NotImplementedError()

    async def load(self) -> bool:
        """Load configuration and activate node features."""
        raise NotImplementedError()

    async def _load_cache_file(self) -> bool:
        """Load states from previous cached information."""
        if self._loaded:
            return True
        if not self._cache_enabled:
            _LOGGER.warning(
                "Unable to load node %s from cache because caching is disabled",
                self.mac,
            )
            return False
        if not self._node_cache.initialized:
            await self._node_cache.initialize_cache(self._cache_folder_create)
        return await self._node_cache.restore_cache()

    async def clear_cache(self) -> None:
        """Clear current cache."""
        if self._node_cache is not None:
            await self._node_cache.clear_cache()

    async def _load_from_cache(self) -> bool:
        """Load states from previous cached information. Return True if successful."""
        if self._loaded:
            return True
        if not await self._load_cache_file():
            _LOGGER.debug("Node %s failed to load cache file", self.mac)
            return False
        # Node Info
        if not await self._node_info_load_from_cache():
            _LOGGER.debug("Node %s failed to load node_info from cache", self.mac)
            return False
        return True

    async def initialize(self) -> bool:
        """Initialize node configuration."""
        if self._initialized:
            return True
        self._initialization_delay_expired = datetime.now(UTC) + timedelta(
            minutes=SUPPRESS_INITIALIZATION_WARNINGS
        )
        self._initialized = True
        return True

    async def _available_update_state(self, available: bool) -> None:
        """Update the node availability state."""
        if self._available == available:
            return
        if available:
            _LOGGER.info("Device %s detected to be available (on-line)", self.name)
            self._available = True
            await self.publish_feature_update_to_subscribers(
                NodeFeature.AVAILABLE, True
            )
            return
        _LOGGER.info("Device %s detected to be not available (off-line)", self.name)
        self._available = False
        await self.publish_feature_update_to_subscribers(NodeFeature.AVAILABLE, False)

    async def node_info_update(
        self, node_info: NodeInfoResponse | None = None
    ) -> NodeInfo | None:
        """Update Node hardware information."""
        if node_info is None:
            request = NodeInfoRequest(self._send, self._mac_in_bytes)
            node_info = await request.send()
        if node_info is None:
            _LOGGER.debug("No response for node_info_update() for %s", self.mac)
            await self._available_update_state(False)
            return self._node_info
        await self._available_update_state(True)
        await self.update_node_details(
            firmware=node_info.firmware,
            node_type=node_info.node_type,
            hardware=node_info.hardware,
            timestamp=node_info.timestamp,
            relay_state=node_info.relay_state,
            logaddress_pointer=node_info.current_logaddress_pointer,
        )
        return self._node_info

    async def _node_info_load_from_cache(self) -> bool:
        """Load node info settings from cache."""
        firmware = self._get_cache_as_datetime(CACHE_FIRMWARE)
        hardware = self._get_cache(CACHE_HARDWARE)
        timestamp = self._get_cache_as_datetime(CACHE_NODE_INFO_TIMESTAMP)
        node_type: NodeType | None = None
        if (node_type_str := self._get_cache(CACHE_NODE_TYPE)) is not None:
            node_type = NodeType(int(node_type_str))
        return await self.update_node_details(
            firmware=firmware,
            hardware=hardware,
            node_type=node_type,
            timestamp=timestamp,
            relay_state=None,
            logaddress_pointer=None,
        )

    async def update_node_details(
        self,
        firmware: datetime | None,
        hardware: str | None,
        node_type: NodeType | None,
        timestamp: datetime | None,
        relay_state: bool | None,
        logaddress_pointer: int | None,
    ) -> bool:
        """Process new node info and return true if all fields are updated."""
        complete = True
        if firmware is None:
            complete = False
        else:
            self._node_info.firmware = firmware
            self._set_cache(CACHE_FIRMWARE, firmware)
        if hardware is None:
            complete = False
        else:
            if self._node_info.version != hardware:
                self._node_info.version = hardware
                # Generate modelname based on hardware version
                model_info = version_to_model(hardware).split(" ")
                self._node_info.model = model_info[0]
                if self._node_info.model == "Unknown":
                    _LOGGER.warning(
                        "Failed to detect hardware model for %s based on '%s'",
                        self.mac,
                        hardware,
                    )
                if len(model_info) > 1:
                    self._node_info.model_type = " ".join(model_info[1:])
                else:
                    self._node_info.model_type = ""
                if self._node_info.model is not None:
                    self._node_info.name = f"{model_info[0]} {self._node_info.mac[-5:]}"
            self._set_cache(CACHE_HARDWARE, hardware)
        if timestamp is None:
            complete = False
        else:
            self._node_info.timestamp = timestamp
            self._set_cache(CACHE_NODE_INFO_TIMESTAMP, timestamp)
        if node_type is None:
            complete = False
        else:
            self._node_info.node_type = NodeType(node_type)
            self._set_cache(CACHE_NODE_TYPE, self._node_info.node_type.value)
        await self.save_cache()
        return complete

    async def is_online(self) -> bool:
        """Check if node is currently online."""
        if await self.ping_update() is None:
            _LOGGER.debug("No response to ping for %s", self.mac)
            return False
        return True

    async def ping_update(
        self, ping_response: NodePingResponse | None = None, retries: int = 1
    ) -> NetworkStatistics | None:
        """Update ping statistics."""
        if ping_response is None:
            request = NodePingRequest(self._send, self._mac_in_bytes, retries)
            ping_response = await request.send()
        if ping_response is None:
            await self._available_update_state(False)
            return None
        await self._available_update_state(True)
        self.update_ping_stats(
            ping_response.timestamp,
            ping_response.rssi_in,
            ping_response.rssi_out,
            ping_response.rtt,
        )
        await self.publish_feature_update_to_subscribers(NodeFeature.PING, self._ping)
        return self._ping

    def update_ping_stats(
        self, timestamp: datetime, rssi_in: int, rssi_out: int, rtt: int
    ) -> None:
        """Update ping statistics."""
        self._ping.timestamp = timestamp
        self._ping.rssi_in = rssi_in
        self._ping.rssi_out = rssi_out
        self._ping.rtt = rtt
        self._available = True

    async def switch_relay(self, state: bool) -> bool | None:
        """Switch relay state."""
        raise NodeError(f"Relay control is not supported for node {self.mac}")

    async def switch_relay_init(self, state: bool) -> bool:
        """Switch state of initial power-up relay state. Returns new state of relay."""
        raise NodeError(f"Control of initial (power-up) state of relay is not supported for node {self.mac}")

    @raise_not_loaded
    async def get_state(self, features: tuple[NodeFeature]) -> dict[NodeFeature, Any]:
        """Update latest state for given feature."""
        states: dict[NodeFeature, Any] = {}
        for feature in features:
            if feature not in self._features:
                raise NodeError(
                    f"Update of feature '{feature.name}' is "
                    + f"not supported for {self.mac}"
                )
            if feature == NodeFeature.INFO:
                states[NodeFeature.INFO] = await self.node_info_update()
            elif feature == NodeFeature.AVAILABLE:
                states[NodeFeature.AVAILABLE] = self._available
            elif feature == NodeFeature.PING:
                states[NodeFeature.PING] = await self.ping_update()
            else:
                raise NodeError(
                    f"Update of feature '{feature.name}' is "
                    + f"not supported for {self.mac}"
                )
        return states

    async def unload(self) -> None:
        """Deactivate and unload node features."""
        if not self._cache_enabled:
            return
        if self._cache_save_task is not None and not self._cache_save_task.done():
            await self._cache_save_task
        await self.save_cache(trigger_only=False, full_write=True)

    def _get_cache(self, setting: str) -> str | None:
        """Retrieve value of specified setting from cache memory."""
        if not self._cache_enabled:
            return None
        return self._node_cache.get_state(setting)

    def _get_cache_as_datetime(self, setting: str) -> datetime | None:
        """Retrieve value of specified setting from cache memory and return it as datetime object."""
        if (timestamp_str := self._get_cache(setting)) is not None:
            data = timestamp_str.split("-")
            if len(data) == 6:
                return datetime(
                    year=int(data[0]),
                    month=int(data[1]),
                    day=int(data[2]),
                    hour=int(data[3]),
                    minute=int(data[4]),
                    second=int(data[5]),
                    tzinfo=UTC,
                )
        return None

    def _set_cache(self, setting: str, value: Any) -> None:
        """Store setting with value in cache memory."""
        if not self._cache_enabled:
            return
        if isinstance(value, datetime):
            self._node_cache.update_state(
                setting,
                f"{value.year}-{value.month}-{value.day}-{value.hour}"
                + f"-{value.minute}-{value.second}",
            )
        elif isinstance(value, str):
            self._node_cache.update_state(setting, value)
        else:
            self._node_cache.update_state(setting, str(value))

    async def save_cache(
        self, trigger_only: bool = True, full_write: bool = False
    ) -> None:
        """Save cached data to cache file when cache is enabled."""
        if not self._cache_enabled or not self._loaded or not self._initialized:
            return
        _LOGGER.debug("Save cache file for node %s", self.mac)
        if self._cache_save_task is not None and not self._cache_save_task.done():
            await self._cache_save_task
        if trigger_only:
            self._cache_save_task = create_task(self._node_cache.save_cache())
        else:
            await self._node_cache.save_cache(rewrite=full_write)

    @staticmethod
    def skip_update(data_class: Any, seconds: int) -> bool:
        """Check if update can be skipped when timestamp of given dataclass is less than given seconds old."""
        if data_class is None:
            return False
        if not hasattr(data_class, "timestamp"):
            return False
        if data_class.timestamp is None:
            return False
        if data_class.timestamp + timedelta(seconds=seconds) > datetime.now(UTC):
            return True
        return False
