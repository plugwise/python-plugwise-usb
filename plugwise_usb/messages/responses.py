"""All known response messages to be received from plugwise devices."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Final

from ..api import NodeType
from ..constants import MESSAGE_FOOTER, MESSAGE_HEADER
from ..exceptions import MessageError
from . import PlugwiseMessage, Priority
from .properties import (
    Bytes,
    DateTime,
    Float,
    Int,
    LogAddr,
    RealClockDate,
    RealClockTime,
    String,
    Time,
    UnixTimestamp,
)

NODE_JOIN_ID: Final = b"0006"
NODE_AWAKE_RESPONSE_ID: Final = b"004F"
NODE_REJOIN_ID: Final = b"0061"
NODE_SWITCH_GROUP_ID: Final = b"0056"
SENSE_REPORT_ID: Final = b"0105"

JOIN_AVAILABLE_SEQ_ID: Final = b"FFFC"
REJOIN_RESPONSE_SEQ_ID: Final = b"FFFD"
AWAKE_RESPONSE_SEQ_ID: Final = b"FFFE"
SWITCH_GROUP_RESPONSE_SEQ_ID: Final = b"FFFF"

BROADCAST_IDS: Final = (
    JOIN_AVAILABLE_SEQ_ID,
    REJOIN_RESPONSE_SEQ_ID,
    AWAKE_RESPONSE_SEQ_ID,
    SWITCH_GROUP_RESPONSE_SEQ_ID,
)


class StickResponseType(bytes, Enum):
    """Response message types for stick."""

    # Minimal value = b"00C0", maximum value = b"00F3"
    # Below the currently known values:

    ACCEPT = b"00C1"
    FAILED = b"00C2"
    TIMEOUT = b"00E1"


class NodeResponseType(bytes, Enum):
    """Response types of a 'NodeResponse' reply message."""

    CIRCLE_PLUS = b"00DD"  # ack for CirclePlusAllowJoiningRequest with state false
    CLOCK_ACCEPTED = b"00D7"
    JOIN_ACCEPTED = b"00D9"  # ack for CirclePlusAllowJoiningRequest with state true
    POWER_LOG_INTERVAL_ACCEPTED = b"00F8"  # ack for CircleMeasureIntervalRequest
    RELAY_SWITCHED_OFF = b"00DE"
    RELAY_SWITCHED_ON = b"00D8"
    RELAY_SWITCH_FAILED = b"00E2"
    SED_CONFIG_ACCEPTED = b"00F6"
    REAL_TIME_CLOCK_ACCEPTED = b"00DF"
    REAL_TIME_CLOCK_FAILED = b"00E7"

    # TODO: Validate these response types
    SED_CONFIG_FAILED = b"00F7"
    POWER_CALIBRATION_ACCEPTED = b"00DA"


class NodeAckResponseType(bytes, Enum):
    """Response types of a 'NodeAckResponse' reply message."""

    SCAN_CONFIG_ACCEPTED = b"00BE"
    SCAN_CONFIG_FAILED = b"00BF"
    SCAN_LIGHT_CALIBRATION_ACCEPTED = b"00BD"
    SENSE_INTERVAL_ACCEPTED = b"00B3"
    SENSE_INTERVAL_FAILED = b"00B4"
    SENSE_BOUNDARIES_ACCEPTED = b"00B5"
    SENSE_BOUNDARIES_FAILED = b"00B6"


class NodeAwakeResponseType(int, Enum):
    """Response types of a 'NodeAwakeResponse' reply message."""

    MAINTENANCE = 0  # SED awake for maintenance
    FIRST = 1  # SED awake for the first time
    STARTUP = 2  # SED awake after restart, e.g. after reinserting a battery
    STATE = 3  # SED awake to report state (Motion / Temperature / Humidity
    UNKNOWN = 4
    BUTTON = 5  # SED awake due to button press


class PlugwiseResponse(PlugwiseMessage):
    """Base class for response messages received by USB-Stick."""

    def __init__(
        self,
        identifier: bytes,
        decode_ack: bool = False,
        decode_mac: bool = True,
    ) -> None:
        """Initialize a response message."""
        super().__init__()
        self._identifier = identifier
        self._mac: bytes | None = None
        self._ack_id: bytes | None = None
        self._decode_ack = decode_ack
        self._decode_mac = decode_mac
        self._params: list[Any] = []
        self._seq_id: bytes = b"FFFF"
        self._retries = 0

    def __repr__(self) -> str:
        """Convert request into writable str."""
        return f"{self.__class__.__name__} (mac={self.mac_decoded}, seq_id={self._seq_id!r}, retries={self._retries})"

    @property
    def retries(self) -> int:
        """Number of retries for processing."""
        return self._retries

    @retries.setter
    def retries(self, retries: int) -> None:
        """Set number of retries for processing."""
        self._retries = retries

    @property
    def ack_id(self) -> bytes | None:
        """Return the acknowledge id."""
        return self._ack_id

    def deserialize(self, response: bytes, has_footer: bool = True) -> None:
        """Deserialize bytes to actual message properties."""
        # Header
        if response[:4] != MESSAGE_HEADER:
            raise MessageError(
                "Invalid message header"
                + str({response[:4]})
                + " for "
                + self.__class__.__name__
            )
        response = response[4:]

        # Footer
        if has_footer:
            if response[-2:] != MESSAGE_FOOTER:
                raise MessageError(
                    "Invalid message footer "
                    + str(response[-2:])
                    + " for "
                    + self.__class__.__name__
                )
            response = response[:-2]

        # Checksum
        if (check := self.calculate_checksum(response[:-4])) != response[-4:]:
            raise MessageError(
                f"Invalid checksum for {self.__class__.__name__}, "
                + f"expected {check!r} got "
                + str(response[-4:]),
            )
        response = response[:-4]

        # ID and Sequence number
        if self._identifier != response[:4]:
            raise MessageError(
                "Invalid message identifier received "
                + f"expected {self._identifier!r} "
                + f"got {response[:4]!r}"
            )
        self._seq_id = response[4:8]
        response = response[8:]

        # Message data
        if len(response) != len(self):
            raise MessageError(
                "Invalid message length received for "
                + f"{self.__class__.__name__}, expected "
                + f"{len(self)} bytes got {len(response)}"
            )
        if self._decode_ack:
            self._ack_id = response[:4]
            response = response[4:]
        if self._decode_mac:
            self._mac = response[:16]
            response = response[16:]
        if len(response) > 0:
            try:
                response = self._parse_params(response)
            except ValueError as ve:
                raise MessageError(
                    "Failed to parse data "
                    + str(response)
                    + "for message "
                    + self.__class__.__name__
                ) from ve

    def _parse_params(self, response: bytes) -> bytes:
        for param in self._params:
            my_val = response[: len(param)]
            param.deserialize(my_val)
            response = response[len(my_val) :]

        return response

    def __len__(self) -> int:
        """Return the size of response message."""
        offset_ack = 4 if self._decode_ack else 0
        offset_mac = 16 if self._decode_mac else 0
        return offset_ack + offset_mac + sum(len(x) for x in self._params)


class StickResponse(PlugwiseResponse):
    """Response message from USB-Stick.

    Response to: Any message request
    """

    def __init__(self) -> None:
        """Initialize StickResponse message object."""
        super().__init__(b"0000", decode_ack=True, decode_mac=False)
        self.priority = Priority.HIGH

    def __repr__(self) -> str:
        """Convert request into writable str."""
        if self.ack_id is None:
            return f"StickResponse (seq_id={self._seq_id!r}, retries={self._retries}, ack=UNKNOWN)"
        return f"StickResponse (seq_id={self._seq_id!r}, retries={self._retries}, ack={StickResponseType(self.ack_id).name})"

    @property
    def response_type(self) -> StickResponseType:
        """Return acknowledge response type."""
        if self.ack_id is None:
            raise MessageError("Acknowledge ID is unknown")
        return StickResponseType(self.ack_id)


class NodeResponse(PlugwiseResponse):
    """Report status from node to a specific request.

    Supported protocols : 1.0, 2.0
    Response to requests: TODO: complete list
                          CircleClockSetRequest
                          CirclePlusRealTimeClockSetRequest
                          CircleRelaySwitchRequest
    """

    def __init__(self) -> None:
        """Initialize NodeResponse message object."""
        super().__init__(b"0000", decode_ack=True)

    def __repr__(self) -> str:
        """Convert request into writable str."""
        if self.ack_id is None:
            return f"{super().__repr__()[:-1]}, ack=UNKNOWN)"
        return (
            f"{super().__repr__()[:-1]}, ack={str(NodeResponseType(self.ack_id).name)})"
        )

    @property
    def response_type(self) -> NodeResponseType:
        """Return acknowledge response type."""
        if self.ack_id is None:
            raise MessageError("Acknowledge ID is unknown")
        return NodeResponseType(self.ack_id)


class StickNetworkInfoResponse(PlugwiseResponse):
    """Report status of zigbee network.

    Supported protocols : 1.0, 2.0
    Response to request : NodeNetworkInfoRequest
    """

    def __init__(self) -> None:
        """Initialize NodeNetworkInfoResponse message object."""
        super().__init__(b"0002")
        self._channel = Int(0, length=2)
        self._source_mac_id = String(None, length=16)
        self.extended_pan_id = String(None, length=16)
        self.unique_network_id = String(None, length=16)
        self._new_node_mac_id = String(None, length=16)
        self.pan_id = String(None, length=4)
        self.idx = Int(0, length=2)
        self._params += [
            self._channel,
            self._source_mac_id,
            self.extended_pan_id,
            self.unique_network_id,
            self._new_node_mac_id,
            self.pan_id,
            self.idx,
        ]

    @property
    def channel(self) -> int:
        """Return zigbee channel."""
        return self._channel.value

    @property
    def new_node_mac_id(self) -> str:
        """New node mac_id."""
        # Clear first two characters of mac ID, as they contain
        # part of the short PAN-ID
        return "00" + self._new_node_mac_id.value[2:]


class NodeSpecificResponse(PlugwiseResponse):
    """TODO: Report some sort of status from node.

    PWAckReplyV1_0
    <argument name="code" length="2"/>

    Supported protocols : 1.0, 2.0
    Response to requests: Unknown: TODO
    """

    def __init__(self) -> None:
        """Initialize NodeSpecificResponse message object."""
        super().__init__(b"0003")
        self.status = Int(0, 4)
        self._params += [self.status]


class CirclePlusConnectResponse(PlugwiseResponse):
    """CirclePlus connected to the network.

    Supported protocols : 1.0, 2.0
    Response to request : CirclePlusConnectRequest
    """

    def __init__(self) -> None:
        """Initialize CirclePlusConnectResponse message object."""
        super().__init__(b"0005")
        self.existing = Int(0, 2)
        self.allowed = Int(0, 2)
        self._params += [self.existing, self.allowed]


class NodeJoinAvailableResponse(PlugwiseResponse):
    """Request from Node to join a plugwise network.

    Supported protocols : 1.0, 2.0
    Response to request : No request as every unjoined node is requesting
    to be added automatically
    """

    def __init__(self) -> None:
        """Initialize NodeJoinAvailableResponse message object."""
        super().__init__(NODE_JOIN_ID)


class NodePingResponse(PlugwiseResponse):
    """Ping and RSSI (Received Signal Strength Indicator) response from node.

    - rssi_in : Incoming last hop RSSI target
    - rssi_out : Last hop RSSI source
    - time difference in ms

    Supported protocols : 1.0, 2.0
    Response to request : NodePingRequest
    """

    def __init__(self) -> None:
        """Initialize NodePingResponse message object."""
        super().__init__(b"000E")
        self._rssi_in = Int(0, length=2)
        self._rssi_out = Int(0, length=2)
        self._rtt = Int(0, 4, False)
        self._params += [
            self._rssi_in,
            self._rssi_out,
            self._rtt,
        ]

    @property
    def rssi_in(self) -> int:
        """Return inbound RSSI level."""
        return self._rssi_in.value

    @property
    def rssi_out(self) -> int:
        """Return outbound RSSI level."""
        return self._rssi_out.value

    @property
    def rtt(self) -> int:
        """Return round trip time."""
        return self._rtt.value


class NodeImageValidationResponse(PlugwiseResponse):
    """TODO: Some kind of response to validate a firmware image for a node.

    Supported protocols : 1.0, 2.0
    Response to request : NodeImageValidationRequest
    """

    def __init__(self, timestamp: datetime | None = None) -> None:
        """Initialize NodeImageValidationResponse message object."""
        super().__init__(b"0010")
        self.image_timestamp = UnixTimestamp(timestamp)
        self._params += [self.image_timestamp]


class StickInitResponse(PlugwiseResponse):
    """Returns the configuration and status of the USB-Stick.

    Optional:
    - circle_plus_mac
    - network_id
    - TODO: Two unknown parameters

    Supported protocols : 1.0, 2.0
    Response to request : StickInitRequest
    """

    def __init__(self) -> None:
        """Initialize StickInitResponse message object."""
        super().__init__(b"0011")
        self._unknown1 = Int(0, length=2)
        self._network_online = Int(0, length=2)
        self._mac_nc = String(None, length=16)
        self._network_id = Int(0, 4, False)
        self._unknown2 = Int(0, length=2)
        self._params += [
            self._unknown1,
            self._network_online,
            self._mac_nc,
            self._network_id,
            self._unknown2,
        ]

    @property
    def mac_network_controller(self) -> str:
        """Return the mac of the network controller (Circle+)."""
        # Replace first 2 characters by 00 for mac of circle+ node
        return "00" + self._mac_nc.value[2:]

    @property
    def network_id(self) -> int:
        """Return network ID."""
        return self._network_id.value

    @property
    def network_online(self) -> bool:
        """Return state of network."""
        return self._network_online.value == 1

    def __repr__(self) -> str:
        """Convert request into writable str."""
        return f"{super().__repr__()[:-1]}, network_controller={self.mac_network_controller}, network_online={self.network_online})"


class CirclePowerUsageResponse(PlugwiseResponse):
    """Returns power usage as impulse counters for several different time frames.

    Supported protocols : 1.0, 2.0, 2.1, 2.3
    Response to request : CirclePowerUsageRequest
    """

    def __init__(self, protocol_version: str = "2.3") -> None:
        """Initialize CirclePowerUsageResponse message object."""
        super().__init__(b"0013")
        self._pulse_1s = Int(0, 4)
        self._pulse_8s = Int(0, 4)
        self._nanosecond_offset = Int(0, 4)
        self._params += [self._pulse_1s, self._pulse_8s]
        if protocol_version == "2.3":
            self._pulse_counter_consumed = Int(0, 8)
            self._pulse_counter_produced = Int(0, 8)
            self._params += [
                self._pulse_counter_consumed,
                self._pulse_counter_produced,
            ]
        self._params += [self._nanosecond_offset]

    @property
    def pulse_1s(self) -> int:
        """Return pulses last second."""
        return self._pulse_1s.value

    @property
    def pulse_8s(self) -> int:
        """Return pulses last 8 seconds."""
        return self._pulse_8s.value

    @property
    def offset(self) -> int:
        """Return offset in nanoseconds."""
        return self._nanosecond_offset.value

    @property
    def consumed_counter(self) -> int:
        """Return consumed pulses."""
        return self._pulse_counter_consumed.value

    @property
    def produced_counter(self) -> int:
        """Return consumed pulses."""
        return self._pulse_counter_produced.value


class CircleLogDataResponse(PlugwiseResponse):
    """TODO: Returns some kind of log data from a node.

    Only supported at protocol version 1.0 !

          <argument name="macId" length="16"/>
          <argument name="storedAbs" length="8"/>
          <argument name="powermeterinfo" length="8"/>
          <argument name="flashaddress" length="8"/>

    Supported protocols : 1.0
    Response to: CircleLogDataRequest
    """

    def __init__(self) -> None:
        """Initialize CircleLogDataResponse message object."""
        super().__init__(b"0015")
        self.stored_abs = DateTime()
        self.powermeterinfo = Int(0, 8, False)
        self.flashaddress = LogAddr(0, length=8)
        self._params += [
            self.stored_abs,
            self.powermeterinfo,
            self.flashaddress,
        ]


class CirclePlusScanResponse(PlugwiseResponse):
    """Returns the MAC of a registered node at the specified memory address of a Circle+.

    Supported protocols : 1.0, 2.0
    Response to request : CirclePlusScanRequest
    """

    def __init__(self) -> None:
        """Initialize CirclePlusScanResponse message object."""
        super().__init__(b"0019")
        self._registered_mac = String(None, length=16)
        self._network_address = Int(0, 2, False)
        self._params += [self._registered_mac, self._network_address]

    @property
    def registered_mac(self) -> str:
        """Return the mac of the node."""
        return self._registered_mac.value

    @property
    def network_address(self) -> int:
        """Return the network address."""
        return self._network_address.value

    def __repr__(self) -> str:
        """Convert response into writable str."""
        return f"{super().__repr__()[:-1]}, network_address={self.network_address}, registered_mac={self.registered_mac})"


class NodeRemoveResponse(PlugwiseResponse):
    """Confirmation (or not) if node is removed from the Plugwise network.

    Also confirmation it has been removed from the memory of the Circle+

    Supported protocols : 1.0, 2.0
    Response to request : NodeRemoveRequest
    """

    def __init__(self) -> None:
        """Initialize NodeRemoveResponse message object."""
        super().__init__(b"001D")
        self.node_mac_id = String(None, length=16)
        self.status = Int(0, 2)
        self._params += [self.node_mac_id, self.status]


class NodeInfoResponse(PlugwiseResponse):
    """Returns the status information of Node.

    Supported protocols : 1.0, 2.0, 2.3
    Response to request : NodeInfoRequest
    """

    def __init__(self, protocol_version: str = "2.0") -> None:
        """Initialize NodeInfoResponse message object."""
        super().__init__(b"0024")

        self.datetime = DateTime()
        self._logaddress_pointer = LogAddr(0, length=8)
        if protocol_version in ("1.0", "2.0"):
            # FIXME 1.0: Define "absoluteHour" variable
            self._relay_state = Int(0, length=2)
            self._params += [
                self.datetime,
                self._logaddress_pointer,
                self._relay_state,
            ]
        if protocol_version == "2.3":
            # FIXME: Define "State_mask" variable
            self.state_mask = Int(0, length=2)
            self._params += [
                self.datetime,
                self._logaddress_pointer,
                self.state_mask,
            ]
        self._frequency = Int(0, length=2)
        self._hw_ver = String(None, length=12)
        self._fw_ver = UnixTimestamp(None)
        self._node_type = Int(0, length=2)
        self._params += [
            self._frequency,
            self._hw_ver,
            self._fw_ver,
            self._node_type,
        ]

    @property
    def hardware(self) -> str:
        """Return hardware id."""
        return str(self._hw_ver.value)

    @property
    def firmware(self) -> datetime:
        """Return timestamp of firmware."""
        return self._fw_ver.value

    @property
    def node_type(self) -> NodeType:
        """Return the type of node."""
        return NodeType(self._node_type.value)

    @property
    def current_logaddress_pointer(self) -> int:
        """Return the current energy log address."""
        return self._logaddress_pointer.value

    @property
    def relay_state(self) -> bool:
        """Return state of relay."""
        return self._relay_state.value == 1

    @property
    def frequency(self) -> int:
        """Return frequency config of node."""
        return self._frequency.value

    def __repr__(self) -> str:
        """Convert request into writable str."""
        return f"{super().__repr__()[:-1]}, log_address_pointer={self._logaddress_pointer.value})"


class EnergyCalibrationResponse(PlugwiseResponse):
    """Returns the calibration settings of node.

    Supported protocols : 1.0, 2.0
    Response to request : EnergyCalibrationRequest
    """

    def __init__(self) -> None:
        """Initialize EnergyCalibrationResponse message object."""
        super().__init__(b"0027")
        self._gain_a = Float(0, 8)
        self._gain_b = Float(0, 8)
        self._off_tot = Float(0, 8)
        self._off_noise = Float(0, 8)
        self._params += [self._gain_a, self._gain_b, self._off_tot, self._off_noise]

    @property
    def gain_a(self) -> float:
        """Return the gain A."""
        return self._gain_a.value

    @property
    def gain_b(self) -> float:
        """Return the gain B."""
        return self._gain_b.value

    @property
    def off_tot(self) -> float:
        """Return the offset."""
        return self._off_tot.value

    @property
    def off_noise(self) -> float:
        """Return the offset."""
        return self._off_noise.value


class CirclePlusRealTimeClockResponse(PlugwiseResponse):
    """returns the real time clock of CirclePlus node.

    Supported protocols : 1.0, 2.0
    Response to request : CirclePlusRealTimeClockGetRequest
    """

    def __init__(self) -> None:
        """Initialize CirclePlusRealTimeClockResponse message object."""
        super().__init__(b"003A")
        self.time = RealClockTime()
        self.day_of_week = Int(0, 2, False)
        self.date = RealClockDate()
        self._params += [self.time, self.day_of_week, self.date]


# TODO : Insert
#
# ID = b"003D" = Schedule response


class CircleClockResponse(PlugwiseResponse):
    """Returns the current internal clock of Node.

    Supported protocols : 1.0, 2.0
    Response to request : CircleClockGetRequest
    """

    def __init__(self) -> None:
        """Initialize CircleClockResponse message object."""
        super().__init__(b"003F")
        self.time = Time()
        self.day_of_week = Int(0, 2, False)
        self.unknown = Int(0, 2)
        self.unknown2 = Int(0, 4)
        self._params += [
            self.time,
            self.day_of_week,
            self.unknown,
            self.unknown2,
        ]


class CircleEnergyLogsResponse(PlugwiseResponse):
    """Returns historical energy usage of requested memory address.

    Each response contains 4 energy counters at specified 1 hour timestamp

    Response to: CircleEnergyLogsRequest
    """

    def __init__(self) -> None:
        """Initialize CircleEnergyLogsResponse message object."""
        super().__init__(b"0049")
        self.logdate1 = DateTime()
        self.pulses1 = Int(0, 8)
        self.logdate2 = DateTime()
        self.pulses2 = Int(0, 8)
        self.logdate3 = DateTime()
        self.pulses3 = Int(0, 8)
        self.logdate4 = DateTime()
        self.pulses4 = Int(0, 8)
        self._logaddr = LogAddr(0, length=8)
        self._params += [
            self.logdate1,
            self.pulses1,
            self.logdate2,
            self.pulses2,
            self.logdate3,
            self.pulses3,
            self.logdate4,
            self.pulses4,
            self._logaddr,
        ]

    @property
    def log_address(self) -> int:
        """Return the gain A."""
        return self._logaddr.value

    @property
    def log_data(self) -> dict[int, tuple[datetime | None, int | None]]:
        """Return log data."""
        log_data: dict[int, tuple[datetime | None, int | None]] = {}
        if self.logdate1.value_set:
            log_data[1] = (self.logdate1.value, self.pulses1.value)
        else:
            log_data[1] = (None, None)
        if self.logdate2.value_set:
            log_data[2] = (self.logdate2.value, self.pulses2.value)
        else:
            log_data[2] = (None, None)
        if self.logdate3.value_set:
            log_data[3] = (self.logdate3.value, self.pulses3.value)
        else:
            log_data[3] = (None, None)
        if self.logdate4.value_set:
            log_data[4] = (self.logdate4.value, self.pulses4.value)
        else:
            log_data[4] = (None, None)
        return log_data

    def __repr__(self) -> str:
        """Convert request into writable str."""
        return f"{super().__repr__()[:-1]}, log_address={self._logaddr.value})"


class NodeAwakeResponse(PlugwiseResponse):
    """Announce that a sleeping end device is awake.

    A sleeping end device (SED) like  Scan, Sense, Switch) sends
    this message to announce that is awake.
    Possible awake types:
    - 0 : The SED joins the network for maintenance
    - 1 : The SED joins a network for the first time
    - 2 : The SED joins a network it has already joined, e.g. after
          reinserting a battery
    - 3 : When a SED switches a device group or when reporting values
          such as temperature/humidity
    - 4 : TODO: Unknown
    - 5 : A human pressed the button on a SED to wake it up

    Response to: <nothing>
    """

    def __init__(self) -> None:
        """Initialize NodeAwakeResponse message object."""
        super().__init__(NODE_AWAKE_RESPONSE_ID)
        self._awake_type = Int(0, 2, False)
        self._params += [self._awake_type]
        self.priority = Priority.HIGH

    @property
    def awake_type(self) -> NodeAwakeResponseType:
        """Return the node awake type."""
        return NodeAwakeResponseType(self._awake_type.value)

    def __repr__(self) -> str:
        """Convert request into writable str."""
        return f"{super().__repr__()[:-1]}, awake_type={self.awake_type.name})"


class NodeSwitchGroupResponse(PlugwiseResponse):
    """Announce groups on/off.

    A sleeping end device (SED: Scan, Sense, Switch) sends
    this message to switch groups on/off when the configured
    switching conditions have been met.

    Response to: <nothing>
    """

    def __init__(self) -> None:
        """Initialize NodeSwitchGroupResponse message object."""
        super().__init__(NODE_SWITCH_GROUP_ID)
        self.group = Int(0, 2, False)
        self._power_state = Int(0, length=2)
        self._params += [
            self.group,
            self._power_state,
        ]

    @property
    def switch_state(self) -> bool:
        """Return state of switch (True = On, False = Off)."""
        return self._power_state.value != 0

    def __repr__(self) -> str:
        """Convert request into writable str."""
        return f"{super().__repr__()[:-1]}, power_state={self._power_state.value}, group={self.group.value})"


class NodeFeaturesResponse(PlugwiseResponse):
    """Returns supported features of node.

    TODO: Feature Bit mask

    Response to: NodeFeaturesRequest
    """

    def __init__(self) -> None:
        """Initialize NodeFeaturesResponse message object."""
        super().__init__(b"0060")
        self.features = String(None, length=16)
        self._params += [self.features]


class NodeRejoinResponse(PlugwiseResponse):
    """Notification message when node (re)joined existing network again.

    Sent when a SED (re)joins the network e.g. when you reinsert
    the battery of a Scan

    sequence number is always FFFD

    Response to: <nothing> or NodeAddRequest
    """

    def __init__(self) -> None:
        """Initialize NodeRejoinResponse message object."""
        super().__init__(NODE_REJOIN_ID)


class NodeAckResponse(PlugwiseResponse):
    """Acknowledge message in regular format.

    Sent by nodes supporting plugwise 2.4 protocol version

    Response to: ?
    """

    def __init__(self) -> None:
        """Initialize NodeAckResponse message object."""
        super().__init__(b"0100")
        self._node_ack_type = Bytes(None, length=4)
        self._params += [self._node_ack_type]
        self.priority = Priority.HIGH

    @property
    def node_ack_type(self) -> NodeAckResponseType:
        """Return acknowledge response type."""
        return NodeAckResponseType(self._node_ack_type.value)

    def __repr__(self) -> str:
        """Convert request into writable str."""
        return f"{super().__repr__()[:-1]}, Ack={self.node_ack_type.name})"


class SenseReportResponse(PlugwiseResponse):
    """Returns the current temperature and humidity of a Sense node.

    The interval this report is sent is configured by
    the 'SenseReportIntervalRequest' request

    Response to: <nothing>
    """

    def __init__(self) -> None:
        """Initialize SenseReportResponse message object."""
        super().__init__(SENSE_REPORT_ID)
        self.humidity = Int(0, length=4, negative=False)
        self.temperature = Int(0, length=4)
        self._params += [self.humidity, self.temperature]


class CircleRelayInitStateResponse(PlugwiseResponse):
    """Returns the configured relay state after power-up of Circle.

    Supported protocols : 2.6
    Response to request : CircleRelayInitStateRequest
    """

    def __init__(self) -> None:
        """Initialize CircleRelayInitStateResponse message object."""
        super().__init__(b"0139")
        self.is_get = Int(0, length=2)
        self.relay = Int(0, length=2)
        self._params += [self.is_get, self.relay]


def get_message_object(  # noqa: C901 PLR0911 PLR0912
    identifier: bytes, length: int, seq_id: bytes
) -> PlugwiseResponse | None:
    """Return message class based on sequence ID, Length of message or message ID."""
    # First check for known sequence ID's
    if seq_id == REJOIN_RESPONSE_SEQ_ID:
        return NodeRejoinResponse()
    if seq_id == AWAKE_RESPONSE_SEQ_ID:
        return NodeAwakeResponse()
    if seq_id == SWITCH_GROUP_RESPONSE_SEQ_ID:
        return NodeSwitchGroupResponse()
    if seq_id == JOIN_AVAILABLE_SEQ_ID:
        return NodeJoinAvailableResponse()

    # No fixed sequence ID, continue at message ID
    if identifier == b"0000":
        if length == 20:
            return StickResponse()
        if length == 36:
            return NodeResponse()
        return None

    # Regular response ID's
    if identifier == b"0002":
        return StickNetworkInfoResponse()
    if identifier == b"0003":
        return NodeSpecificResponse()
    if identifier == b"0005":
        return CirclePlusConnectResponse()
    if identifier == NODE_JOIN_ID:
        return NodeJoinAvailableResponse()
    if identifier == b"000E":
        return NodePingResponse()
    if identifier == b"0010":
        return NodeImageValidationResponse()
    if identifier == b"0011":
        return StickInitResponse()
    if identifier == b"0013":
        return CirclePowerUsageResponse()
    if identifier == b"0015":
        return CircleLogDataResponse()
    if identifier == b"0019":
        return CirclePlusScanResponse()
    if identifier == b"001D":
        return NodeRemoveResponse()
    if identifier == b"0024":
        return NodeInfoResponse()
    if identifier == b"0027":
        return EnergyCalibrationResponse()
    if identifier == b"003A":
        return CirclePlusRealTimeClockResponse()
    if identifier == b"003F":
        return CircleClockResponse()
    if identifier == b"0049":
        return CircleEnergyLogsResponse()
    if identifier == NODE_SWITCH_GROUP_ID:
        return NodeSwitchGroupResponse()
    if identifier == b"0060":
        return NodeFeaturesResponse()
    if identifier == NODE_REJOIN_ID:
        return NodeRejoinResponse()
    if identifier == b"0100":
        return NodeAckResponse()
    if identifier == SENSE_REPORT_ID:
        return SenseReportResponse()
    if identifier == b"0139":
        return CircleRelayInitStateResponse()
    raise MessageError(f"Unknown message for identifier {identifier!r}")
