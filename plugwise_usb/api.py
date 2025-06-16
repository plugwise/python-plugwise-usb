"""Plugwise USB-Stick API."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
import logging
from typing import Any, Protocol

_LOGGER = logging.getLogger(__name__)


class StickEvent(Enum):
    """Plugwise USB Stick events for callback subscription."""

    CONNECTED = auto()
    DISCONNECTED = auto()
    MESSAGE_RECEIVED = auto()
    NETWORK_OFFLINE = auto()
    NETWORK_ONLINE = auto()


class MotionSensitivity(Enum):
    """Motion sensitivity levels for Scan devices."""

    HIGH = auto()
    MEDIUM = auto()
    OFF = auto()


class NodeEvent(Enum):
    """Plugwise Node events for callback subscription."""

    AWAKE = auto()
    DISCOVERED = auto()
    LOADED = auto()
    JOIN = auto()


class NodeFeature(str, Enum):
    """USB Stick Node feature."""

    AVAILABLE = "available"
    BATTERY = "battery"
    CIRCLE = "circle"
    CIRCLEPLUS = "circleplus"
    ENERGY = "energy"
    HUMIDITY = "humidity"
    INFO = "info"
    MOTION = "motion"
    MOTION_CONFIG = "motion_config"
    PING = "ping"
    POWER = "power"
    RELAY = "relay"
    RELAY_INIT = "relay_init"
    RELAY_LOCK = "relay_lock"
    SWITCH = "switch"
    SENSE = "sense"
    TEMPERATURE = "temperature"


class NodeType(Enum):
    """USB Node types."""

    STICK = 0
    CIRCLE_PLUS = 1  # AME_NC
    CIRCLE = 2  # AME_NR
    SWITCH = 3  # AME_SEDSwitch
    SENSE = 5  # AME_SEDSense
    SCAN = 6  # AME_SEDScan
    CELSIUS_SED = 7  # AME_CelsiusSED
    CELSIUS_NR = 8  # AME_CelsiusNR
    STEALTH = 9  # AME_STEALTH_ZE


# 10 AME_MSPBOOTLOAD
# 11 AME_STAR


PUSHING_FEATURES = (
    NodeFeature.AVAILABLE,
    NodeFeature.BATTERY,
    NodeFeature.CIRCLE,
    NodeFeature.CIRCLEPLUS,
    NodeFeature.HUMIDITY,
    NodeFeature.MOTION,
    NodeFeature.MOTION_CONFIG,
    NodeFeature.TEMPERATURE,
    NodeFeature.SENSE,
    NodeFeature.SWITCH,
)


@dataclass(frozen=True)
class AvailableState:
    """Availability of node.

    Description: Availability of node on Zigbee network.

    Attributes:
        state: bool: Indicate if node is operational (True) or off-line (False). Battery powered nodes which are in sleeping mode report to be operational.
        last_seen: datetime: Last time a messages was received from the Node.

    """

    state: bool
    last_seen: datetime


@dataclass(frozen=True)
class BatteryConfig:
    """Battery related configuration settings.

    Description: Configuration settings for battery powered devices.

    Attributes:
        awake_duration: int | None: Duration in seconds a battery powered devices is awake to accept (configuration) messages.
        clock_interval: int | None: Interval in minutes a battery powered devices is synchronizing its clock.
        clock_sync: bool | None: Indicate if the internal clock must be synced.
        maintenance_interval: int | None: Interval in minutes a battery powered devices is awake for maintenance purposes.
        sleep_duration: int | None: Interval in minutes a battery powered devices is sleeping.

    """

    awake_duration: int | None = None
    clock_interval: int | None = None
    clock_sync: bool | None = None
    maintenance_interval: int | None = None
    sleep_duration: int | None = None


@dataclass
class NodeInfoMessage:
    """Node hardware information Message."""

    firmware: datetime | None = None
    hardware: str | None = None
    node_type: NodeType | None = None
    timestamp: datetime | None = None
    relay_state: bool | None = None
    current_logaddress_pointer: int | None = None


@dataclass
class NodeInfo:
    """Node hardware information."""

    mac: str
    zigbee_address: int
    is_battery_powered: bool = False
    features: tuple[NodeFeature, ...] = (NodeFeature.INFO,)
    firmware: datetime | None = None
    name: str | None = None
    model: str | None = None
    model_type: str | None = None
    node_type: NodeType | None = None
    timestamp: datetime | None = None
    version: str | None = None


@dataclass
class NetworkStatistics:
    """Zigbee network information."""

    timestamp: datetime | None = None
    rssi_in: int | None = None
    rssi_out: int | None = None
    rtt: int | None = None


@dataclass
class PowerStatistics:
    """Power statistics collection."""

    last_second: float | None = None
    last_8_seconds: float | None = None
    timestamp: datetime | None = None


@dataclass(frozen=True)
class RelayConfig:
    """Configuration of relay.

    Description: Configuration settings for relay.

    Attributes:
        init_state: bool | None: Configured state at which the relay must be at initial power-up of device.

    """

    init_state: bool | None = None


@dataclass(frozen=True)
class RelayLock:
    """Status of relay lock."""

    state: bool | None = None


@dataclass(frozen=True)
class RelayState:
    """Status of relay."""

    state: bool | None = None
    timestamp: datetime | None = None


@dataclass(frozen=True)
class MotionState:
    """Status of motion sensor."""

    state: bool | None = None
    timestamp: datetime | None = None


@dataclass(frozen=True)
class MotionConfig:
    """Configuration of motion sensor.

    Description: Configuration settings for motion detection.
                 When value is scheduled to be changed the returned value is the optimistic value

    Attributes:
        reset_timer: int | None: Motion reset timer in minutes before the motion detection is switched off.
        daylight_mode: bool | None: Motion detection when light level is below threshold.
        sensitivity_level: MotionSensitivity | None: Motion sensitivity level.

    """

    daylight_mode: bool | None = None
    reset_timer: int | None = None
    sensitivity_level: MotionSensitivity | None = None


@dataclass
class EnergyStatistics:
    """Energy statistics collection."""

    log_interval_consumption: int | None = None
    log_interval_production: int | None = None
    hour_consumption: float | None = None
    hour_consumption_reset: datetime | None = None
    day_consumption: float | None = None
    day_consumption_reset: datetime | None = None
    hour_production: float | None = None
    hour_production_reset: datetime | None = None
    day_production: float | None = None
    day_production_reset: datetime | None = None


@dataclass
class SenseStatistics:
    """Sense statistics collection."""

    temperature: float | None = None
    humidity: float | None = None


class PlugwiseNode(Protocol):
    """Protocol definition of a Plugwise device node."""

    # region Generic node properties
    @property
    def features(self) -> tuple[NodeFeature, ...]:
        """Supported feature types of node."""

    @property
    def is_battery_powered(self) -> bool:
        """Indicate if node is powered by battery."""

    @property
    def is_loaded(self) -> bool:
        """Indicate if node is loaded and available to interact."""

    @property
    def name(self) -> str:
        """Return name of node."""

    @property
    def node_info(self) -> NodeInfo:
        """Return NodeInfo class with all node information."""

    # endregion
    async def load(self) -> bool:
        """Load configuration and activate node features."""

    async def unload(self) -> None:
        """Unload and deactivate node."""

    # region Network properties
    @property
    def available(self) -> bool:
        """Last known network availability state."""

    @property
    def available_state(self) -> AvailableState:
        """Network availability state."""

    @property
    def last_seen(self) -> datetime:
        """Timestamp of last network activity."""

    @property
    def mac(self) -> str:
        """Zigbee mac address."""

    @property
    def network_address(self) -> int:
        """Zigbee network registration address."""

    @property
    def ping_stats(self) -> NetworkStatistics:
        """Ping statistics for node."""

    # endregion

    async def is_online(self) -> bool:
        """Check network status of node."""

    # TODO: Move to node with subscription to stick event
    async def reconnect(self) -> None:
        """Reconnect node to Plugwise Zigbee network."""

    # TODO: Move to node with subscription to stick event
    async def disconnect(self) -> None:
        """Disconnect from Plugwise Zigbee network."""

    # region Cache settings
    @property
    def cache_folder(self) -> str:
        """Path to cache folder."""

    @cache_folder.setter
    def cache_folder(self, cache_folder: str) -> None:
        """Path to cache folder."""

    @property
    def cache_folder_create(self) -> bool:
        """Create cache folder when it does not exists."""

    @cache_folder_create.setter
    def cache_folder_create(self, enable: bool = True) -> None:
        """Create cache folder when it does not exists."""

    @property
    def cache_enabled(self) -> bool:
        """Activate caching of retrieved information."""

    @cache_enabled.setter
    def cache_enabled(self, enable: bool) -> None:
        """Activate caching of retrieved information."""

    async def clear_cache(self) -> None:
        """Clear currently cached information."""

    async def save_cache(
        self, trigger_only: bool = True, full_write: bool = False
    ) -> None:
        """Write currently cached information to cache file."""

    # endregion

    # region Sensors
    @property
    def energy(self) -> EnergyStatistics:
        """Energy statistics.

        Raises NodeError when energy feature is not present at device.
        """

    @property
    def humidity(self) -> float:
        """Last received humidity state.

        Raises NodeError when humidity feature is not present at device.
        """

    @property
    def motion(self) -> bool | None:
        """Current state of motion detection.

        Raises NodeError when motion feature is not present at device.
        """

    @property
    def motion_state(self) -> MotionState:
        """Last known motion state information.

        Raises NodeError when motion feature is not present at device.
        """

    @property
    def power(self) -> PowerStatistics:
        """Current power statistics.

        Raises NodeError when power feature is not present at device.
        """

    @property
    def relay(self) -> bool:
        """Current state of relay.

        Raises NodeError when relay feature is not present at device.
        """

    @property
    def relay_lock(self) -> RelayLock:
        """Last known relay lock state information.

        Raises NodeError when relay lock feature is not present at device.
        """

    @property
    def relay_state(self) -> RelayState:
        """Last known relay state information.

        Raises NodeError when relay feature is not present at device.
        """

    @property
    def switch(self) -> bool:
        """Current state of the switch.

        Raises NodeError when switch feature is not present at device.
        """

    @property
    def temperature(self) -> float:
        """Last received temperature state.

        Raises NodeError when temperature feature is not present at device.
        """

    # endregion

    # region control
    async def get_state(self, features: tuple[NodeFeature]) -> dict[NodeFeature, Any]:
        """Request an updated state for given feature.

        Returns the state or statistics for each requested feature.
        """

    # endregion

    # region Actions to execute
    async def set_relay(self, state: bool) -> bool:
        """Change the state of the relay.

        Description:
            Configures the state of the relay.

        Args:
            state: Boolean indicating the required state of the relay (True = ON, False = OFF)

        Returns:
            Boolean: with the newly set state of the relay

        Raises:
            FeatureError: When the relay feature is not present at device.
            NodeError: When the node is not yet loaded or setting the state failed.

        """

    async def set_relay_lock(self, state: bool) -> bool:
        """Change the state of the relay-lock."""

    # endregion

    # region configuration properties

    @property
    def battery_config(self) -> BatteryConfig:
        """Battery configuration settings.

        Returns:
            BatteryConfig: Currently configured battery settings.
                           When settings are scheduled to be changed it will return the new settings.

        Raises:
            FeatureError: When this configuration feature is not present at device.
            NodeError: When the node is not yet loaded or configuration failed.

        """

    @property
    def motion_config(self) -> MotionConfig:
        """Motion configuration settings.

        Returns:
            MotionConfig: with the current motion configuration settings.

        Raises:
            FeatureError: When this configuration feature is not present at device.
            NodeError: When the node is not yet loaded or configuration failed.

        """

    @property
    def relay_config(self) -> RelayConfig:
        """Relay configuration settings.

        Returns:
            RelayConfig: Current relay configuration settings.

        Raises:
            FeatureError: When this configuration feature is not present at device.
            NodeError: When the node is not yet loaded or configuration failed.

        """

    # endregion

    # region Configuration actions
    async def set_awake_duration(self, seconds: int) -> bool:
        """Change the awake duration.

        Description:
            Configure the duration for a battery powered device (Sleeping Endpoint Device) to be awake.
            The configuration will be set the next time the device is awake for maintenance purposes.

            Use the 'is_battery_powered' property to determine if the device is battery powered.

        Args:
            seconds: Number of seconds between each time the device must wake-up for maintenance purposes
                      Minimum value: 1
                      Maximum value: 255

        Returns:
            Boolean: True when the configuration is successfully scheduled to be changed. False when
                     the configuration is already set.

        Raises:
            FeatureError: When this configuration feature is not present at device.
            NodeError: When the node is not yet loaded or configuration failed.
            ValueError: When the seconds value is out of range.

        """

    async def set_clock_interval(self, minutes: int) -> bool:
        """Change the clock interval.

        Description:
            Configure the duration for a battery powered device (Sleeping Endpoint Device) to synchronize the internal clock.
            Use the 'is_battery_powered' property to determine if the device is battery powered.

        Args:
            minutes: Number of minutes between each time the device must synchronize the clock
                      Minimum value: 1
                      Maximum value: 65535

        Returns:
            Boolean: True when the configuration is successfully scheduled to be changed. False when
                     the configuration is already set.

        Raises:
            FeatureError: When this configuration feature is not present at device.
            NodeError: When the node is not yet loaded or configuration failed.
            ValueError: When the minutes value is out of range.

        """

    async def set_clock_sync(self, sync: bool) -> bool:
        """Change the clock synchronization setting.

        Description:
            Configure the duration for a battery powered device (Sleeping Endpoint Device) to synchronize the internal clock.
            Use the 'is_battery_powered' property to determine if the device is battery powered.

        Args:
            sync: Boolean indicating the internal clock must be synced (True = sync enabled, False = sync disabled)

        Returns:
            Boolean: True when the configuration is successfully scheduled to be changed. False when
                     the configuration is already set.

        Raises:
            FeatureError: When this configuration feature is not present at device.
            NodeError: When the node is not yet loaded or configuration failed.

        """

    async def set_maintenance_interval(self, minutes: int) -> bool:
        """Change the maintenance interval.

        Description:
            Configure the maintenance interval for a battery powered device (Sleeping Endpoint Device).
            The configuration will be set the next time the device is awake for maintenance purposes.

            Use the 'is_battery_powered' property to determine if the device is battery powered.

        Args:
            minutes: Number of minutes between each time the device must wake-up for maintenance purposes
                      Minimum value: 1
                      Maximum value: 1440

        Returns:
            Boolean: True when the configuration is successfully scheduled to be changed. False when
                     the configuration is already set.

        Raises:
            FeatureError: When this configuration feature is not present at device.
            NodeError: When the node is not yet loaded or configuration failed.
            ValueError: When the seconds value is out of range.

        """

    async def set_motion_daylight_mode(self, state: bool) -> bool:
        """Configure motion daylight mode.

        Description:
            Configure if motion must be detected when light level is below threshold.

        Args:
            state: Boolean indicating the required state (True = ON, False = OFF)

        Returns:
            Boolean: with the newly configured state of the daylight mode

        Raises:
            FeatureError: When the daylight mode feature is not present at device.
            NodeError: When the node is not yet loaded or configuration failed.

        """

    async def set_motion_reset_timer(self, minutes: int) -> bool:
        """Configure the motion reset timer in minutes.

        Description:
            Configure the duration in minutes a Scan device must not detect motion before reporting no motion.
            The configuration will be set the next time the device is awake for maintenance purposes.

            Use the 'is_battery_powered' property to determine if the device is battery powered.

        Args:
            minutes: Number of minutes before the motion detection is switched off
                      Minimum value: 1
                      Maximum value: 255

        Returns:
            Boolean: True when the configuration is successfully scheduled to be changed. False when
                     the configuration is already set.

        Raises:
            FeatureError: When this configuration feature is not present at device.
            NodeError: When the node is not yet loaded or configuration failed.
            ValueError: When the seconds value is out of range.

        """

    async def set_motion_sensitivity_level(self, level: MotionSensitivity) -> bool:
        """Configure motion sensitivity level.

        Description:
            Configure the sensitivity level of motion detection.

        Args:
            level: MotionSensitivity indicating the required sensitivity level

        Returns:
            Boolean: True when the configuration is successfully scheduled to be changed. False when
                     the configuration is already set.

        Raises:
            FeatureError: When the motion sensitivity feature is not present at device.
            NodeError: When the node is not yet loaded or configuration failed.

        """

    async def set_relay_init(self, state: bool) -> bool:
        """Change the initial state of the relay.

        Description:
            Configures the state of the relay to be directly after power-up of the device.

        Args:
            state: Boolean indicating the required state of the relay (True = ON, False = OFF)

        Returns:
            Boolean: with the newly configured state of the relay

        Raises:
            FeatureError: When the initial (power-up) relay configure feature is not present at device.
            NodeError: When the node is not yet loaded or configuration failed.

        """

    async def set_sleep_duration(self, minutes: int) -> bool:
        """Change the sleep duration.

        Description:
            Configure the duration for a battery powered device (Sleeping Endpoint Device) to sleep.
            Use the 'is_battery_powered' property to determine if the device is battery powered.

        Args:
            minutes: Number of minutes to sleep
                      Minimum value: 1
                      Maximum value: 65535

        Returns:
            Boolean: True when the configuration is successfully scheduled to be changed. False when
                     the configuration is already set.

        Raises:
            FeatureError: When this configuration feature is not present at device.
            NodeError: When the node is not yet loaded or configuration failed.
            ValueError: When the minutes value is out of range.

        """

    # endregion

    # region Helper functions
    async def message_for_node(self, message: Any) -> None:
        """Process message for node.

        Description: Submit a plugwise message for this node.

        Args:
            message: Plugwise message to process.

        """

    # endregion
