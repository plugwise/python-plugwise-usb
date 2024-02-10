"""Plugwise USB-Stick API."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto


class StickEvent(Enum):
    """Plugwise USB Stick events for callback subscription."""

    CONNECTED = auto()
    DISCONNECTED = auto()
    MESSAGE_RECEIVED = auto()
    NETWORK_OFFLINE = auto()
    NETWORK_ONLINE = auto()


class NodeEvent(Enum):
    """Plugwise Node events for callback subscription."""

    AWAKE = auto()
    DISCOVERED = auto()
    LOADED = auto()
    JOIN = auto()


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


class NodeFeature(str, Enum):
    """USB Stick Node feature."""

    AVAILABLE = "available"
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


PUSHING_FEATURES = (
    NodeFeature.HUMIDITY,
    NodeFeature.MOTION,
    NodeFeature.TEMPERATURE,
    NodeFeature.SWITCH
)


@dataclass
class NodeInfo:
    """Node hardware information."""

    mac: str
    zigbee_address: int
    battery_powered: bool = False
    features: tuple[NodeFeature, ...] = (NodeFeature.INFO,)
    firmware: datetime | None = None
    name: str | None = None
    model: str | None = None
    type: NodeType | None = None
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
