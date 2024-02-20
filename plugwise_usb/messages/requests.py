"""All known request messages to be send to plugwise devices."""
from __future__ import annotations

from asyncio import Future, TimerHandle, get_running_loop
from collections.abc import Callable
from datetime import datetime, timezone
from enum import Enum
import logging

from ..constants import (
    DAY_IN_MINUTES,
    HOUR_IN_MINUTES,
    LOGADDR_OFFSET,
    MAX_RETRIES,
    MESSAGE_FOOTER,
    MESSAGE_HEADER,
    NODE_TIME_OUT,
)
from ..exceptions import NodeError, NodeTimeout, StickError, StickTimeout
from ..messages.responses import PlugwiseResponse, StickResponse, StickResponseType
from ..util import (
    DateTime,
    Int,
    LogAddr,
    RealClockDate,
    RealClockTime,
    SInt,
    String,
    Time,
)
from . import PlugwiseMessage

_LOGGER = logging.getLogger(__name__)


class Priority(int, Enum):
    """Message priority levels for USB-stick message requests."""

    CANCEL = 0
    HIGH = 1
    MEDIUM = 2
    LOW = 3


class PlugwiseRequest(PlugwiseMessage):
    """Base class for request messages to be send from by USB-Stick."""

    arguments: list = []
    priority: Priority = Priority.MEDIUM

    def __init__(
        self,
        identifier: bytes,
        mac: bytes | None,
    ) -> None:
        """Initialize request message."""
        super().__init__(identifier)

        self._args = []
        self._mac = mac
        self._send_counter: int = 0
        self._max_retries: int = MAX_RETRIES
        self.timestamp = datetime.now(timezone.utc)
        self._loop = get_running_loop()
        self._id = id(self)
        self._reply_identifier: bytes = b"0000"
        self._response: PlugwiseResponse | None = None
        self._stick_subscription_fn: Callable[[], None] | None = None
        self._unsubscribe_stick_response: Callable[[], None] | None = None
        self._unsubscribe_node_response: Callable[[], None] | None = None
        self._response_timeout: TimerHandle | None = None
        self._response_future: Future[PlugwiseResponse] = (
            self._loop.create_future()
        )
        self._other = False

    def __repr__(self) -> str:
        """Convert request into writable str."""
        return f"{self.__class__.__name__} for {self.mac_decoded}"

    def response_future(self) -> Future[PlugwiseResponse]:
        """Return awaitable future with response message."""
        return self._response_future

    def reset_future(self):
        """Return awaitable future with response message."""
        self._response_future = self._loop.create_future()

    @property
    def response(self) -> PlugwiseResponse:
        """Return response message."""
        if not self._response_future.done():
            raise StickError("No response available")
        return self._response_future.result()

    @property
    def seq_id(self) -> bytes | None:
        """Return sequence id assigned to this request."""
        return self._seq_id

    @seq_id.setter
    def seq_id(self, seq_id: bytes) -> None:
        """Assign sequence id."""
        self._seq_id = seq_id
        if self._unsubscribe_stick_response is not None:
            return
        self._unsubscribe_stick_response = self._stick_subscription_fn(
            self._process_stick_response,
            seq_id=seq_id
        )

    def subscribe_to_responses(
        self,
        stick_subscription_fn: Callable[[], None],
        node_subscription_fn: Callable[[], None]
    ) -> None:
        """Register for response messages."""
        self._unsubscribe_node_response = (
            node_subscription_fn(
                self._process_node_response,
                mac=self._mac,
                message_ids=(b"0000", self._reply_identifier),
            )
        )
        self._stick_subscription_fn = stick_subscription_fn

    def start_response_timeout(self) -> None:
        """Start timeout for node response."""
        self.stop_response_timeout()
        self._response_timeout = self._loop.call_later(
            NODE_TIME_OUT, self._response_timeout_expired
        )

    def stop_response_timeout(self) -> None:
        """Stop timeout for node response."""
        if self._response_timeout is not None:
            self._response_timeout.cancel()

    def _response_timeout_expired(self, stick_timeout: bool = False) -> None:
        """Handle response timeout."""
        if self._response_future.done():
            return
        if self._unsubscribe_node_response is not None:
            self._unsubscribe_node_response()
            self._unsubscribe_node_response = None
        if stick_timeout:
            self._response_future.set_exception(
                StickTimeout(
                    f"Stick Timeout: USB-stick responded with time out to {self}"
                )
            )
        else:
            self._response_future.set_exception(
                NodeTimeout(
                    f"Node Timeout: No response to {self} within {NODE_TIME_OUT} seconds"
                )
            )

    def assign_error(self, error: BaseException) -> None:
        """Assign error for this request."""
        self.stop_response_timeout()
        if self._response_future.done():
            return
        self._response_future.set_exception(error)

    async def _process_node_response(self, response: PlugwiseResponse) -> bool:
        """Process incoming message from node."""
        if self._seq_id is not None and self._seq_id == response.seq_id:
            self._unsubscribe_stick_response()
            self._response = response
            self.stop_response_timeout()
            if not self._response_future.done():
                if self._send_counter > 1:
                    _LOGGER.info("Response %s for retried request %s id %d", response, self, self._id)
                elif self._other:
                    _LOGGER.debug("Response %s for request %s after other", response, self)
                else:
                    _LOGGER.debug("Response %s for request %s id %d", response, self, self._id)
                self._response_future.set_result(response)
            else:
                _LOGGER.warning("Response %s for request %s id %d already done", response, self, self._id)

            self._unsubscribe_node_response()
            return True
        self._other = True
        if self._seq_id:
            _LOGGER.warning("Response %s for request %s is not mine %s", response, self, str(response.seq_id))
        else:
            _LOGGER.warning("Response %s for request %s has not received seq_id", response, self)
        return False

    async def _process_stick_response(self, stick_response: StickResponse) -> None:
        """Process incoming stick response."""
        if self._response_future.done():
            return
        if self._seq_id is not None and self._seq_id == stick_response.seq_id:
            _LOGGER.debug("%s for request %s id %d", stick_response, self, self._id)
            if stick_response.ack_id == StickResponseType.TIMEOUT:
                self._response_timeout_expired(stick_timeout=True)
            elif stick_response.ack_id == StickResponseType.FAILED:
                if self._unsubscribe_node_response is not None:
                    self._unsubscribe_node_response()
                    self._unsubscribe_node_response = None
                self._response_future.set_exception(
                    NodeError(
                        f"Stick failed request {self._seq_id}"
                    )
                )
            elif stick_response.ack_id == StickResponseType.ACCEPT:
                pass
            else:
                _LOGGER.debug(
                    "Unknown StickResponseType %s at %s for request %s id %d",
                    str(stick_response.ack_id),
                    stick_response,
                    self,
                    self._id
                )

    @property
    def object_id(self) -> int:
        """Return the object id."""
        return self._id

    @property
    def max_retries(self) -> int:
        """Return the maximum retries."""
        return self._max_retries

    @max_retries.setter
    def max_retries(self, max_retries: int) -> None:
        """Set maximum retries."""
        self._max_retries = max_retries

    @property
    def retries_left(self) -> int:
        """Return number of retries left."""
        return self._max_retries - self._send_counter

    @property
    def resend(self) -> bool:
        """Return true if retry counter is not reached yet."""
        return self._max_retries > self._send_counter

    def add_send_attempt(self):
        """Increase the number of retries."""
        self._send_counter += 1

    def __gt__(self, other: PlugwiseRequest) -> bool:
        """Greater than."""
        if self.priority.value == other.priority.value:
            return self.timestamp > other.timestamp
        if self.priority.value < other.priority.value:
            return True
        return False

    def __lt__(self, other: PlugwiseRequest) -> bool:
        """Less than."""
        if self.priority.value == other.priority.value:
            return self.timestamp < other.timestamp
        if self.priority.value > other.priority.value:
            return True
        return False

    def __ge__(self, other: PlugwiseRequest) -> bool:
        """Greater than or equal."""
        if self.priority.value == other.priority.value:
            return self.timestamp >= other.timestamp
        if self.priority.value < other.priority.value:
            return True
        return False

    def __le__(self, other: PlugwiseRequest) -> bool:
        """Less than or equal."""
        if self.priority.value == other.priority.value:
            return self.timestamp <= other.timestamp
        if self.priority.value > other.priority.value:
            return True
        return False


class StickNetworkInfoRequest(PlugwiseRequest):
    """Request network information.

    Supported protocols : 1.0, 2.0
    Response message    : NodeNetworkInfoResponse
    """

    def __init__(self) -> None:
        """Initialize StickNetworkInfoRequest message object."""
        self._reply_identifier = b"0002"
        super().__init__(b"0001", None)


class CirclePlusConnectRequest(PlugwiseRequest):
    """Request to connect a Circle+ to the Stick.

    Supported protocols : 1.0, 2.0
    Response message    : CirclePlusConnectResponse
    """

    def __init__(self, mac: bytes) -> None:
        """Initialize CirclePlusConnectRequest message object."""
        self._reply_identifier = b"0005"
        super().__init__(b"0004", mac)

    # This message has an exceptional format and therefore
    # need to override the serialize method
    def serialize(self) -> bytes:
        """Convert message to serialized list of bytes."""
        # This command has
        # args: byte
        # key, byte
        # networkinfo.index, ulong
        # networkkey = 0
        args = b"00000000000000000000"
        msg: bytes = self._identifier + args
        if self._mac is not None:
            msg += self._mac
        checksum = self.calculate_checksum(msg)
        return MESSAGE_HEADER + msg + checksum + MESSAGE_FOOTER


class NodeAddRequest(PlugwiseRequest):
    """Add node to the Plugwise Network and add it to memory of Circle+ node.

    Supported protocols : 1.0, 2.0
    Response message    : TODO
    """

    def __init__(self, mac: bytes, accept: bool) -> None:
        """Initialize NodeAddRequest message object."""
        super().__init__(b"0007", mac)
        accept_value = 1 if accept else 0
        self._args.append(Int(accept_value, length=2))

    # This message has an exceptional format (MAC at end of message)
    # and therefore a need to override the serialize method
    def serialize(self) -> bytes:
        """Convert message to serialized list of bytes."""
        args = b"".join(a.serialize() for a in self._args)
        msg: bytes = self._identifier + args
        if self._mac is not None:
            msg += self._mac
        checksum = self.calculate_checksum(msg)
        return MESSAGE_HEADER + msg + checksum + MESSAGE_FOOTER

    def validate_reply(self, node_response: PlugwiseResponse) -> bool:
        """"Validate node response."""
        return True


class CirclePlusAllowJoiningRequest(PlugwiseRequest):
    """Enable or disable receiving joining request of unjoined nodes.

    Circle+ node will respond

    Supported protocols : 1.0, 2.0,
                          2.6 (has extra 'AllowThirdParty' field)
    Response message    : NodeAckResponse
    """

    def __init__(self, enable: bool) -> None:
        """Initialize NodeAddRequest message object."""
        super().__init__(b"0008", None)
        self._reply_identifier = b"0003"
        val = 1 if enable else 0
        self._args.append(Int(val, length=2))


class NodeResetRequest(PlugwiseRequest):
    """TODO:Some kind of reset request.

    Supported protocols : 1.0, 2.0, 2.1
    Response message    : <UNKNOWN>
    """

    def __init__(self, mac: bytes, moduletype: int, timeout: int) -> None:
        """Initialize NodeResetRequest message object."""
        super().__init__(b"0009", mac)
        self._args += [
            Int(moduletype, length=2),
            Int(timeout, length=2),
        ]


class StickInitRequest(PlugwiseRequest):
    """Initialize USB-Stick.

    Supported protocols : 1.0, 2.0
    Response message    : StickInitResponse
    """

    def __init__(self) -> None:
        """Initialize StickInitRequest message object."""
        super().__init__(b"000A", None)
        self._reply_identifier = b"0011"
        self._max_retries = 1


class NodeImagePrepareRequest(PlugwiseRequest):
    """TODO: Some kind of request to prepare node for a firmware image.

    Supported protocols : 1.0, 2.0
    Response message    : <UNKNOWN>
    """

    def __init__(self) -> None:
        """Initialize NodeImagePrepareRequest message object."""
        super().__init__(b"000B", None)


class NodeImageValidateRequest(PlugwiseRequest):
    """TODO: Some kind of request to validate a firmware image for a node.

    Supported protocols : 1.0, 2.0
    Response message    : NodeImageValidationResponse
    """

    def __init__(self) -> None:
        """Initialize NodeImageValidateRequest message object."""
        super().__init__(b"000C", None)
        self._reply_identifier = b"0010"


class NodePingRequest(PlugwiseRequest):
    """Ping node.

    Supported protocols : 1.0, 2.0
    Response message    : NodePingResponse
    """

    def __init__(self, mac: bytes, retries: int = MAX_RETRIES) -> None:
        """Initialize NodePingRequest message object."""
        super().__init__(b"000D", mac)
        self._reply_identifier = b"000E"
        self._max_retries = retries


class NodeImageActivateRequest(PlugwiseRequest):
    """TODO: Some kind of request to activate a firmware image for a node.

    Supported protocols : 1.0, 2.0
    Response message    : <UNKNOWN>
    """

    def __init__(
        self, mac: bytes, request_type: int, reset_delay: int
    ) -> None:
        """Initialize NodeImageActivateRequest message object."""
        super().__init__(b"000F", mac)
        _type = Int(request_type, 2)
        _reset_delay = Int(reset_delay, 2)
        self._args += [_type, _reset_delay]


class CirclePowerUsageRequest(PlugwiseRequest):
    """Request current power usage.

    Supported protocols : 1.0, 2.0, 2.1, 2.3
    Response message    : CirclePowerUsageResponse
    """

    def __init__(self, mac: bytes) -> None:
        """Initialize CirclePowerUsageRequest message object."""
        super().__init__(b"0012", mac)
        self._reply_identifier = b"0013"


class CircleLogDataRequest(PlugwiseRequest):
    """TODO: Some kind of request to get log data from a node.

    Only supported at protocol version 1.0 !

          <argument name="fromAbs" length="8"/>
          <argument name="toAbs" length="8"/>

    Supported protocols : 1.0
    Response message    :  CircleLogDataResponse
    """

    def __init__(self, mac: bytes, start: datetime, end: datetime) -> None:
        """Initialize CircleLogDataRequest message object."""
        super().__init__(b"0014", mac)
        self._reply_identifier = b"0015"
        passed_days_start = start.day - 1
        month_minutes_start = (
            (passed_days_start * DAY_IN_MINUTES)
            + (start.hour * HOUR_IN_MINUTES)
            + start.minute
        )
        from_abs = DateTime(start.year, start.month, month_minutes_start)
        passed_days_end = end.day - 1
        month_minutes_end = (
            (passed_days_end * DAY_IN_MINUTES)
            + (end.hour * HOUR_IN_MINUTES)
            + end.minute
        )
        to_abs = DateTime(end.year, end.month, month_minutes_end)
        self._args += [from_abs, to_abs]


class CircleClockSetRequest(PlugwiseRequest):
    """Set internal clock of node and flash address.

    reset=True, will reset all locally stored energy logs

    Supported protocols : 1.0, 2.0
    Response message    : NodeResponse
    """

    def __init__(
        self,
        mac: bytes,
        dt: datetime,
        protocol_version: float,
        reset: bool = False,
    ) -> None:
        """Initialize CircleLogDataRequest message object."""
        super().__init__(b"0016", mac)
        self._reply_identifier = b"0000"
        self.priority = Priority.HIGH
        if protocol_version == 1.0:
            pass
            # FIXME: Define "absoluteHour" variable
        elif protocol_version >= 2.0:
            passed_days = dt.day - 1
            month_minutes = (
                (passed_days * DAY_IN_MINUTES)
                + (dt.hour * HOUR_IN_MINUTES)
                + dt.minute
            )
            this_date = DateTime(dt.year, dt.month, month_minutes)
        this_time = Time(dt.hour, dt.minute, dt.second)
        day_of_week = Int(dt.weekday(), 2)
        if reset:
            log_buf_addr = LogAddr(LOGADDR_OFFSET, 8, False)
        else:
            log_buf_addr = String("FFFFFFFF", 8)
        self._args += [this_date, log_buf_addr, this_time, day_of_week]


class CircleRelaySwitchRequest(PlugwiseRequest):
    """Request to switches relay on/off.

    Supported protocols : 1.0, 2.0
    Response message    : NodeResponse
    """

    def __init__(self, mac: bytes, on: bool) -> None:
        """Initialize CircleRelaySwitchRequest message object."""
        super().__init__(b"0017", mac)
        self._reply_identifier = b"0000"
        self.priority = Priority.HIGH
        val = 1 if on else 0
        self._args.append(Int(val, length=2))


class CirclePlusScanRequest(PlugwiseRequest):
    """Request all linked Circle plugs from Circle+.

    A Plugwise network (Circle+) can have 64 devices the node ID value
    has a range from 0 to 63

    Supported protocols : 1.0, 2.0
    Response message    : CirclePlusScanResponse
    """

    def __init__(self, mac: bytes, network_address: int) -> None:
        """Initialize CirclePlusScanRequest message object."""
        super().__init__(b"0018", mac)
        self._reply_identifier = b"0019"
        self._args.append(Int(network_address, length=2))
        self.network_address = network_address


class NodeRemoveRequest(PlugwiseRequest):
    """Request node to be removed from Plugwise network by removing it from memory of Circle+ node.

    Supported protocols : 1.0, 2.0
    Response message    : NodeRemoveResponse
    """

    def __init__(self, mac_circle_plus: bytes, mac_to_unjoined: str) -> None:
        """Initialize NodeRemoveRequest message object."""
        super().__init__(b"001C", mac_circle_plus)
        self._reply_identifier = b"001D"
        self._args.append(String(mac_to_unjoined, length=16))


class NodeInfoRequest(PlugwiseRequest):
    """Request status info of node.

    Supported protocols : 1.0, 2.0, 2.3
    Response message    : NodeInfoResponse
    """

    def __init__(self, mac: bytes, retries: int = MAX_RETRIES) -> None:
        """Initialize NodeInfoRequest message object."""
        super().__init__(b"0023", mac)
        self._reply_identifier = b"0024"
        self._max_retries = retries


class EnergyCalibrationRequest(PlugwiseRequest):
    """Request power calibration settings of node.

    Supported protocols : 1.0, 2.0
    Response message    : EnergyCalibrationResponse
    """

    def __init__(self, mac: bytes) -> None:
        """Initialize EnergyCalibrationRequest message object."""
        super().__init__(b"0026", mac)
        self._reply_identifier = b"0027"


class CirclePlusRealTimeClockSetRequest(PlugwiseRequest):
    """Set real time clock of Circle+.

    Supported protocols : 1.0, 2.0
    Response message    : NodeResponse
    """

    def __init__(self, mac: bytes, dt: datetime):
        """Initialize CirclePlusRealTimeClockSetRequest message object."""
        super().__init__(b"0028", mac)
        self._reply_identifier = b"0000"
        self.priority = Priority.HIGH
        this_time = RealClockTime(dt.hour, dt.minute, dt.second)
        day_of_week = Int(dt.weekday(), 2)
        this_date = RealClockDate(dt.day, dt.month, dt.year)
        self._args += [this_time, day_of_week, this_date]


class CirclePlusRealTimeClockGetRequest(PlugwiseRequest):
    """Request current real time clock of CirclePlus.

    Supported protocols : 1.0, 2.0
    Response message    : CirclePlusRealTimeClockResponse
    """

    def __init__(self, mac: bytes):
        """Initialize CirclePlusRealTimeClockGetRequest message object."""
        super().__init__(b"0029", mac)
        self._reply_identifier = b"003A"

# TODO : Insert
#
# ID = b"003B" = Get Schedule request
# ID = b"003C" = Set Schedule request


class CircleClockGetRequest(PlugwiseRequest):
    """Request current internal clock of node.

    Supported protocols : 1.0, 2.0
    Response message    :  CircleClockResponse
    """

    def __init__(self, mac: bytes):
        """Initialize CircleClockGetRequest message object."""
        super().__init__(b"003E", mac)
        self._reply_identifier = b"003F"


class CircleActivateScheduleRequest(PlugwiseRequest):
    """Request to switch Schedule on or off.

    Supported protocols : 1.0, 2.0
    Response message    : <UNKNOWN> TODO:
    """

    def __init__(self, mac: bytes, on: bool) -> None:
        """Initialize CircleActivateScheduleRequest message object."""
        super().__init__(b"0040", mac)
        val = 1 if on else 0
        self._args.append(Int(val, length=2))
        # the second parameter is always 0x01
        self._args.append(Int(1, length=2))


class NodeAddToGroupRequest(PlugwiseRequest):
    """Add node to group.

    Response message: TODO:
    """

    def __init__(
        self, mac: bytes, group_mac: bytes, task_id: str, port_mask: str
    ) -> None:
        """Initialize NodeAddToGroupRequest message object."""
        super().__init__(b"0045", mac)
        group_mac_val = String(group_mac, length=16)
        task_id_val = String(task_id, length=16)
        port_mask_val = String(port_mask, length=16)
        self._args += [group_mac_val, task_id_val, port_mask_val]


class NodeRemoveFromGroupRequest(PlugwiseRequest):
    """Remove node from group.

    Response message: TODO:
    """

    def __init__(self, mac: bytes, group_mac: bytes) -> None:
        """Initialize NodeRemoveFromGroupRequest message object."""
        super().__init__(b"0046", mac)
        group_mac_val = String(group_mac, length=16)
        self._args += [group_mac_val]


class NodeBroadcastGroupSwitchRequest(PlugwiseRequest):
    """Broadcast to group to switch.

    Response message: TODO:
    """

    def __init__(self, group_mac: bytes, switch_state: bool) -> None:
        """Initialize NodeBroadcastGroupSwitchRequest message object."""
        super().__init__(b"0047", group_mac)
        val = 1 if switch_state else 0
        self._args.append(Int(val, length=2))


class CircleEnergyLogsRequest(PlugwiseRequest):
    """Request energy usage counters stored a given memory address.

    Response message: CircleEnergyLogsResponse
    """

    def __init__(self, mac: bytes, log_address: int) -> None:
        """Initialize CircleEnergyLogsRequest message object."""
        super().__init__(b"0048", mac)
        self._reply_identifier = b"0049"
        self._log_address = log_address
        self.priority = Priority.LOW
        self._args.append(LogAddr(log_address, 8))

    def __repr__(self) -> str:
        """Convert request into writable str."""
        return f"{self.__class__.__name__} for {self.mac_decoded} | log_address={self._log_address}"


class CircleHandlesOffRequest(PlugwiseRequest):
    """?PWSetHandlesOffRequestV1_0.

    Response message: ?
    """

    def __init__(self, mac: bytes) -> None:
        """Initialize CircleHandlesOffRequest message object."""
        super().__init__(b"004D", mac)


class CircleHandlesOnRequest(PlugwiseRequest):
    """?PWSetHandlesOnRequestV1_0.

    Response message: ?
    """

    def __init__(self, mac: bytes) -> None:
        """Initialize CircleHandlesOnRequest message object."""
        super().__init__(b"004E", mac)


class NodeSleepConfigRequest(PlugwiseRequest):
    """Configure timers for SED nodes to minimize battery usage.

    stay_active             : Duration in seconds the SED will be
                              awake for receiving commands
    sleep_for               : Duration in minutes the SED will be
                              in sleeping mode and not able to respond
                              any command
    maintenance_interval    : Interval in minutes the node will wake up
                             and able to receive commands
    clock_sync              : Enable/disable clock sync
    clock_interval          : Duration in minutes the node synchronize
                              its clock

    Response message: Ack message with SLEEP_SET
    """

    def __init__(
        self,
        mac: bytes,
        stay_active: int,
        maintenance_interval: int,
        sleep_for: int,
        sync_clock: bool,
        clock_interval: int,
    ):
        """Initialize NodeSleepConfigRequest message object."""
        super().__init__(b"0050", mac)
        self._reply_identifier = b"0100"
        stay_active_val = Int(stay_active, length=2)
        sleep_for_val = Int(sleep_for, length=4)
        maintenance_interval_val = Int(maintenance_interval, length=4)
        val = 1 if sync_clock else 0
        clock_sync_val = Int(val, length=2)
        clock_interval_val = Int(clock_interval, length=4)
        self._args += [
            stay_active_val,
            maintenance_interval_val,
            sleep_for_val,
            clock_sync_val,
            clock_interval_val,
        ]


class NodeSelfRemoveRequest(PlugwiseRequest):
    """TODO: Remove node?.

    <command number="0051" vnumber="1.0"
    implementation="Plugwise.IO.Commands.V20.PWSelfRemovalRequestV1_0">
      <arguments>
        <argument name="macId" length="16"/>
      </arguments>
    </command>

    """

    def __init__(self, mac: bytes) -> None:
        """Initialize NodeSelfRemoveRequest message object."""
        super().__init__(b"0051", mac)


class CircleMeasureIntervalRequest(PlugwiseRequest):
    """Configure the logging interval of energy measurement in minutes.

    FIXME: Make sure production interval is a multiply of consumption !!

    Response message: Ack message with ???  TODO:
    """

    def __init__(self, mac: bytes, consumption: int, production: int):
        """Initialize CircleMeasureIntervalRequest message object."""
        super().__init__(b"0057", mac)
        self._args.append(Int(consumption, length=4))
        self._args.append(Int(production, length=4))


class NodeClearGroupMacRequest(PlugwiseRequest):
    """TODO: usage?.

    Response message: ????
    """

    def __init__(self, mac: bytes, taskId: int) -> None:
        """Initialize NodeClearGroupMacRequest message object."""
        super().__init__(b"0058", mac)
        self._args.append(Int(taskId, length=2))


class CircleSetScheduleValueRequest(PlugwiseRequest):
    """Send chunk of On/Off/StandbyKiller Schedule to Circle(+).

    Response message: TODO:
    """

    def __init__(self, mac: bytes, val: int) -> None:
        """Initialize CircleSetScheduleValueRequest message object."""
        super().__init__(b"0059", mac)
        self._args.append(SInt(val, length=4))


class NodeFeaturesRequest(PlugwiseRequest):
    """Request feature set node supports.

    Response message: NodeFeaturesResponse
    """

    def __init__(self, mac: bytes, val: int) -> None:
        """Initialize NodeFeaturesRequest message object."""
        super().__init__(b"005F", mac)
        self._reply_identifier = b"0060"
        self._args.append(SInt(val, length=4))


class ScanConfigureRequest(PlugwiseRequest):
    """Configure a Scan node.

    reset_timer : Delay in minutes when signal is send
                  when no motion is detected
    sensitivity : Sensitivity of Motion sensor
                  (High, Medium, Off)
    light       : Daylight override to only report motion
                  when light level is below calibrated level

    Response message: NodeAckResponse
    """

    def __init__(
        self, mac: bytes, reset_timer: int, sensitivity: int, light: bool
    ):
        """Initialize ScanConfigureRequest message object."""
        super().__init__(b"0101", mac)
        self._reply_identifier = b"0100"
        reset_timer_value = Int(reset_timer, length=2)
        # Sensitivity: HIGH(0x14),  MEDIUM(0x1E),  OFF(0xFF)
        sensitivity_value = Int(sensitivity, length=2)
        light_temp = 1 if light else 0
        light_value = Int(light_temp, length=2)
        self._args += [
            sensitivity_value,
            light_value,
            reset_timer_value,
        ]


class ScanLightCalibrateRequest(PlugwiseRequest):
    """Calibrate light sensitivity.

    Response message: NodeAckResponse
    """

    def __init__(self, mac: bytes):
        """Initialize ScanLightCalibrateRequest message object."""
        super().__init__(b"0102", mac)
        self._reply_identifier = b"0100"


class SenseReportIntervalRequest(PlugwiseRequest):
    """Sets the Sense temperature and humidity measurement report interval in minutes.

    Based on this interval, periodically a 'SenseReportResponse' message is sent by the Sense node

    Response message: NodeAckResponse
    """

    def __init__(self, mac: bytes, interval: int):
        """Initialize ScanLightCalibrateRequest message object."""
        super().__init__(b"0103", mac)
        self._reply_identifier = b"0100"
        self._args.append(Int(interval, length=2))


class CircleRelayInitStateRequest(PlugwiseRequest):
    """Get or set initial relay state after power-up of Circle.

    Supported protocols : 2.6
    Response message    : CircleInitRelayStateResponse
    """

    def __init__(self, mac: bytes, configure: bool, relay_state: bool) -> None:
        """Initialize CircleRelayInitStateRequest message object."""
        super().__init__(b"0138", mac)
        self._reply_identifier = b"0139"
        self.priority = Priority.LOW
        self.set_or_get = Int(1 if configure else 0, length=2)
        self.relay = Int(1 if relay_state else 0, length=2)
        self._args += [self.set_or_get, self.relay]
