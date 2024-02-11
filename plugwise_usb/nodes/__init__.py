"""Plugwise devices linked to USB-stick."""

from __future__ import annotations

from abc import ABC
from asyncio import create_task
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from ..api import (
    MotionState,
    NetworkStatistics,
    NodeFeature,
    NodeInfo,
    NodeType,
    PowerStatistics,
    RelayState,
)
from ..connection import StickController
from ..constants import UTF8, MotionSensitivity
from ..exceptions import NodeError, StickError, PlugwiseException
from ..messages.requests import NodeInfoRequest, NodePingRequest
from ..messages.responses import NodeInfoResponse, NodePingResponse
from ..util import version_to_model
from .helpers import raise_not_loaded
from .helpers.cache import NodeCache
from .helpers.counter import EnergyStatistics, EnergyCalibration, EnergyCounters
from .helpers.firmware import FEATURE_SUPPORTED_AT_FIRMWARE, SupportedVersions
from .helpers.subscription import FeaturePublisher

_LOGGER = logging.getLogger(__name__)
NODE_FEATURES = (
    NodeFeature.AVAILABLE,
    NodeFeature.INFO,
    NodeFeature.PING,
)


class PlugwiseNode(FeaturePublisher, ABC):
    """Abstract Base Class for a Plugwise node."""

    def __init__(
        self,
        mac: str,
        address: int,
        controller: StickController,
        loaded_callback: Callable,
    ):
        """Initialize Plugwise base node class."""
        self._loaded_callback = loaded_callback
        self._message_subscribe = controller.subscribe_to_node_responses
        self._features = NODE_FEATURES
        self._last_update = datetime.now(timezone.utc)
        self._node_info = NodeInfo(mac, address)
        self._ping = NetworkStatistics()
        self._power = PowerStatistics()

        self._mac_in_bytes = bytes(mac, encoding=UTF8)
        self._mac_in_str = mac
        self._send = controller.send
        self._parent_mac: str = controller.mac_coordinator

        self._node_cache: NodeCache | None = None
        self._cache_enabled: bool = False
        self._cache_folder: str = ""

        # Sensors
        self._available: bool = False
        self._humidity: float | None = None
        self._motion: bool | None = None

        self._switch: bool | None = None
        self._temperature: float | None = None

        self._connected: bool = False
        self._initialized: bool = False
        self._loaded: bool = False
        self._node_protocols: SupportedVersions | None = None
        self._node_last_online: datetime | None = None

        # Motion
        self._motion = False
        self._motion_state = MotionState()
        self._motion_reset_timer: int | None = None
        self._scan_subscription: Callable[[], None] | None = None
        self._motion_reset_timer = None
        self._daylight_mode: bool | None = None
        self._sensitivity_level: MotionSensitivity | None = None
        self._new_motion_reset_timer: int | None = None
        self._new_daylight_mode: bool | None = None
        self._new_sensitivity: MotionSensitivity | None = None

        # Node info
        self._current_log_address: int | None = None

        # Relay
        self._relay: bool | None = None
        self._relay_state = RelayState()
        self._relay_init_state: bool | None = None

        # Power & energy
        self._calibration: EnergyCalibration | None = None
        self._energy_counters = EnergyCounters(mac)

    @property
    def network_address(self) -> int:
        """Network (zigbee based) registration address of this node."""
        return self._node_info.zigbee_address

    @property
    def parent_mac(self) -> str:
        return self._parent_mac

    @property
    def cache_folder(self) -> str:
        """Return path to cache folder."""
        return self._cache_folder

    @cache_folder.setter
    def cache_folder(self, cache_folder: str) -> None:
        """Set path to cache folder."""
        if cache_folder == self._cache_folder:
            return
        self._cache_folder = cache_folder
        if self._cache_enabled:
            if self._node_cache is None:
                self._node_cache = NodeCache(self._cache_folder)
            else:
                self._node_cache.cache_root_directory = cache_folder

    @property
    def cache_enabled(self) -> bool:
        """Return usage of cache."""
        return self._cache_enabled

    @cache_enabled.setter
    def cache_enabled(self, enable: bool) -> None:
        """Enable or disable usage of cache."""
        if enable == self._cache_enabled:
            return
        if enable:
            if self._node_cache is None:
                self._node_cache = NodeCache(self.mac, self._cache_folder)
            else:
                self._node_cache.cache_root_directory = self._cache_folder
        else:
            self._node_cache = None
        self._cache_enabled = enable

    @property
    def available(self) -> bool:
        """Return network availability state."""
        return self._available

    @property
    def energy(self) -> EnergyStatistics | None:
        """"Return energy statistics."""
        if NodeFeature.POWER not in self._features:
            raise NodeError(
                f"Energy state is not supported for node {self.mac}"
            )

    @property
    def features(self) -> tuple[NodeFeature, ...]:
        """"Return tuple with all supported feature types."""
        return self._features

    @property
    def node_info(self) -> NodeInfo:
        """"Return node information."""
        return self._node_info

    @property
    def humidity(self) -> float | None:
        """"Return humidity state."""
        if NodeFeature.HUMIDITY not in self._features:
            raise NodeError(
                f"Humidity state is not supported for node {self.mac}"
            )
        return self._humidity

    @property
    def last_update(self) -> datetime:
        """"Return timestamp of last update."""
        return self._last_update

    @property
    def loaded(self) -> bool:
        """Return load status."""
        return self._loaded

    @property
    def mac(self) -> str:
        """Return mac address of node."""
        return self._mac_in_str

    @property
    def motion(self) -> bool | None:
        """Motion detection value."""
        if NodeFeature.MOTION not in self._features:
            raise NodeError(
                f"Motion state is not supported for node {self.mac}"
            )
        return self._motion

    @property
    def motion_state(self) -> MotionState:
        """Motion detection state."""
        if NodeFeature.MOTION not in self._features:
            raise NodeError(
                f"Motion state is not supported for node {self.mac}"
            )
        return self._motion_state

    @property
    def ping(self) -> NetworkStatistics:
        """Ping statistics."""
        return self._ping

    @property
    def power(self) -> PowerStatistics:
        """Power statistics."""
        if NodeFeature.POWER not in self._features:
            raise NodeError(
                f"Power state is not supported for node {self.mac}"
            )
        return self._power

    @property
    def switch(self) -> bool | None:
        """Switch button value."""
        if NodeFeature.SWITCH not in self._features:
            raise NodeError(
                f"Switch value is not supported for node {self.mac}"
            )
        return self._switch

    @property
    def relay_state(self) -> RelayState:
        """State of relay."""
        if NodeFeature.RELAY not in self._features:
            raise NodeError(
                f"Relay state is not supported for node {self.mac}"
            )
        return self._relay_state

    @property
    def relay(self) -> bool:
        """Relay value."""
        if NodeFeature.RELAY not in self._features:
            raise NodeError(
                f"Relay value is not supported for node {self.mac}"
            )
        if self._relay is None:
            raise NodeError(f"Relay value is unknown for node {self.mac}")
        return self._relay

    @relay.setter
    def relay(self, state: bool) -> None:
        """Change relay to state value."""
        raise NotImplementedError()

    @property
    def temperature(self) -> float | None:
        """Temperature value."""
        if NodeFeature.TEMPERATURE not in self._features:
            raise NodeError(
                f"Temperature state is not supported for node {self.mac}"
            )
        return self._temperature

    @property
    def relay_init(
        self,
    ) -> bool | None:
        """Request the relay states at startup/power-up."""
        raise NotImplementedError()

    @relay_init.setter
    def relay_init(self, state: bool) -> None:
        """Request to configure relay states at startup/power-up."""
        raise NotImplementedError()

    def _setup_protocol(
        self,
        firmware: dict[datetime, SupportedVersions],
        node_features: tuple[NodeFeature],
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
        new_feature_list = list(self._features)
        for feature in node_features:
            if (
                required_version := FEATURE_SUPPORTED_AT_FIRMWARE.get(feature)
            ) is not None:
                if (
                    self._node_protocols.min
                    <= required_version
                    <= self._node_protocols.max
                    and feature not in new_feature_list
                ):
                    new_feature_list.append(feature)
        self._features = tuple(new_feature_list)
        self._node_info.features = self._features

    async def reconnect(self) -> None:
        """Reconnect node to Plugwise Zigbee network."""
        if await self.ping_update() is not None:
            self._connected = True

    async def disconnect(self) -> None:
        """Disconnect node from Plugwise Zigbee network."""
        self._connected = False
        if self._available:
            self._available = False
            await self.publish_event(NodeFeature.AVAILABLE, False)

    @property
    def energy_consumption_interval(self) -> int | None:
        """Interval (minutes) energy consumption counters are locally logged at Circle devices."""
        if NodeFeature.ENERGY not in self._features:
            raise NodeError(
                f"Energy log interval is not supported for node {self.mac}"
            )
        return self._energy_counters.consumption_interval

    @property
    def energy_production_interval(self) -> int | None:
        """Interval (minutes) energy production counters are locally logged at Circle devices."""
        if NodeFeature.ENERGY not in self._features:
            raise NodeError(
                f"Energy log interval is not supported for node {self.mac}"
            )
        return self._energy_counters.production_interval


    @property
    def maintenance_interval(self) -> int | None:
        """Maintenance interval (seconds) a battery powered node sends it heartbeat."""
        raise NotImplementedError()

    async def scan_calibrate_light(self) -> bool:
        """
        Request to calibration light sensitivity of Scan device.
        Returns True if successful.
        """
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
        """Load and activate node features."""
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
        if self._node_cache is None:
            _LOGGER.warning(
                "Unable to load node %s from cache because cache configuration is not loaded",
                self.mac,
            )
            return False
        return await self._node_cache.restore_cache()

    async def clear_cache(self) -> None:
        """Clear current cache."""
        if self._node_cache is not None:
            await self._node_cache.clear_cache()

    async def _load_from_cache(self) -> bool:
        """
        Load states from previous cached information.
        Return True if successful.
        """
        if self._loaded:
            return True
        if not await self._load_cache_file():
            _LOGGER.debug("Node %s failed to load cache file", self.mac)
            return False

        # Node Info
        if not await self._node_info_load_from_cache():
            _LOGGER.debug(
                "Node %s failed to load node_info from cache",
                self.mac
            )
            return False
        return True

    async def initialize(self) -> bool:
        """Initialize node."""
        raise NotImplementedError()

    async def _available_update_state(self, available: bool) -> None:
        """Update the node availability state."""
        if self._available == available:
            return
        if available:
            _LOGGER.info("Mark node %s to be available", self.mac)
            self._available = True
            await self.publish_feature_update_to_subscribers(
                NodeFeature.AVAILABLE, True
            )
            return
        _LOGGER.info("Mark node %s to be NOT available", self.mac)
        self._available = False
        await self.publish_feature_update_to_subscribers(
            NodeFeature.AVAILABLE, False
        )

    async def node_info_update(
        self, node_info: NodeInfoResponse | None = None
    ) -> bool:
        """Update Node hardware information."""
        if node_info is None:
            try:
                node_info = await self._send(
                    NodeInfoRequest(self._mac_in_bytes)
                )
            except PlugwiseException as e:
                _LOGGER.warning(
                    "%s for node_info_update() for %s",
                    str(e),
                    self.mac
                )
                await self._available_update_state(False)
                return False                
        
        if node_info is None:
            _LOGGER.debug(
                "No response for node_info_update() for %s",
                self.mac
            )
            _LOGGER.warning('Unavailable due to missing node_info_update')
            await self._available_update_state(False)
            return False
        self._last_update = datetime.utcnow()
        
        if node_info.mac_decoded != self.mac:
            raise NodeError(
                f"Incorrect node_info {node_info.mac_decoded} " +
                f"!= {self.mac}, id={node_info}"
            )

        await self._available_update_state(True)

        self._node_info_update_state(
            firmware=node_info.firmware,
            node_type=node_info.node_type,
            hardware=node_info.hardware,
            timestamp=node_info.timestamp,
        )
        return True

    async def _node_info_load_from_cache(self) -> bool:
        """Load node info settings from cache."""
        firmware: datetime | None = None
        node_type: NodeType | None = None
        hardware: str | None = self._get_cache("hardware")
        timestamp: datetime | None = None
        if (firmware_str := self._get_cache("firmware")) is not None:
            data = firmware_str.split("-")
            if len(data) == 6:
                firmware = datetime(
                    year=int(data[0]),
                    month=int(data[1]),
                    day=int(data[2]),
                    hour=int(data[3]),
                    minute=int(data[4]),
                    second=int(data[5]),
                    tzinfo=timezone.utc
                )
        if (node_type_str := self._get_cache("node_type")) is not None:
            node_type = NodeType(int(node_type_str))
        if (
            timestamp_str := self._get_cache("node_info_timestamp")
        ) is not None:
            data = timestamp_str.split("-")
            if len(data) == 6:
                timestamp = datetime(
                    year=int(data[0]),
                    month=int(data[1]),
                    day=int(data[2]),
                    hour=int(data[3]),
                    minute=int(data[4]),
                    second=int(data[5]),
                    tzinfo=timezone.utc
                )
        return self._node_info_update_state(
            firmware=firmware,
            hardware=hardware,
            node_type=node_type,
            timestamp=timestamp,
        )

    def _node_info_update_state(
        self,
        firmware: datetime | None,
        hardware: str | None,
        node_type: NodeType | None,
        timestamp: datetime | None,
    ) -> bool:
        """Process new node info and return true if all fields are updated."""
        complete = True
        if firmware is None:
            complete = False
        else:
            self._node_info.firmware = firmware
            self._set_cache("firmware", firmware)
        if hardware is None:
            complete = False
        else:
            if self._node_info.version != hardware:
                self._node_info.version = hardware
                # Generate modelname based on hardware version
                self._node_info.model = version_to_model(hardware)
                if self._node_info.model == "Unknown":
                    _LOGGER.warning(
                        "Failed to detect hardware model for %s based on '%s'",
                        self.mac,
                        hardware,
                    )
                if self._node_info.model is not None:
                    self._node_info.name = str(self._node_info.mac[-5:])
            self._set_cache("hardware", hardware)
        if timestamp is None:
            complete = False
        else:
            self._node_info.timestamp = timestamp
            self._set_cache("node_info_timestamp", timestamp)
        if node_type is None:
            complete = False
        else:
            self._node_info.type = NodeType(node_type)
            self._set_cache("node_type", self._node_info.type.value)
        if self._loaded and self._initialized:
            create_task(self.save_cache())
        return complete

    async def is_online(self) -> bool:
        """Check if node is currently online."""
        result = await self.ping_update()
        return result is not None

    async def ping_update(
        self, ping_response: NodePingResponse | None = None, retries: int = 1
    ) -> NetworkStatistics | None:
        """Update ping statistics."""
        if ping_response is None:
            try:
                ping_response: NodePingResponse | None = await self._send(
                    NodePingRequest(
                        self._mac_in_bytes, retries=1
                    )
                )
            except (StickError, NodeError) as e:
                _LOGGER.warning(
                    "%s for ping_update() for %s",
                    str(e),
                    self.mac
                )
                await self._available_update_state(False)
                return False
        if ping_response is None:
            await self._available_update_state(False)
            return False
        self._last_update = datetime.utcnow()
        await self._available_update_state(True)

        self._ping.timestamp = ping_response.timestamp
        self._ping.rssi_in = ping_response.rssi_in
        self._ping.rssi_out = ping_response.rssi_out
        self._ping.rtt = ping_response.rtt
        
        await self.publish_feature_update_to_subscribers(
            NodeFeature.PING, self._ping
        )
        return self._ping

    async def switch_relay(self, state: bool) -> bool | None:
        """Switch relay state."""
        raise NodeError(f"Relay control is not supported for node {self.mac}")

    @raise_not_loaded
    async def get_state(
        self, features: tuple[NodeFeature]
    ) -> dict[NodeFeature, Any]:
        """Update latest state for given feature."""
        states: dict[NodeFeature, Any] = {}
        for feature in features:
            if feature not in self._features:
                raise NodeError(
                    f"Update of feature '{feature.name}' is "
                    + f"not supported for {self.mac}"
                )
            if feature == NodeFeature.INFO:
                # Only request node info when information is > 5 minutes old
                if not self.skip_update(self._node_info, 300) or not self.available:
                    await self.node_info_update(None)
                states[NodeFeature.INFO] = self._node_info
            elif feature == NodeFeature.AVAILABLE:
                states[NodeFeature.AVAILABLE] = self.available
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
        raise NotImplementedError()

    def _get_cache(self, setting: str) -> str | None:
        """Retrieve value of specified setting from cache memory."""
        if not self._cache_enabled or self._node_cache is None:
            return None
        return self._node_cache.get_state(setting)

    def _set_cache(self, setting: str, value: Any) -> None:
        """Store setting with value in cache memory."""
        if not self._cache_enabled:
            return
        if self._node_cache is None:
            _LOGGER.warning(
                "Failed to update '%s' in cache because cache is not initialized yet",
                setting
            )
            return
        if isinstance(value, datetime):
            self._node_cache.add_state(
                setting,
                f"{value.year}-{value.month}-{value.day}-{value.hour}" +
                f"-{value.minute}-{value.second}"
            )
        elif isinstance(value, str):
            self._node_cache.add_state(setting, value)
        else:
            self._node_cache.add_state(setting, str(value))

    async def save_cache(self) -> None:
        """Save current cache to cache file."""
        if not self._cache_enabled:
            return
        if self._node_cache is None:
            _LOGGER.warning(
                "Failed to save cache to disk because cache is not initialized yet"
            )
            return
        _LOGGER.debug("Save cache file for node %s", self.mac)
        await self._node_cache.save_cache()

    @staticmethod
    def skip_update(data_class: Any, seconds: int) -> bool:
        """Check if update can be skipped when timestamp of given dataclass is less than given seconds old."""
        if data_class is None:
            return False
        if not hasattr(data_class, "timestamp"):
            return False
        if data_class.timestamp is None:
            return False
        if data_class.timestamp + timedelta(
            seconds=seconds
        ) > datetime.now(timezone.utc):
            return True
        return False
