"""All known request messages to be send to plugwise devices."""

from __future__ import annotations

from asyncio import Future, TimerHandle, get_running_loop
from collections.abc import Awaitable, Callable, Coroutine
from copy import copy
from datetime import datetime
import logging
from typing import Any

from ..constants import (
    DAY_IN_MINUTES,
    HOUR_IN_MINUTES,
    MAX_RETRIES,
    MESSAGE_FOOTER,
    MESSAGE_HEADER,
    NODE_TIME_OUT,
)
from ..exceptions import MessageError, NodeError, NodeTimeout, StickError, StickTimeout
from ..messages.responses import (
    CircleClockResponse,
    CircleEnergyLogsResponse,
    CircleLogDataResponse,
    CirclePlusConnectResponse,
    CirclePlusRealTimeClockResponse,
    CirclePlusScanResponse,
    CirclePowerUsageResponse,
    CircleRelayInitStateResponse,
    EnergyCalibrationResponse,
    NodeAckResponse,
    NodeFeaturesResponse,
    NodeImageValidationResponse,
    NodeInfoResponse,
    NodePingResponse,
    NodeRemoveResponse,
    NodeResponse,
    NodeSpecificResponse,
    PlugwiseResponse,
    StickInitResponse,
    StickNetworkInfoResponse,
    StickResponse,
    StickResponseType,
)
from . import PlugwiseMessage, Priority
from .properties import (
    DateTime,
    Int,
    LogAddr,
    RealClockDate,
    RealClockTime,
    SInt,
    String,
    Time,
)

_LOGGER = logging.getLogger(__name__)


class PlugwiseRequest(PlugwiseMessage):
    """Base class for request messages to be send from by USB-Stick."""

    _reply_identifier: bytes = b"0000"

    def __init__(
        self,
        send_fn: Callable[[PlugwiseRequest, bool], Awaitable[PlugwiseResponse | None]]
        | None,
        mac: bytes | None,
    ) -> None:
        """Initialize request message."""
        super().__init__()
        self._args = []
        self._mac = mac
        self._send_counter: int = 0
        self._send_fn = send_fn
        self._max_retries: int = MAX_RETRIES
        self._loop = get_running_loop()
        self._response: PlugwiseResponse | None = None
        self._stick_subscription_fn: (
            Callable[
                [
                    Callable[[StickResponse], Coroutine[Any, Any, None]],
                    bytes | None,
                    tuple[StickResponseType, ...] | None,
                ],
                Callable[[], None],
            ]
            | None
        ) = None
        self._node_subscription_fn: (
            Callable[
                [
                    Callable[[PlugwiseResponse], Coroutine[Any, Any, bool]],
                    bytes | None,
                    tuple[bytes, ...] | None,
                    bytes | None,
                ],
                Callable[[], None],
            ]
            | None
        ) = None
        self._unsubscribe_stick_response: Callable[[], None] | None = None
        self._unsubscribe_node_response: Callable[[], None] | None = None
        self._response_timeout: TimerHandle | None = None
        self._response_future: Future[PlugwiseResponse] = self._loop.create_future()
        self._waiting_for_response = False

        self.no_response = False

    def __repr__(self) -> str:
        """Convert request into writable str."""
        if self._seq_id is None:
            return f"{self.__class__.__name__} (mac={self.mac_decoded}, seq_id=UNKNOWN, attempt={self._send_counter})"
        return f"{self.__class__.__name__} (mac={self.mac_decoded}, seq_id={self._seq_id!r}, attempt={self._send_counter})"

    def response_future(self) -> Future[PlugwiseResponse]:
        """Return awaitable future with response message."""
        if self._response_future.done():
            self._response_future = self._loop.create_future()
        return self._response_future

    @property
    def response(self) -> PlugwiseResponse:
        """Return response message."""
        if self._response is None:
            raise StickError("No response available")
        return self._response

    @property
    def seq_id(self) -> bytes | None:
        """Return sequence id assigned to this request."""
        return self._seq_id

    @seq_id.setter
    def seq_id(self, seq_id: bytes) -> None:
        """Assign sequence id."""
        if self._seq_id is not None:
            _LOGGER.warning(
                "Unable to change seq_id into %s for request %s", seq_id, self
            )
            raise MessageError(
                f"Unable to set seq_id to {seq_id!r}. Already set to {self._seq_id!r}"
            )
        self._seq_id = seq_id

    async def subscribe_to_response(
        self,
        stick_subscription_fn: Callable[
            [
                Callable[[StickResponse], Coroutine[Any, Any, None]],
                bytes | None,
                tuple[StickResponseType, ...] | None,
            ],
            Coroutine[Any, Any, Callable[[], None]],
        ],
        node_subscription_fn: Callable[
            [
                Callable[[PlugwiseResponse], Coroutine[Any, Any, bool]],
                bytes | None,
                tuple[bytes, ...] | None,
                bytes | None,
            ],
            Coroutine[Any, Any, Callable[[], None]],
        ],
    ) -> None:
        """Subscribe to receive the response messages."""
        if self._seq_id is None:
            raise MessageError(
                "Unable to subscribe to response because seq_id is not set"
            )
        self._unsubscribe_stick_response = await stick_subscription_fn(
            self._process_stick_response, self._seq_id, None
        )
        self._unsubscribe_node_response = await node_subscription_fn(
            self.process_node_response,
            self._mac,
            (self._reply_identifier,),
            self._seq_id,
        )

    def _unsubscribe_from_stick(self) -> None:
        """Unsubscribe from StickResponse messages."""
        if self._unsubscribe_stick_response is not None:
            self._unsubscribe_stick_response()
            self._unsubscribe_stick_response = None

    def _unsubscribe_from_node(self) -> None:
        """Unsubscribe from NodeResponse messages."""
        if self._unsubscribe_node_response is not None:
            self._unsubscribe_node_response()
            self._unsubscribe_node_response = None

    def start_response_timeout(self) -> None:
        """Start timeout for node response."""
        self.stop_response_timeout()
        self._response_timeout = self._loop.call_later(
            NODE_TIME_OUT, self._response_timeout_expired
        )
        self._waiting_for_response = True

    def stop_response_timeout(self) -> None:
        """Stop timeout for node response."""
        self._waiting_for_response = False
        if self._response_timeout is not None:
            self._response_timeout.cancel()

    @property
    def waiting_for_response(self) -> bool:
        """Indicate if request is actively waiting for a response."""
        return self._waiting_for_response

    def _response_timeout_expired(self, stick_timeout: bool = False) -> None:
        """Handle response timeout."""
        if self._response_future.done():
            return
        if stick_timeout:
            _LOGGER.info("USB-stick responded with time out to %s", self)
        else:
            _LOGGER.info(
                "No response received for %s within %s seconds", self, NODE_TIME_OUT
            )
        self._seq_id = None
        self._unsubscribe_from_stick()
        self._unsubscribe_from_node()
        if stick_timeout:
            self._response_future.set_exception(
                StickTimeout(f"USB-stick responded with time out to {self}")
            )
        else:
            self._response_future.set_exception(
                NodeTimeout(
                    f"No device response to {self} within {NODE_TIME_OUT} seconds"
                )
            )

    def assign_error(self, error: BaseException) -> None:
        """Assign error for this request."""
        self.stop_response_timeout()
        self._unsubscribe_from_stick()
        self._unsubscribe_from_node()
        if self._response_future.done():
            return
        self._waiting_for_response = False
        self._response_future.set_exception(error)

    async def process_node_response(self, response: PlugwiseResponse) -> bool:
        """Process incoming message from node."""
        if self._seq_id is None:
            _LOGGER.warning(
                "Received %s as reply to %s without a seq_id assigned",
                self._response,
                self,
            )
            return False
        if self._seq_id != response.seq_id:
            _LOGGER.warning(
                "Received %s as reply to %s which is not correct (expected seq_id=%s)",
                self._response,
                self,
                str(self.seq_id),
            )
            return False
        if self._response_future.done():
            return False

        self._response = copy(response)
        self.stop_response_timeout()
        self._unsubscribe_from_stick()
        self._unsubscribe_from_node()
        if self._send_counter > 1:
            _LOGGER.debug(
                "Received %s after %s retries as reply to %s",
                self._response,
                self._send_counter,
                self,
            )
        else:
            _LOGGER.debug("Received %s as reply to %s", self._response, self)
        self._response_future.set_result(self._response)
        return True

    async def _process_stick_response(self, stick_response: StickResponse) -> None:
        """Process incoming stick response."""
        if (
            self._response_future.done()
            or self._seq_id is None
            or self._seq_id != stick_response.seq_id
        ):
            return

        if stick_response.ack_id == StickResponseType.ACCEPT:
            return

        if stick_response.ack_id == StickResponseType.TIMEOUT:
            self._response_timeout_expired(stick_timeout=True)
            return

        if stick_response.ack_id == StickResponseType.FAILED:
            self._unsubscribe_from_node()
            self._seq_id = None
            self._response_future.set_exception(
                NodeError(f"Stick failed request {self._seq_id}")
            )
            return

        _LOGGER.debug(
            "Unknown StickResponseType %s at %s for request %s",
            str(stick_response.ack_id),
            stick_response,
            self,
        )

    async def _send_request(
        self, suppress_node_errors=False
    ) -> PlugwiseResponse | None:
        """Send request."""
        if self._send_fn is None:
            return None
        return await self._send_fn(self, suppress_node_errors)

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

    def add_send_attempt(self) -> None:
        """Increase the number of retries."""
        self._send_counter += 1


class PlugwiseCancelRequest(PlugwiseRequest):
    """Cancel request for priority queue."""

    def __init__(self) -> None:
        """Initialize request message."""
        super().__init__(None, None)
        self.priority = Priority.CANCEL


class StickNetworkInfoRequest(PlugwiseRequest):
    """Request network information.

    Supported protocols : 1.0, 2.0
    Response message    : StickNetworkInfoResponse
    """

    _identifier = b"0001"
    _reply_identifier = b"0002"

    async def send(self) -> StickNetworkInfoResponse | None:
        """Send request."""
        result = await self._send_request()
        if isinstance(result, StickNetworkInfoResponse):
            return result
        if result is None:
            return None
        raise MessageError(
            f"Invalid response message. Received {result.__class__.__name__}, expected StickNetworkInfoResponse"
        )


class CirclePlusConnectRequest(PlugwiseRequest):
    """Request to connect a Circle+ to the Stick.

    Supported protocols : 1.0, 2.0
    Response message    : CirclePlusConnectResponse
    """

    _identifier = b"0004"
    _reply_identifier = b"0005"

    async def send(self) -> CirclePlusConnectResponse | None:
        """Send request."""
        result = await self._send_request()
        if isinstance(result, CirclePlusConnectResponse):
            return result
        if result is None:
            return None
        raise MessageError(
            f"Invalid response message. Received {result.__class__.__name__}, expected CirclePlusConnectResponse"
        )

    # This message has an exceptional format and therefore
    # need to override the serialize method
    def serialize(self) -> bytes:
        """Convert message to serialized list of bytes."""
        # This command has
        # args: byte
        # key, byte
        # network info.index, ulong
        # network key = 0
        args = b"00000000000000000000"
        msg: bytes = self._identifier + args
        if self._mac is not None:
            msg += self._mac
        checksum = self.calculate_checksum(msg)
        return MESSAGE_HEADER + msg + checksum + MESSAGE_FOOTER


class NodeAddRequest(PlugwiseRequest):
    """Add node to the Plugwise Network and add it to memory of Circle+ node.

    Supported protocols : 1.0, 2.0
    Response message    : None
    There will be a delayed NodeRejoinResponse, b"0061", picked up by a separate subscription.
    """

    _identifier = b"0007"
    _reply_identifier = None

    def __init__(
        self,
        send_fn: Callable[[PlugwiseRequest, bool], Awaitable[PlugwiseResponse | None]],
        mac: bytes,
        accept: bool,
    ) -> None:
        """Initialize NodeAddRequest message object."""
        super().__init__(send_fn, mac)
        accept_value = 1 if accept else 0
        self._args.append(Int(accept_value, length=2))
        self.no_response = True

    async def send(self) -> None:
        """Send request."""
        await self._send_request()

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


class CirclePlusAllowJoiningRequest(PlugwiseRequest):
    """Enable or disable receiving joining request of unjoined nodes.

    Circle+ node will respond

    Supported protocols : 1.0, 2.0,
                          2.6 (has extra 'AllowThirdParty' field)
    Response message    : NodeResponse
    """

    _identifier = b"0008"

    def __init__(
        self,
        send_fn: Callable[[PlugwiseRequest, bool], Awaitable[PlugwiseResponse | None]],
        enable: bool,
    ) -> None:
        """Initialize NodeAddRequest message object."""
        super().__init__(send_fn, None)
        val = 1 if enable else 0
        self._args.append(Int(val, length=2))

    async def send(self) -> NodeResponse | None:
        """Send request."""
        result = await self._send_request()
        if isinstance(result, NodeResponse):
            return result
        if result is None:
            return None
        raise MessageError(
            f"Invalid response message. Received {result.__class__.__name__}, expected NodeResponse"
        )


class NodeResetRequest(PlugwiseRequest):
    """TODO:Some kind of reset request.

    Supported protocols : 1.0, 2.0, 2.1
    Response message    : <UNKNOWN>
    """

    _identifier = b"0009"
    _reply_identifier = b"0003"

    def __init__(
        self,
        send_fn: Callable[[PlugwiseRequest, bool], Awaitable[PlugwiseResponse | None]],
        mac: bytes,
        moduletype: int,
        timeout: int,
    ) -> None:
        """Initialize NodeResetRequest message object."""
        super().__init__(send_fn, mac)
        self._args += [
            Int(moduletype, length=2),
            Int(timeout, length=2),
        ]

    async def send(self) -> NodeSpecificResponse | None:
        """Send request."""
        result = await self._send_request()
        if isinstance(result, NodeSpecificResponse):
            return result
        if result is None:
            return None
        raise MessageError(
            f"Invalid response message. Received {result.__class__.__name__}, expected NodeSpecificResponse"
        )


class StickInitRequest(PlugwiseRequest):
    """Initialize USB-Stick.

    Supported protocols : 1.0, 2.0
    Response message    : StickInitResponse
    """

    _identifier = b"000A"
    _reply_identifier = b"0011"

    def __init__(
        self,
        send_fn: Callable[[PlugwiseRequest, bool], Awaitable[PlugwiseResponse | None]],
    ) -> None:
        """Initialize StickInitRequest message object."""
        super().__init__(send_fn, None)
        self._max_retries = 1

    async def send(self) -> StickInitResponse | None:
        """Send request."""
        if self._send_fn is None:
            raise MessageError("Send function missing")
        result = await self._send_request()
        if isinstance(result, StickInitResponse):
            return result
        if result is None:
            return None
        raise MessageError(
            f"Invalid response message. Received {result.__class__.__name__}, expected StickInitResponse"
        )


class NodeImagePrepareRequest(PlugwiseRequest):
    """TODO: Some kind of request to prepare node for a firmware image.

    Supported protocols : 1.0, 2.0
    Response message    : <UNKNOWN>
    """

    _identifier = b"000B"
    _reply_identifier = b"0003"

    async def send(self) -> NodeSpecificResponse | None:
        """Send request."""
        result = await self._send_request()
        if isinstance(result, NodeSpecificResponse):
            return result
        if result is None:
            return None
        raise MessageError(
            f"Invalid response message. Received {result.__class__.__name__}, expected NodeSpecificResponse"
        )


class NodeImageValidateRequest(PlugwiseRequest):
    """TODO: Some kind of request to validate a firmware image for a node.

    Supported protocols : 1.0, 2.0
    Response message    : NodeImageValidationResponse
    """

    _identifier = b"000C"
    _reply_identifier = b"0010"

    async def send(self) -> NodeImageValidationResponse | None:
        """Send request."""
        result = await self._send_request()
        if isinstance(result, NodeImageValidationResponse):
            return result
        if result is None:
            return None
        raise MessageError(
            f"Invalid response message. Received {result.__class__.__name__}, expected NodeImageValidationResponse"
        )


class NodePingRequest(PlugwiseRequest):
    """Ping node.

    Supported protocols : 1.0, 2.0
    Response message    : NodePingResponse
    """

    _identifier = b"000D"
    _reply_identifier = b"000E"

    def __init__(
        self,
        send_fn: Callable[[PlugwiseRequest, bool], Awaitable[PlugwiseResponse | None]],
        mac: bytes,
        retries: int = MAX_RETRIES,
    ) -> None:
        """Initialize NodePingRequest message object."""
        super().__init__(send_fn, mac)
        self._reply_identifier = b"000E"
        self._max_retries = retries

    async def send(self) -> NodePingResponse | None:
        """Send request."""
        result = await self._send_request()
        if isinstance(result, NodePingResponse):
            return result
        if result is None:
            return None
        raise MessageError(
            f"Invalid response message. Received {result.__class__.__name__}, expected NodePingResponse"
        )


class NodeImageActivateRequest(PlugwiseRequest):
    """TODO: Some kind of request to activate a firmware image for a node.

    Supported protocols : 1.0, 2.0
    Response message    : <UNKNOWN>
    """

    _identifier = b"000F"
    _reply_identifier = b"000E"

    def __init__(
        self,
        send_fn: Callable[[PlugwiseRequest, bool], Awaitable[PlugwiseResponse | None]],
        mac: bytes,
        request_type: int,
        reset_delay: int,
    ) -> None:
        """Initialize NodeImageActivateRequest message object."""
        super().__init__(send_fn, mac)
        _type = Int(request_type, 2)
        _reset_delay = Int(reset_delay, 2)
        self._args += [_type, _reset_delay]


class CirclePowerUsageRequest(PlugwiseRequest):
    """Request current power usage.

    Supported protocols : 1.0, 2.0, 2.1, 2.3
    Response message    : CirclePowerUsageResponse
    """

    _identifier = b"0012"
    _reply_identifier = b"0013"

    async def send(self) -> CirclePowerUsageResponse | None:
        """Send request."""
        result = await self._send_request()
        if isinstance(result, CirclePowerUsageResponse):
            return result
        if result is None:
            return None
        raise MessageError(
            f"Invalid response message. Received {result.__class__.__name__}, expected CirclePowerUsageResponse"
        )


class CircleLogDataRequest(PlugwiseRequest):
    """TODO: Some kind of request to get log data from a node.

    Only supported at protocol version 1.0 !

          <argument name="fromAbs" length="8"/>
          <argument name="toAbs" length="8"/>

    Supported protocols : 1.0
    Response message    :  CircleLogDataResponse
    """

    _identifier = b"0014"
    _reply_identifier = b"0015"

    def __init__(
        self,
        send_fn: Callable[[PlugwiseRequest, bool], Awaitable[PlugwiseResponse | None]],
        mac: bytes,
        start: datetime,
        end: datetime,
    ) -> None:
        """Initialize CircleLogDataRequest message object."""
        super().__init__(send_fn, mac)
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

    async def send(self) -> CircleLogDataResponse | None:
        """Send request."""
        result = await self._send_request()
        if isinstance(result, CircleLogDataResponse):
            return result
        if result is None:
            return None
        raise MessageError(
            f"Invalid response message. Received {result.__class__.__name__}, expected CircleLogDataResponse"
        )


class CircleClockSetRequest(PlugwiseRequest):
    """Set internal clock of node and flash address.

    reset=True, will reset all locally stored energy logs

    Supported protocols : 1.0, 2.0
    Response message    : NodeResponse
    """

    _identifier = b"0016"

    # pylint: disable=too-many-arguments
    def __init__(
        self,
        send_fn: Callable[[PlugwiseRequest, bool], Awaitable[PlugwiseResponse | None]],
        mac: bytes,
        dt: datetime,
        protocol_version: float,
        reset: bool = False,
    ) -> None:
        """Initialize CircleLogDataRequest message object."""
        super().__init__(send_fn, mac)
        self.priority = Priority.HIGH
        if protocol_version < 2.0:
            # FIXME: Define "absoluteHour" variable
            raise MessageError("Unsupported version of CircleClockSetRequest")

        passed_days = dt.day - 1
        month_minutes = (
            (passed_days * DAY_IN_MINUTES) + (dt.hour * HOUR_IN_MINUTES) + dt.minute
        )
        this_date = DateTime(dt.year, dt.month, month_minutes)
        this_time = Time(dt.hour, dt.minute, dt.second)
        day_of_week = Int(dt.weekday(), 2)
        if reset:
            self._args += [
                this_date,
                LogAddr(0, 8, False),
                this_time,
                day_of_week,
            ]
        else:
            self._args += [this_date, String("FFFFFFFF", 8), this_time, day_of_week]

    async def send(self) -> NodeResponse | None:
        """Send request."""
        result = await self._send_request()
        if isinstance(result, NodeResponse):
            return result
        if result is None:
            return None
        raise MessageError(
            f"Invalid response message. Received {result.__class__.__name__}, expected NodeResponse"
        )


class CircleRelaySwitchRequest(PlugwiseRequest):
    """Request to switches relay on/off.

    Supported protocols : 1.0, 2.0
    Response message    : NodeResponse
    """

    _identifier = b"0017"

    def __init__(
        self,
        send_fn: Callable[[PlugwiseRequest, bool], Awaitable[PlugwiseResponse | None]],
        mac: bytes,
        on: bool,
    ) -> None:
        """Initialize CircleRelaySwitchRequest message object."""
        super().__init__(send_fn, mac)
        self.priority = Priority.HIGH
        val = 1 if on else 0
        self._args.append(Int(val, length=2))

    async def send(self) -> NodeResponse | None:
        """Send request."""
        result = await self._send_request()
        if isinstance(result, NodeResponse):
            return result
        if result is None:
            return None
        raise MessageError(
            f"Invalid response message. Received {result.__class__.__name__}, expected NodeResponse"
        )


class CirclePlusScanRequest(PlugwiseRequest):
    """Request all linked Circle plugs from Circle+.

    A Plugwise network (Circle+) can have 64 devices the node ID value
    has a range from 0 to 63

    Supported protocols : 1.0, 2.0
    Response message    : CirclePlusScanResponse
    """

    _identifier = b"0018"
    _reply_identifier = b"0019"

    def __init__(
        self,
        send_fn: Callable[[PlugwiseRequest, bool], Awaitable[PlugwiseResponse | None]],
        mac: bytes,
        network_address: int,
    ) -> None:
        """Initialize CirclePlusScanRequest message object."""
        super().__init__(send_fn, mac)
        self._args.append(Int(network_address, length=2))
        self.network_address = network_address

    def __repr__(self) -> str:
        """Convert request into writable str."""
        return f"{super().__repr__()[:-1]}, network_address={self.network_address})"

    async def send(self) -> CirclePlusScanResponse | None:
        """Send request."""
        result = await self._send_request()
        if isinstance(result, CirclePlusScanResponse):
            return result
        if result is None:
            return None
        raise MessageError(
            f"Invalid response message. Received {result.__class__.__name__}, expected CirclePlusScanResponse"
        )


class NodeRemoveRequest(PlugwiseRequest):
    """Request node to be removed from Plugwise network by removing it from memory of Circle+ node.

    Supported protocols : 1.0, 2.0
    Response message    : NodeRemoveResponse
    """

    _identifier = b"001C"
    _reply_identifier = b"001D"

    def __init__(
        self,
        send_fn: Callable[[PlugwiseRequest, bool], Awaitable[PlugwiseResponse | None]],
        mac_circle_plus: bytes,
        mac_to_unjoined: str,
    ) -> None:
        """Initialize NodeRemoveRequest message object."""
        super().__init__(send_fn, mac_circle_plus)
        self._args.append(String(mac_to_unjoined, length=16))

    async def send(self) -> NodeRemoveResponse | None:
        """Send request."""
        result = await self._send_request()
        if isinstance(result, NodeRemoveResponse):
            return result
        if result is None:
            return None
        raise MessageError(
            f"Invalid response message. Received {result.__class__.__name__}, expected NodeRemoveResponse"
        )


class NodeInfoRequest(PlugwiseRequest):
    """Request status info of node.

    Supported protocols : 1.0, 2.0, 2.3
    Response message    : NodeInfoResponse
    """

    _identifier = b"0023"
    _reply_identifier = b"0024"

    def __init__(
        self,
        send_fn: Callable[[PlugwiseRequest, bool], Awaitable[PlugwiseResponse | None]],
        mac: bytes,
        retries: int = MAX_RETRIES,
    ) -> None:
        """Initialize NodeInfoRequest message object."""
        super().__init__(send_fn, mac)
        self._max_retries = retries

    async def send(self) -> NodeInfoResponse | None:
        """Send request."""
        result = await self._send_request()
        if isinstance(result, NodeInfoResponse):
            return result
        if result is None:
            return None
        raise MessageError(
            f"Invalid response message. Received {result.__class__.__name__}, expected NodeInfoResponse"
        )


class EnergyCalibrationRequest(PlugwiseRequest):
    """Request power calibration settings of node.

    Supported protocols : 1.0, 2.0
    Response message    : EnergyCalibrationResponse
    """

    _identifier = b"0026"
    _reply_identifier = b"0027"

    async def send(self) -> EnergyCalibrationResponse | None:
        """Send request."""
        result = await self._send_request()
        if isinstance(result, EnergyCalibrationResponse):
            return result
        if result is None:
            return None
        raise MessageError(
            f"Invalid response message. Received {result.__class__.__name__}, expected EnergyCalibrationResponse"
        )


class CirclePlusRealTimeClockSetRequest(PlugwiseRequest):
    """Set real time clock of Circle+.

    Supported protocols : 1.0, 2.0
    Response message    : NodeResponse
    """

    _identifier = b"0028"

    def __init__(
        self,
        send_fn: Callable[[PlugwiseRequest, bool], Awaitable[PlugwiseResponse | None]],
        mac: bytes,
        dt: datetime,
    ):
        """Initialize CirclePlusRealTimeClockSetRequest message object."""
        super().__init__(send_fn, mac)
        self.priority = Priority.HIGH
        this_time = RealClockTime(dt.hour, dt.minute, dt.second)
        day_of_week = Int(dt.weekday(), 2)
        this_date = RealClockDate(dt.day, dt.month, dt.year)
        self._args += [this_time, day_of_week, this_date]

    async def send(self) -> NodeResponse | None:
        """Send request."""
        result = await self._send_request()
        if isinstance(result, NodeResponse):
            return result
        if result is None:
            return None
        raise MessageError(
            f"Invalid response message. Received {result.__class__.__name__}, expected NodeResponse"
        )


class CirclePlusRealTimeClockGetRequest(PlugwiseRequest):
    """Request current real time clock of CirclePlus.

    Supported protocols : 1.0, 2.0
    Response message    : CirclePlusRealTimeClockResponse
    """

    _identifier = b"0029"
    _reply_identifier = b"003A"

    async def send(self) -> CirclePlusRealTimeClockResponse | None:
        """Send request."""
        result = await self._send_request()
        if isinstance(result, CirclePlusRealTimeClockResponse):
            return result
        if result is None:
            return None
        raise MessageError(
            f"Invalid response message. Received {result.__class__.__name__}, expected CirclePlusRealTimeClockResponse"
        )


# TODO : Insert
#
# ID = b"003B" = Get Schedule request
# ID = b"003C" = Set Schedule request


class CircleClockGetRequest(PlugwiseRequest):
    """Request current internal clock of node.

    Supported protocols : 1.0, 2.0
    Response message    :  CircleClockResponse
    """

    _identifier = b"003E"
    _reply_identifier = b"003F"

    async def send(self) -> CircleClockResponse | None:
        """Send request."""
        result = await self._send_request()
        if isinstance(result, CircleClockResponse):
            return result
        if result is None:
            return None
        raise MessageError(
            f"Invalid response message. Received {result.__class__.__name__}, expected CircleClockResponse"
        )


class CircleActivateScheduleRequest(PlugwiseRequest):
    """Request to switch Schedule on or off.

    Supported protocols : 1.0, 2.0
    Response message    : <UNKNOWN> TODO:
    """

    _identifier = b"0040"

    def __init__(
        self,
        send_fn: Callable[[PlugwiseRequest, bool], Awaitable[PlugwiseResponse | None]],
        mac: bytes,
        on: bool,
    ) -> None:
        """Initialize CircleActivateScheduleRequest message object."""
        super().__init__(send_fn, mac)
        val = 1 if on else 0
        self._args.append(Int(val, length=2))
        # the second parameter is always 0x01
        self._args.append(Int(1, length=2))


class NodeAddToGroupRequest(PlugwiseRequest):
    """Add node to group.

    Response message: TODO:
    """

    _identifier = b"0045"

    # pylint: disable=too-many-arguments
    def __init__(
        self,
        send_fn: Callable[[PlugwiseRequest, bool], Awaitable[PlugwiseResponse | None]],
        mac: bytes,
        group_mac: str,
        task_id: str,
        port_mask: str,
    ) -> None:
        """Initialize NodeAddToGroupRequest message object."""
        super().__init__(send_fn, mac)
        group_mac_val = String(group_mac, length=16)
        task_id_val = String(task_id, length=16)
        port_mask_val = String(port_mask, length=16)
        self._args += [group_mac_val, task_id_val, port_mask_val]

    async def send(self) -> NodeResponse | None:
        """Send request."""
        result = await self._send_request()
        if isinstance(result, NodeResponse):
            return result
        if result is None:
            return None
        raise MessageError(
            f"Invalid response message. Received {result.__class__.__name__}, expected NodeResponse"
        )


class NodeRemoveFromGroupRequest(PlugwiseRequest):
    """Remove node from group.

    Response message: TODO:
    """

    _identifier = b"0046"

    def __init__(
        self,
        send_fn: Callable[[PlugwiseRequest, bool], Awaitable[PlugwiseResponse | None]],
        mac: bytes,
        group_mac: str,
    ) -> None:
        """Initialize NodeRemoveFromGroupRequest message object."""
        super().__init__(send_fn, mac)
        group_mac_val = String(group_mac, length=16)
        self._args += [group_mac_val]


class NodeBroadcastGroupSwitchRequest(PlugwiseRequest):
    """Broadcast to group to switch.

    Response message: TODO:
    """

    _identifier = b"0047"

    def __init__(
        self,
        send_fn: Callable[[PlugwiseRequest, bool], Awaitable[PlugwiseResponse | None]],
        group_mac: bytes,
        switch_state: bool,
    ) -> None:
        """Initialize NodeBroadcastGroupSwitchRequest message object."""
        super().__init__(send_fn, group_mac)
        val = 1 if switch_state else 0
        self._args.append(Int(val, length=2))


class CircleEnergyLogsRequest(PlugwiseRequest):
    """Request energy usage counters stored a given memory address.

    Response message: CircleEnergyLogsResponse
    """

    _identifier = b"0048"
    _reply_identifier = b"0049"

    def __init__(
        self,
        send_fn: Callable[[PlugwiseRequest, bool], Awaitable[PlugwiseResponse | None]],
        mac: bytes,
        log_address: int,
    ) -> None:
        """Initialize CircleEnergyLogsRequest message object."""
        super().__init__(send_fn, mac)
        self._log_address = log_address
        self.priority = Priority.LOW
        self._args.append(LogAddr(log_address, 8))

    def __repr__(self) -> str:
        """Convert request into writable str."""
        return f"{super().__repr__()[:-1]}, log_address={self._log_address})"

    async def send(self) -> CircleEnergyLogsResponse | None:
        """Send request."""
        result = await self._send_request()
        if isinstance(result, CircleEnergyLogsResponse):
            return result
        if result is None:
            return None
        raise MessageError(
            f"Invalid response message. Received {result.__class__.__name__}, expected CircleEnergyLogsResponse"
        )


class CircleHandlesOffRequest(PlugwiseRequest):
    """?PWSetHandlesOffRequestV1_0.

    Response message: TODO
    """

    _identifier = b"004D"


class CircleHandlesOnRequest(PlugwiseRequest):
    """?PWSetHandlesOnRequestV1_0.

    Response message: ?
    """

    _identifier = b"004E"


class NodeSleepConfigRequest(PlugwiseRequest):
    """Configure timers for SED nodes to minimize battery usage.

    Description:
        Response message: NodeResponse with SLEEP_SET

    Args:
        send_fn: Send function
        mac: MAC address of the node
        awake_duration: Duration in seconds the SED will be awake for receiving commands
        sleep_for: Duration in minutes the SED will be in sleeping mode and not able to respond any command
        maintenance_interval: Interval in minutes the node will wake up and able to receive commands
        sync_clock: Enable/disable clock sync
        clock_interval: Duration in minutes the node synchronize its clock

    """

    _identifier = b"0050"

    # pylint: disable=too-many-arguments
    def __init__(  # noqa: PLR0913
        self,
        send_fn: Callable[[PlugwiseRequest, bool], Awaitable[PlugwiseResponse | None]],
        mac: bytes,
        awake_duration: int,
        maintenance_interval: int,
        sleep_duration: int,
        sync_clock: bool,
        clock_interval: int,
    ):
        """Initialize NodeSleepConfigRequest message object."""
        super().__init__(send_fn, mac)
        self.awake_duration_val = Int(awake_duration, length=2)
        self.sleep_duration_val = Int(sleep_duration, length=4)
        self.maintenance_interval_val = Int(maintenance_interval, length=4)
        val = 1 if sync_clock else 0
        self.clock_sync_val = Int(val, length=2)
        self.clock_interval_val = Int(clock_interval, length=4)
        self._args += [
            self.awake_duration_val,
            self.maintenance_interval_val,
            self.sleep_duration_val,
            self.clock_sync_val,
            self.clock_interval_val,
        ]

    async def send(self) -> NodeResponse | None:
        """Send request."""
        result = await self._send_request()
        _LOGGER.debug("NodeSleepConfigRequest result: %s", result)
        if isinstance(result, NodeResponse):
            return result
        if result is None:
            return None
        raise MessageError(
            f"Invalid response message. Received {result.__class__.__name__}, expected NodeResponse"
        )

    def __repr__(self) -> str:
        """Convert request into writable str."""
        return f"{super().__repr__()[:-1]}, awake_duration={self.awake_duration_val.value}, maintenance_interval={self.maintenance_interval_val.value}, sleep_duration={self.sleep_duration_val.value}, clock_interval={self.clock_interval_val.value}, clock_sync={self.clock_sync_val.value})"


class NodeSelfRemoveRequest(PlugwiseRequest):
    """TODO: Remove node?.

    <command number="0051" vnumber="1.0"
    implementation="Plugwise.IO.Commands.V20.PWSelfRemovalRequestV1_0">
      <arguments>
        <argument name="macId" length="16"/>
      </arguments>
    </command>
    """

    _identifier = b"0051"


class CircleMeasureIntervalRequest(PlugwiseRequest):
    """Configure the logging interval of energy measurement in minutes.

    FIXME: Make sure production interval is a multiply of consumption !!

    Response message: NodeResponse with ack-type POWER_LOG_INTERVAL_ACCEPTED
    """

    _identifier = b"0057"

    def __init__(
        self,
        send_fn: Callable[[PlugwiseRequest, bool], Awaitable[PlugwiseResponse | None]],
        mac: bytes,
        consumption: int,
        production: int,
    ):
        """Initialize CircleMeasureIntervalRequest message object."""
        super().__init__(send_fn, mac)
        self._args.append(Int(consumption, length=4))
        self._args.append(Int(production, length=4))

    async def send(self) -> NodeResponse | None:
        """Send request."""
        result = await self._send_request()
        if isinstance(result, NodeResponse):
            return result
        if result is None:
            return None
        raise MessageError(
            f"Invalid response message. Received {result.__class__.__name__}, expected NodeResponse"
        )


class NodeClearGroupMacRequest(PlugwiseRequest):
    """TODO: usage?.

    Response message: TODO
    """

    _identifier = b"0058"

    def __init__(
        self,
        send_fn: Callable[[PlugwiseRequest, bool], Awaitable[PlugwiseResponse | None]],
        mac: bytes,
        task_id: int,
    ) -> None:
        """Initialize NodeClearGroupMacRequest message object."""
        super().__init__(send_fn, mac)
        self._args.append(Int(task_id, length=2))


class CircleSetScheduleValueRequest(PlugwiseRequest):
    """Send chunk of On/Off/StandbyKiller Schedule to Circle(+).

    Response message: TODO:
    """

    _identifier = b"0059"

    def __init__(
        self,
        send_fn: Callable[[PlugwiseRequest, bool], Awaitable[PlugwiseResponse | None]],
        mac: bytes,
        val: int,
    ) -> None:
        """Initialize CircleSetScheduleValueRequest message object."""
        super().__init__(send_fn, mac)
        self._args.append(SInt(val, length=4))


class NodeFeaturesRequest(PlugwiseRequest):
    """Request feature set node supports.

    Response message: NodeFeaturesResponse
    """

    _identifier = b"005F"
    _reply_identifier = b"0060"

    def __init__(
        self,
        send_fn: Callable[[PlugwiseRequest, bool], Awaitable[PlugwiseResponse | None]],
        mac: bytes,
        val: int,
    ) -> None:
        """Initialize NodeFeaturesRequest message object."""
        super().__init__(send_fn, mac)
        self._args.append(SInt(val, length=4))

    async def send(self) -> NodeFeaturesResponse | None:
        """Send request."""
        result = await self._send_request()
        if isinstance(result, NodeFeaturesResponse):
            return result
        if result is None:
            return None
        raise MessageError(
            f"Invalid response message. Received {result.__class__.__name__}, expected NodeFeaturesResponse"
        )


class ScanConfigureRequest(PlugwiseRequest):
    """Configure a Scan node.

    reset_timer : Delay in minutes when signal is send
                  when no motion is detected. Minimum 1, max 255
    sensitivity : Sensitivity of Motion sensor
                  (High, Medium, Off)
    light       : Daylight override to only report motion
                  when light level is below calibrated level

    Response message: NodeAckResponse
    """

    _identifier = b"0101"
    _reply_identifier = b"0100"

    # pylint: disable=too-many-arguments
    def __init__(
        self,
        send_fn: Callable[[PlugwiseRequest, bool], Awaitable[PlugwiseResponse | None]],
        mac: bytes,
        reset_timer: int,
        sensitivity: int,
        light: bool,
    ):
        """Initialize ScanConfigureRequest message object."""
        super().__init__(send_fn, mac)
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

    async def send(self) -> NodeAckResponse | None:
        """Send request."""
        result = await self._send_request()
        if isinstance(result, NodeAckResponse):
            return result
        if result is None:
            return None
        raise MessageError(
            f"Invalid response message. Received {result.__class__.__name__}, expected NodeAckResponse"
        )


class ScanLightCalibrateRequest(PlugwiseRequest):
    """Calibrate light sensitivity.

    Response message: NodeAckResponse
    """

    _identifier = b"0102"
    _reply_identifier = b"0100"

    async def send(self) -> NodeAckResponse | None:
        """Send request."""
        result = await self._send_request()
        if isinstance(result, NodeAckResponse):
            return result
        if result is None:
            return None
        raise MessageError(
            f"Invalid response message. Received {result.__class__.__name__}, expected NodeAckResponse"
        )


class SenseReportIntervalRequest(PlugwiseRequest):
    """Sets the Sense temperature and humidity measurement report interval in minutes.

    Based on this interval, periodically a 'SenseReportResponse' message is sent by the Sense node

    Response message: NodeAckResponse
    """

    _identifier = b"0103"
    _reply_identifier = b"0100"

    def __init__(
        self,
        send_fn: Callable[[PlugwiseRequest, bool], Awaitable[PlugwiseResponse | None]],
        mac: bytes,
        interval: int,
    ):
        """Initialize ScanLightCalibrateRequest message object."""
        super().__init__(send_fn, mac)
        self._args.append(Int(interval, length=2))

    async def send(self) -> NodeAckResponse | None:
        """Send request."""
        result = await self._send_request()
        if isinstance(result, NodeAckResponse):
            return result
        if result is None:
            return None
        raise MessageError(
            f"Invalid response message. Received {result.__class__.__name__}, expected NodeAckResponse"
        )


class CircleRelayInitStateRequest(PlugwiseRequest):
    """Get or set initial relay state after power-up of Circle.

    Supported protocols : 2.6
    Response message    : CircleInitRelayStateResponse
    """

    _identifier = b"0138"
    _reply_identifier = b"0139"

    def __init__(
        self,
        send_fn: Callable[[PlugwiseRequest, bool], Awaitable[PlugwiseResponse | None]],
        mac: bytes,
        configure: bool,
        relay_state: bool,
    ) -> None:
        """Initialize CircleRelayInitStateRequest message object."""
        super().__init__(send_fn, mac)
        self.priority = Priority.LOW
        self.set_or_get = Int(1 if configure else 0, length=2)
        self.relay = Int(1 if relay_state else 0, length=2)
        self._args += [self.set_or_get, self.relay]

    async def send(self) -> CircleRelayInitStateResponse | None:
        """Send request."""
        result = await self._send_request()
        if isinstance(result, CircleRelayInitStateResponse):
            return result
        if result is None:
            return None
        raise MessageError(
            f"Invalid response message. Received {result.__class__.__name__}, expected CircleRelayInitStateResponse"
        )
