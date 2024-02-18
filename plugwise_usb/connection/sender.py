"""Send data to USB-Stick.

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

from ..constants import STICK_TIME_OUT
from ..exceptions import StickError
from ..messages.requests import PlugwiseRequest
from ..messages.responses import StickResponse, StickResponseType
from .receiver import StickReceiver

_LOGGER = logging.getLogger(__name__)


class StickSender:
    """Send request messages though USB Stick transport connection."""

    def __init__(
        self, stick_receiver: StickReceiver, transport: Transport
    ) -> None:
        """Initialize the Stick Sender class."""
        self._loop = get_running_loop()
        self._receiver = stick_receiver
        self._transport = transport
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
        """Send message to serial port of USB stick.

        Returns the updated request object. Raises StickError
        """
        await self._stick_lock.acquire()
        self._current_request = request

        if self._transport is None:
            raise StickError("USB-Stick transport missing.")

        self._stick_response: Future[bytes] = self._loop.create_future()

        serialized_data = request.serialize()
        request.subscribe_to_responses(
            self._receiver.subscribe_to_stick_responses,
            self._receiver.subscribe_to_node_responses,
        )

        _LOGGER.debug("Sending %s", request)
        # Write message to serial port buffer
        self._transport.write(serialized_data)
        request.add_send_attempt()
        request.start_response_timeout()

        # Wait for USB stick to accept request
        try:
            seq_id: bytes = await wait_for(
                self._stick_response, timeout=STICK_TIME_OUT
            )
        except TimeoutError:
            request.assign_error(
                BaseException(
                    StickError(
                        f"Failed to send {request.__class__.__name__} " +
                        "because USB-Stick did not respond " +
                        f"within {STICK_TIME_OUT} seconds."
                    )
                )
            )
        except BaseException as exc:  # pylint: disable=broad-exception-caught
            request.assign_error(exc)
        else:
            # Update request with session id
            _LOGGER.debug("Request %s assigned seq_id %s", request, str(seq_id))
            request.seq_id = seq_id
            self._open_requests[seq_id] = request
        finally:
            self._stick_response = None
            self._stick_lock.release()

        return request

    async def _process_stick_response(self, response: StickResponse) -> None:
        """Process stick response."""
        if (
            self._stick_response is None
            or self._stick_response.done()
        ):
            _LOGGER.debug("No open request for %s", str(response))
            return

        if response.ack_id != StickResponseType.ACCEPT:
            #
            # TODO
            # Verify if we actually do receive any non ACCEPT stick response as the first response
            # after submitting an request.
            #
            _LOGGER.warning("Received %s as POSSIBLE reply to %s", response, self._current_request)
            return
        _LOGGER.debug("Received %s as reply to %s", response, self._current_request)
        self._stick_response.set_result(response.seq_id)
        await self._stick_lock.acquire()
        if response.seq_id in self._open_requests:
            del self._open_requests[response.seq_id]
        else:
            return
        self._stick_lock.release()

    def stop(self) -> None:
        """Stop sender."""
        self._unsubscribe_stick_response()
