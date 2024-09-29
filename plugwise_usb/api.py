"""Plugwise USB-Stick API."""

from collections.abc import Awaitable, Callable
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
    ENERGY = "energy"
    HUMIDITY = "humidity"
    INFO = "info"
    MOTION = "motion"
    PING = "ping"
    POWER = "power"
    RELAY = "relay"
    RELAY_INIT = "relay_init"
    SWITCH = "switch"
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
    NodeFeature.HUMIDITY,
    NodeFeature.MOTION,
    NodeFeature.TEMPERATURE,
    NodeFeature.SWITCH,
)


@dataclass
class BatteryConfig:
    """Battery related configuration settings."""

    # Duration in minutes the node synchronize its clock
    clock_interval: int | None = None

    # Enable/disable clock sync
    clock_sync: bool | None = None

    # Minimal interval in minutes the node will wake up
    # and able to receive (maintenance) commands
    maintenance_interval: int | None = None

    # Duration in seconds the SED will be awake for receiving commands
    stay_active: int | None = None

    #  Duration in minutes the SED will be in sleeping mode
    #  and not able to respond any command
    sleep_for: int | None = None


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


@dataclass
class RelayState:
    """Status of relay."""

    relay_state: bool | None = None
    timestamp: datetime | None = None


@dataclass
class MotionState:
    """Status of motion sensor."""

    motion: bool | None = None
    timestamp: datetime | None = None
    reset_timer: int | None = None
    daylight_mode: bool | None = None


@dataclass
class EnergyStatistics:
    """Energy statistics collection."""

    log_interval_consumption: int | None = None
    log_interval_production: int | None = None
    hour_consumption: float | None = None
    hour_consumption_reset: datetime | None = None
    day_consumption: float | None = None
    day_consumption_reset: datetime | None = None
    week_consumption: float | None = None
    week_consumption_reset: datetime | None = None
    hour_production: float | None = None
    hour_production_reset: datetime | None = None
    day_production: float | None = None
    day_production_reset: datetime | None = None
    week_production: float | None = None
    week_production_reset: datetime | None = None


class PlugwiseNode(Protocol):
    """Protocol definition of a Plugwise device node."""

    def __init__(
        self,
        mac: str,
        address: int,
        loaded_callback: Callable[[NodeEvent, str], Awaitable[None]],
    ) -> None:
        """Initialize plugwise node object."""

    # region Generic node details
    @property
    def features(self) -> tuple[NodeFeature, ...]:
        """Supported feature types of node."""

    @property
    def is_battery_powered(self) -> bool:
        """Indicate if node is power by battery."""

    @property
    def is_loaded(self) -> bool:
        """Indicate if node is loaded."""

    @property
    def last_update(self) -> datetime:
        """Timestamp of last update."""

    @property
    def name(self) -> str:
        """Return name of node."""

    @property
    def node_info(self) -> NodeInfo:
        """Node information."""

    async def load(self) -> bool:
        """Load configuration and activate node features."""

    async def update_node_details(
        self,
        firmware: datetime | None,
        hardware: str | None,
        node_type: NodeType | None,
        timestamp: datetime | None,
        relay_state: bool | None,
        logaddress_pointer: int | None,
    ) -> bool:
        """Update node information."""

    async def unload(self) -> None:
        """Load configuration and activate node features."""

    # endregion

    # region Network
    @property
    def available(self) -> bool:
        """Last known network availability state."""

    @property
    def mac(self) -> str:
        """Zigbee mac address."""

    @property
    def network_address(self) -> int:
        """Zigbee network registration address."""

    @property
    def ping_stats(self) -> NetworkStatistics:
        """Ping statistics."""

    async def is_online(self) -> bool:
        """Check network status."""

    def update_ping_stats(
        self, timestamp: datetime, rssi_in: int, rssi_out: int, rtt: int
    ) -> None:
        """Update ping statistics."""

    # TODO: Move to node with subscription to stick event
    async def reconnect(self) -> None:
        """Reconnect node to Plugwise Zigbee network."""

    # TODO: Move to node with subscription to stick event
    async def disconnect(self) -> None:
        """Disconnect from Plugwise Zigbee network."""

    # endregion

    # region cache

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

    # region sensors
    @property
    def energy(self) -> EnergyStatistics | None:
        """Energy statistics.

        Raises NodeError when energy feature is not present at device.
        """

    @property
    def humidity(self) -> float | None:
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
    def relay_state(self) -> RelayState:
        """Last known relay state information.

        Raises NodeError when relay feature is not present at device.
        """

    @property
    def switch(self) -> bool | None:
        """Current state of the switch.

        Raises NodeError when switch feature is not present at device.
        """

    @property
    def temperature(self) -> float | None:
        """Last received temperature state.

        Raises NodeError when temperature feature is not present at device.
        """

    async def get_state(self, features: tuple[NodeFeature]) -> dict[NodeFeature, Any]:
        """Request an updated state for given feature.

        Returns the state or statistics for each requested feature.
        """

    # endregion

    # region control & configure
    @property
    def battery_config(self) -> BatteryConfig:
        """Battery configuration settings.

        Raises NodeError when battery configuration feature is not present at device.
        """

    @property
    def relay_init(self) -> bool | None:
        """Configured state at which the relay must be at initial power-up of device.

        Raises NodeError when relay configuration feature is not present at device.
        """

    async def switch_relay(self, state: bool) -> bool | None:
        """Change the state of the relay and return the new state of relay.

        Raises NodeError when relay feature is not present at device.
        """

    async def switch_relay_init_off(self, state: bool) -> bool | None:
        """Change the state of initial (power-up) state of the relay and return the new configured setting.

        Raises NodeError when the initial (power-up) relay configure feature is not present at device.
        """

    @property
    def energy_consumption_interval(self) -> int | None: ...  # noqa: D102

    @property
    def energy_production_interval(self) -> int | None: ...  # noqa: D102

    @property
    def maintenance_interval(self) -> int | None: ...  # noqa: D102

    @property
    def motion_reset_timer(self) -> int: ...  # noqa: D102

    @property
    def daylight_mode(self) -> bool: ...  # noqa: D102

    @property
    def sensitivity_level(self) -> MotionSensitivity: ...  # noqa: D102

    async def configure_motion_reset(self, delay: int) -> bool: ...  # noqa: D102

    async def scan_calibrate_light(self) -> bool: ...  # noqa: D102

    async def scan_configure(  # noqa: D102
        self,
        motion_reset_timer: int,
        sensitivity_level: MotionSensitivity,
        daylight_mode: bool,
    ) -> bool: ...

    # endregion
