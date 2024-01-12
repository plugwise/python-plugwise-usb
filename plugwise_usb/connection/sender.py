"""
Serialize request message and pass data stream to legacy Plugwise USB-Stick
Wait for stick to respond.
When request is accepted by USB-Stick, return the Sequence ID of the session.

process flow

1. Send(request)
1. wait for lock
1. convert (serialize) request message into bytes
1. send data to serial port
1. wait for stick reply (accept, timeout, failed)
1. when accept, return sequence id for response message of node

"""
from __future__ import annotations

from asyncio import Future, Lock, Transport, get_running_loop, wait_for
import logging

from .receiver import StickReceiver
from ..constants import STICK_TIME_OUT
from ..exceptions import StickError, StickFailed, StickTimeout
from ..messages.responses import StickResponse, StickResponseType
from ..messages.requests import PlugwiseRequest

_LOGGER = logging.getLogger(__name__)


class StickSender():
    """Send request messages though USB Stick transport connection."""

    def __init__(
        self, stick_receiver: StickReceiver, transport: Transport
    ) -> None:
        """Initialize the Stick Sender class"""
        self._loop = get_running_loop()
        self._receiver = stick_receiver
        self._transport = transport
        self._expected_seq_id: bytes = b"FFFF"
        self._stick_response: Future[bytes] | None = None
        self._stick_lock = Lock()
        self._current_request: None | PlugwiseRequest = None
        self._open_requests: dict[bytes, PlugwiseRequest] = {}
        self._unsubscribe_stick_response = (
            self._receiver.subscribe_to_stick_responses(
                self._process_stick_response
            )
        )

    async def write_request_to_port(
        self, request: PlugwiseRequest
    ) -> PlugwiseRequest:
        """
        Send message to serial port of USB stick.
        Returns the updated request object.
        Raises StickError
        """
        await self._stick_lock.acquire()
        self._current_request = request

        if self._transport is None:
            raise StickError("USB-Stick transport missing.")

        self._stick_response: Future[bytes] = self._loop.create_future()

        serialized_data = request.serialize()
        request.subscribe_to_responses(
            self._receiver.subscribe_to_node_responses
        )

        # Write message to serial port buffer
        self._transport.write(serialized_data)
        request.add_send_attempt()

        # Wait for USB stick to accept request
        try:
            seq_id: bytes = await wait_for(
                self._stick_response, timeout=STICK_TIME_OUT
            )
        except TimeoutError as exc:
            raise StickError(
                f"Failed to send {request.__class__.__name__} because " +
                f"USB-Stick did not respond within {STICK_TIME_OUT} seconds."
            ) from exc
        else:
            # Update request with session id
            request.seq_id = seq_id
            self._expected_seq_id = self._next_seq_id(self._expected_seq_id)
        finally:
            self._stick_response = None
            self._stick_lock.release()

        return request

    async def _process_stick_response(self, response: StickResponse) -> None:
        """Process stick response."""
        if self._expected_seq_id == b"FFFF":
            # First response, so accept current sequence id
            self._expected_seq_id = response.seq_id

        if self._expected_seq_id != response.seq_id:
            _LOGGER.warning(
                "Stick response (ack_id=%s) received with invalid seq id, " +
                "expected %s received %s",
                str(response.ack_id),
                str(self._expected_seq_id),
                str(response.seq_id),
            )
            return

        if (
            self._stick_response is None
            or self._stick_response.done()
        ):
            _LOGGER.warning(
                "Unexpected stick response (ack_id=%s, seq_id=%s) received",
                str(response.ack_id),
                str(response.seq_id),
            )
            return

        if response.ack_id == StickResponseType.ACCEPT:
            self._stick_response.set_result(response.seq_id)
        elif response.ack_id == StickResponseType.FAILED:
            self._stick_response.set_exception(
                BaseException(
                    StickFailed(
                        "USB-Stick failed to submit "
                        + f"{self._current_request.__class__.__name__} to "
                        + f"node '{self._current_request.mac_decoded}'."
                    )
                )
            )
        elif response.ack_id == StickResponseType.TIMEOUT:
            self._stick_response.set_exception(
                BaseException(
                    StickTimeout(
                        "USB-Stick timeout to submit "
                        + f"{self._current_request.__class__.__name__} to "
                        + f"node '{self._current_request.mac_decoded}'."
                    )
                )
            )

    def stop(self) -> None:
        """Stop sender"""
        self._unsubscribe_stick_response()

    @staticmethod
    def _next_seq_id(seq_id: bytes) -> bytes:
        """Increment sequence id by one, return 4 bytes."""
        # Max seq_id = b'FFFB'
        # b'FFFC' reserved for <unknown> message
        # b'FFFD' reserved for 'NodeJoinAckResponse' message
        # b'FFFE' reserved for 'NodeAwakeResponse' message
        # b'FFFF' reserved for 'NodeSwitchGroupResponse' message
        if seq_id == b"FFFF":
            return b"FFFF"
        if (temp_int := int(seq_id, 16) + 1) >= 65532:
            temp_int = 0
        temp_str = str(hex(temp_int)).lstrip("0x").upper()
        while len(temp_str) < 4:
            temp_str = "0" + temp_str
        return temp_str.encode()
