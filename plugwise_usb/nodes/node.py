"""Base class of Plugwise node device."""

from __future__ import annotations

from abc import ABC
from asyncio import Task, create_task
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
import logging
from typing import Any

from ..api import (
    AvailableState,
    BatteryConfig,
    EnergyStatistics,
    MotionConfig,
    MotionSensitivity,
    MotionState,
    NetworkStatistics,
    NodeEvent,
    NodeFeature,
    NodeInfo,
    NodeInfoMessage,
    NodeType,
    PowerStatistics,
    RelayConfig,
    RelayLock,
    RelayState,
    SenseStatistics,
)
from ..connection import StickController
from ..constants import SUPPRESS_INITIALIZATION_WARNINGS, TYPE_MODEL, UTF8
from ..exceptions import FeatureError, NodeError
from ..helpers.util import version_to_model
from ..messages.requests import NodeInfoRequest, NodePingRequest
from ..messages.responses import NodeInfoResponse, NodePingResponse
from .helpers import raise_not_loaded
from .helpers.cache import NodeCache
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
CACHE_RELAY = "relay"


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
        super().__init__()
        self._loaded_callback = loaded_callback
        self._message_subscribe = controller.subscribe_to_messages
        self._features: tuple[NodeFeature, ...] = NODE_FEATURES
        self._last_seen = datetime.now(tz=UTC)
        self._node_info = NodeInfo(mac, address)
        self._ping = NetworkStatistics()
        self._mac_in_bytes = bytes(mac, encoding=UTF8)
        self._mac_in_str = mac
        self._send = controller.send
        self._cache_enabled: bool = False
        self._cache_folder_create: bool = False
        self._cache_save_task: Task[None] | None = None
        self._node_cache = NodeCache(mac)
        # Sensors
        self._available: bool = False
        self._connected: bool = False
        self._initialized: bool = False
        self._initialization_delay_expired: datetime | None = None
        self._loaded: bool = False
        self._node_protocols: SupportedVersions | None = None

        # Node info
        self._current_log_address: int | None = None

    # region Properties

    @property
    def available(self) -> bool:
        """Return network availability state."""
        return self._available

    @property
    def available_state(self) -> AvailableState:
        """Network availability state."""
        return AvailableState(
            self._available,
            self._last_seen,
        )

    @property
    def node_protocols(self) -> SupportedVersions | None:
        """Return the node_protocols for the Node."""
        if self._node_protocols is None:
            return None

        return self._node_protocols

    @property
    @raise_not_loaded
    def battery_config(self) -> BatteryConfig:
        """Battery related configuration settings."""
        if NodeFeature.BATTERY not in self._features:
            raise FeatureError(
                f"Battery configuration property is not supported for node {self.mac}"
            )
        raise NotImplementedError()

    @property
    @raise_not_loaded
    def clock_sync(self) -> bool:
        """Indicate if the internal clock must be synced."""
        if NodeFeature.BATTERY not in self._features:
            raise FeatureError(
                f"Clock sync property is not supported for node {self.mac}"
            )
        raise NotImplementedError()

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
    @raise_not_loaded
    def energy(self) -> EnergyStatistics:
        """Energy statistics."""
        if NodeFeature.POWER not in self._features:
            raise FeatureError(f"Energy state is not supported for node {self.mac}")
        raise NotImplementedError()

    @property
    @raise_not_loaded
    def energy_consumption_interval(self) -> int | None:
        """Interval (minutes) energy consumption counters are locally logged at Circle devices."""
        if NodeFeature.ENERGY not in self._features:
            raise FeatureError(
                f"Energy log interval is not supported for node {self.mac}"
            )
        raise NotImplementedError()

    @property
    @raise_not_loaded
    def energy_production_interval(self) -> int | None:
        """Interval (minutes) energy production counters are locally logged at Circle devices."""
        if NodeFeature.ENERGY not in self._features:
            raise FeatureError(
                f"Energy log interval is not supported for node {self.mac}"
            )
        raise NotImplementedError()

    @property
    def features(self) -> tuple[NodeFeature, ...]:
        """Supported feature types of node."""
        return self._features

    @property
    def is_battery_powered(self) -> bool:
        """Return if node is battery powered."""
        return self._node_info.is_battery_powered

    @property
    def is_loaded(self) -> bool:
        """Return load status."""
        return self._loaded

    @property
    def last_seen(self) -> datetime:
        """Timestamp of last network activity."""
        return self._last_seen

    @property
    def name(self) -> str:
        """Return name of node."""
        if self._node_info.name is not None:
            return self._node_info.name
        return self._mac_in_str

    @property
    def network_address(self) -> int:
        """Zigbee network registration address."""
        return self._node_info.zigbee_address

    @property
    def node_info(self) -> NodeInfo:
        """Node information."""
        return self._node_info

    @property
    def mac(self) -> str:
        """Zigbee mac address of node."""
        return self._mac_in_str

    @property
    @raise_not_loaded
    def motion(self) -> bool:
        """Motion detection value."""
        if NodeFeature.MOTION not in self._features:
            raise FeatureError(f"Motion state is not supported for node {self.mac}")
        raise NotImplementedError()

    @property
    @raise_not_loaded
    def motion_config(self) -> MotionConfig:
        """Motion configuration settings."""
        if NodeFeature.MOTION not in self._features:
            raise FeatureError(
                f"Motion configuration is not supported for node {self.mac}"
            )
        raise NotImplementedError()

    @property
    @raise_not_loaded
    def motion_state(self) -> MotionState:
        """Motion detection state."""
        if NodeFeature.MOTION not in self._features:
            raise FeatureError(f"Motion state is not supported for node {self.mac}")
        raise NotImplementedError()

    @property
    def ping_stats(self) -> NetworkStatistics:
        """Ping statistics."""
        return self._ping

    @property
    @raise_not_loaded
    def power(self) -> PowerStatistics:
        """Power statistics."""
        if NodeFeature.POWER not in self._features:
            raise FeatureError(f"Power state is not supported for node {self.mac}")
        raise NotImplementedError()

    @property
    @raise_not_loaded
    def relay_config(self) -> RelayConfig:
        """Relay configuration."""
        if NodeFeature.RELAY_INIT not in self._features:
            raise FeatureError(
                f"Relay configuration is not supported for node {self.mac}"
            )
        raise NotImplementedError()

    @property
    @raise_not_loaded
    def relay(self) -> bool:
        """Relay value."""
        if NodeFeature.RELAY not in self._features:
            raise FeatureError(f"Relay value is not supported for node {self.mac}")
        raise NotImplementedError()

    @property
    @raise_not_loaded
    def relay_state(self) -> RelayState:
        """State of relay."""
        if NodeFeature.RELAY not in self._features:
            raise FeatureError(f"Relay state is not supported for node {self.mac}")
        raise NotImplementedError()

    @property
    @raise_not_loaded
    def relay_lock(self) -> RelayLock:
        """State of relay lock."""
        if NodeFeature.RELAY_LOCK not in self._features:
            raise FeatureError(f"Relay lock is not supported for node {self.mac}")
        raise NotImplementedError()

    @property
    @raise_not_loaded
    def switch(self) -> bool:
        """Switch button value."""
        if NodeFeature.SWITCH not in self._features:
            raise FeatureError(f"Switch value is not supported for node {self.mac}")
        raise NotImplementedError()

    @property
    @raise_not_loaded
    def sense(self) -> SenseStatistics:
        """Sense statistics."""
        if NodeFeature.SENSE not in self._features:
            raise FeatureError(f"Sense statistics is not supported for node {self.mac}")

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
            ) is not None and (
                self._node_protocols.min <= required_version <= self._node_protocols.max
                and feature not in self._features
            ):
                self._features += (feature,)

        self._node_info.features = self._features

    async def reconnect(self) -> None:
        """Reconnect node to Plugwise Zigbee network."""
        if await self.ping_update() is not None:
            self._connected = True
            await self._available_update_state(True, None)

    async def disconnect(self) -> None:
        """Disconnect node from Plugwise Zigbee network."""
        self._connected = False
        await self._available_update_state(False)

    async def scan_calibrate_light(self) -> bool:
        """Request to calibration light sensitivity of Scan device. Returns True if successful."""
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

    async def initialize(self) -> None:
        """Initialize node configuration."""
        if self._initialized:
            return

        self._initialization_delay_expired = datetime.now(tz=UTC) + timedelta(
            minutes=SUPPRESS_INITIALIZATION_WARNINGS
        )
        self._initialized = True

    async def _available_update_state(
        self, available: bool, timestamp: datetime | None = None
    ) -> None:
        """Update the node availability state."""
        if self._available == available:
            if (
                self._last_seen is not None
                and timestamp is not None
                and int((timestamp - self._last_seen).total_seconds()) > 5  # noqa: PLR2004
            ):
                self._last_seen = timestamp
                await self.publish_feature_update_to_subscribers(
                    NodeFeature.AVAILABLE, self.available_state
                )
            return
        if timestamp is not None:
            self._last_seen = timestamp
        if available:
            _LOGGER.info("Device %s detected to be available (on-line)", self.name)
            self._available = True
            await self.publish_feature_update_to_subscribers(
                NodeFeature.AVAILABLE, self.available_state
            )
            return
        _LOGGER.info("Device %s detected to be not available (off-line)", self.name)
        self._available = False
        await self.publish_feature_update_to_subscribers(
            NodeFeature.AVAILABLE, self.available_state
        )

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

        await self._available_update_state(True, node_info.timestamp)
        await self.update_node_details(node_info)
        return self._node_info

    async def _node_info_load_from_cache(self) -> bool:
        """Load node info settings from cache."""
        if self._available:
            # Skip loading this data from cache when the Node is available
            return True

        firmware = self._get_cache_as_datetime(CACHE_FIRMWARE)
        hardware = self._get_cache(CACHE_HARDWARE)
        node_type: NodeType | None = None
        if (node_type_str := self._get_cache(CACHE_NODE_TYPE)) is not None:
            node_type = NodeType(int(node_type_str))
        relay_state = self._get_cache(CACHE_RELAY) == "True"
        timestamp = self._get_cache_as_datetime(CACHE_NODE_INFO_TIMESTAMP)
        node_info = NodeInfoMessage(
            current_logaddress_pointer=None,
            firmware=firmware,
            hardware=hardware,
            node_type=node_type,
            relay_state=relay_state,
            timestamp=timestamp,
        )
        return await self.update_node_details(node_info)

    async def update_node_details(
        self, node_info: NodeInfoResponse | NodeInfoMessage | None = None
    ) -> bool:
        """Process new node info and return true if all fields are updated."""
        _LOGGER.debug(
            "update_node_details | firmware=%s, hardware=%s, nodetype=%s",
            node_info.firmware,
            node_info.hardware,
            node_info.node_type,
        )
        _LOGGER.debug(
            "update_node_details | timestamp=%s, relay_state=%s, logaddress_pointer=%s,",
            node_info.timestamp,
            node_info.relay_state,
            node_info.current_logaddress_pointer,
        )
        complete = True
        if node_info.node_type is None:
            complete = False
        else:
            self._node_info.node_type = NodeType(node_info.node_type)
            self._set_cache(CACHE_NODE_TYPE, self._node_info.node_type.value)

        if node_info.firmware is None:
            complete = False
        else:
            self._node_info.firmware = node_info.firmware
            self._set_cache(CACHE_FIRMWARE, node_info.firmware)

        complete &= self._update_node_details_hardware(node_info.hardware)
        complete &= self._update_node_details_timestamp(node_info.timestamp)

        _LOGGER.debug("Saving Node calibration update to cache for %s", self.mac)
        await self.save_cache()
        if node_info.timestamp is not None and node_info.timestamp > datetime.now(
            tz=UTC
        ) - timedelta(minutes=5):
            await self._available_update_state(True, node_info.timestamp)

        return complete

    def _update_node_details_timestamp(self, timestamp: datetime | None) -> bool:
        if timestamp is None:
            return False
        else:
            self._node_info.timestamp = timestamp
            self._set_cache(CACHE_NODE_INFO_TIMESTAMP, timestamp)
        return True

    def _update_node_details_hardware(self, hardware: str | None) -> bool:
        if hardware is None:
            return False
        else:
            if self._node_info.version != hardware:
                # Generate modelname based on hardware version
                hardware, model_info = version_to_model(hardware)
                model_info = model_info.split(" ")
                self._node_info.model = model_info[0]
                # Correct model when node_type doesn't match
                # Switch reports hardware version of paired Circle (pw_usb_beta #245)
                if self._node_info.node_type is not None:
                    allowed_models = TYPE_MODEL.get(self._node_info.node_type.value)
                    if allowed_models and model_info[0] not in allowed_models:
                        # Replace model_info list
                        model_info = [
                            allowed_models[0]
                        ]  # Not correct for 1 but should not be a problem
                        self._node_info.model = model_info[0]

                # Handle + devices
                if len(model_info) > 1 and "+" in model_info[1]:
                    self._node_info.model = model_info[0] + " " + model_info[1]
                    model_info[0] = self._node_info.model
                    model_info.pop(1)

                self._node_info.version = hardware
                if self._node_info.model == "Unknown":
                    _LOGGER.warning(
                        "Failed to detect hardware model for %s based on '%s'",
                        self.mac,
                        hardware,
                    )

                self._node_info.model_type = None
                if len(model_info) > 1:
                    self._node_info.model_type = " ".join(model_info[1:])

                if self._node_info.model is not None:
                    self._node_info.name = f"{model_info[0]} {self._node_info.mac[-5:]}"

            self._set_cache(CACHE_HARDWARE, hardware)
        return True

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
        await self._available_update_state(True, ping_response.timestamp)
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

            match feature:
                case NodeFeature.INFO:
                    states[NodeFeature.INFO] = await self.node_info_update()
                case NodeFeature.AVAILABLE:
                    states[NodeFeature.AVAILABLE] = self.available_state
                case NodeFeature.PING:
                    states[NodeFeature.PING] = await self.ping_update()
                case _:
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
        _LOGGER.debug("Writing cache to disk while unloading for %s", self.mac)
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
            if len(data) == 6:  # noqa: PLR2004
                try:
                    return datetime(
                        year=int(data[0]),
                        month=int(data[1]),
                        day=int(data[2]),
                        hour=int(data[3]),
                        minute=int(data[4]),
                        second=int(data[5]),
                        tzinfo=UTC,
                    )
                except ValueError:
                    _LOGGER.warning(
                        "Invalid datetime format in cache for setting %s: %s",
                        setting,
                        timestamp_str,
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
        if data_class.timestamp + timedelta(seconds=seconds) > datetime.now(tz=UTC):
            return True
        return False

    # region Configuration of properties
    @raise_not_loaded
    async def set_awake_duration(self, seconds: int) -> bool:
        """Change the awake duration."""
        if NodeFeature.BATTERY not in self._features:
            raise FeatureError(
                f"Changing awake duration is not supported for node {self.mac}"
            )
        raise NotImplementedError()

    @raise_not_loaded
    async def set_clock_interval(self, minutes: int) -> bool:
        """Change the clock interval."""
        if NodeFeature.BATTERY not in self._features:
            raise FeatureError(
                f"Changing clock interval is not supported for node {self.mac}"
            )
        raise NotImplementedError()

    @raise_not_loaded
    async def set_clock_sync(self, sync: bool) -> bool:
        """Change the clock synchronization setting."""
        if NodeFeature.BATTERY not in self._features:
            raise FeatureError(
                f"Configuration of clock sync is not supported for node {self.mac}"
            )
        raise NotImplementedError()

    @raise_not_loaded
    async def set_maintenance_interval(self, minutes: int) -> bool:
        """Change the maintenance interval."""
        if NodeFeature.BATTERY not in self._features:
            raise FeatureError(
                f"Changing maintenance interval is not supported for node {self.mac}"
            )
        raise NotImplementedError()

    @raise_not_loaded
    async def set_motion_daylight_mode(self, state: bool) -> bool:
        """Configure if motion must be detected when light level is below threshold."""
        if NodeFeature.MOTION not in self._features:
            raise FeatureError(
                f"Configuration of daylight mode is not supported for node {self.mac}"
            )
        raise NotImplementedError()

    @raise_not_loaded
    async def set_motion_reset_timer(self, minutes: int) -> bool:
        """Configure the motion reset timer in minutes."""
        if NodeFeature.MOTION not in self._features:
            raise FeatureError(
                f"Changing motion reset timer is not supported for node {self.mac}"
            )
        raise NotImplementedError()

    @raise_not_loaded
    async def set_motion_sensitivity_level(self, level: MotionSensitivity) -> bool:
        """Configure motion sensitivity level."""
        if NodeFeature.MOTION not in self._features:
            raise FeatureError(
                f"Configuration of motion sensitivity is not supported for node {self.mac}"
            )
        raise NotImplementedError()

    @raise_not_loaded
    async def set_relay(self, state: bool) -> bool:
        """Change the state of the relay."""
        if NodeFeature.RELAY not in self._features:
            raise FeatureError(
                f"Changing relay state is not supported for node {self.mac}"
            )
        raise NotImplementedError()

    @raise_not_loaded
    async def set_relay_lock(self, state: bool) -> bool:
        """Change lock of the relay."""
        if NodeFeature.RELAY_LOCK not in self._features:
            raise FeatureError(
                f"Changing relay lock state is not supported for node {self.mac}"
            )
        raise NotImplementedError()

    @raise_not_loaded
    async def set_relay_init(self, state: bool) -> bool:
        """Change the initial power-on state of the relay."""
        if NodeFeature.RELAY_INIT not in self._features:
            raise FeatureError(
                f"Configuration of initial power-up relay state is not supported for node {self.mac}"
            )
        raise NotImplementedError()

    @raise_not_loaded
    async def set_sleep_duration(self, minutes: int) -> bool:
        """Change the sleep duration."""
        if NodeFeature.BATTERY not in self._features:
            raise FeatureError(
                f"Configuration of sleep duration is not supported for node {self.mac}"
            )
        raise NotImplementedError()

    # endregion

    async def message_for_node(self, message: Any) -> None:
        """Process message for node."""
        if isinstance(message, NodePingResponse):
            await self.ping_update(message)
        elif isinstance(message, NodeInfoResponse):
            await self.node_info_update(message)
        else:
            raise NotImplementedError()
