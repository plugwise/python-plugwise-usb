"""
Protocol receiver

Process incoming data stream from the Plugwise USB-Stick and
convert it into response messages.

Responsible to

  1. Collect and buffer raw data received from Stick: data_received()
  2. Convert raw data into response message: parse_data()
  3. Forward response message to the message subscribers

and publish detected connection status changes

  1. Notify status subscribers to connection state changes

"""

from __future__ import annotations
from asyncio import (
    Future,
    create_task,
    gather,
    Protocol,
    Queue,
    get_running_loop,
    sleep,
)
from serial_asyncio import SerialTransport
from collections.abc import Awaitable, Callable
from concurrent import futures
import logging
from ..api import StickEvent
from ..constants import MESSAGE_FOOTER, MESSAGE_HEADER
from ..exceptions import MessageError
from ..messages.responses import (
    PlugwiseResponse,
    StickResponse,
    get_message_object,
)

_LOGGER = logging.getLogger(__name__)
STICK_RECEIVER_EVENTS = (
    StickEvent.CONNECTED,
    StickEvent.DISCONNECTED
)


async def delayed_run(coroutine: Callable, seconds: float):
    """Postpone a coroutine to be executed after given delay"""
    await sleep(seconds)
    await coroutine


class StickReceiver(Protocol):
    """
    Receive data from USB Stick connection and
    convert it into response messages.
    """

    def __init__(
        self,
        connected_future: Future | None = None,
    ) -> None:
        """Initialize instance of the USB Stick connection."""
        super().__init__()
        self._loop = get_running_loop()
        self._connected_future = connected_future
        self._transport: SerialTransport | None = None
        self._buffer: bytes = bytes([])
        self._connection_state = False
        self._request_queue = Queue()
        self._stick_future: futures.Future | None = None
        self._responses: dict[bytes, Callable[[PlugwiseResponse], None]] = {}
        self._stick_response_future: futures.Future | None = None
        # Subscribers
        self._stick_event_subscribers: dict[
            Callable[[], None],
            tuple[Callable[[StickEvent], Awaitable[None]], StickEvent | None]
        ] = {}

        self._stick_response_subscribers: dict[
            Callable[[], None],
            Callable[[StickResponse], Awaitable[None]]
        ] = {}

        self._node_response_subscribers: dict[
            Callable[[], None],
            tuple[
                Callable[[PlugwiseResponse], Awaitable[None]], bytes | None,
                tuple[bytes] | None,
            ]
        ] = {}

    def connection_lost(self, exc: Exception | None = None) -> None:
        """Call when port was closed expectedly or unexpectedly."""
        _LOGGER.debug("Connection lost")
        if (
            self._connected_future is not None
            and not self._connected_future.done()
        ):
            if exc is None:
                self._connected_future.set_result(True)
            else:
                self._connected_future.set_exception(exc)
        if len(self._stick_event_subscribers) > 0:
            self._loop.create_task(
                self._notify_stick_event_subscribers(StickEvent.DISCONNECTED)
            )

        self._transport = None
        self._connection_state = False

    @property
    def is_connected(self) -> bool:
        """Return current connection state of the USB-Stick."""
        return self._connection_state

    def connection_made(self, transport: SerialTransport) -> None:
        """Call when the serial connection to USB-Stick is established."""
        _LOGGER.debug("Connection made")
        self._transport = transport
        if (
            self._connected_future is not None
            and not self._connected_future.done()
        ):
            self._connected_future.set_result(True)
        self._connection_state = True
        if len(self._stick_event_subscribers) > 0:
            self._loop.create_task(
                self._notify_stick_event_subscribers(StickEvent.CONNECTED)
            )

    async def close(self) -> None:
        """Close connection."""
        if self._transport is None:
            return
        if self._stick_future is not None and not self._stick_future.done():
            self._stick_future.cancel()

        self._transport.close()

    def data_received(self, data: bytes) -> None:
        """Receive data from USB-Stick connection.

        This function is called by inherited asyncio.Protocol class
        """
        _LOGGER.debug("USB stick received [%s]", data)
        self._buffer += data
        if len(self._buffer) < 8:
            return
        while self.extract_message_from_buffer():
            pass

    def extract_message_from_buffer(self) -> bool:
        """Parse data in buffer and extract any message.

        When buffer does not contain any message return False.
        """
        # Lookup header of message
        if (_header_index := self._buffer.find(MESSAGE_HEADER)) == -1:
            return False
        self._buffer = self._buffer[_header_index:]

        # Lookup footer of message
        if (_footer_index := self._buffer.find(MESSAGE_FOOTER)) == -1:
            return False

        # Detect response message type
        _empty_message = get_message_object(
            self._buffer[4:8], _footer_index, self._buffer[8:12]
        )
        if _empty_message is None:
            _raw_msg_data = self._buffer[2:][: _footer_index - 4]
            self._buffer = self._buffer[_footer_index:]
            _LOGGER.warning("Drop unknown message type %s", str(_raw_msg_data))
            return True

        # Populate response message object with data
        response: PlugwiseResponse | None = None
        response = self._populate_message(
            _empty_message, self._buffer[: _footer_index + 2]
        )
        _LOGGER.debug('USB Got %s', response)
        # Parse remaining buffer
        self._reset_buffer(self._buffer[_footer_index:])

        if response is not None:
            self._request_queue.put_nowait(response)

        if len(self._buffer) >= 8:
            self.extract_message_from_buffer()
        else:
            self._loop.create_task(
                self._msg_queue_processing_function()
            )
        return False

    def _populate_message(
        self, message: PlugwiseResponse, data: bytes
    ) -> PlugwiseResponse | None:
        """Return plugwise response message based on data."""
        try:
            message.deserialize(data)
        except MessageError as err:
            _LOGGER.warning(err)
            return None
        return message

    async def _msg_queue_processing_function(self):
        while self._request_queue.qsize() > 0:
            response: PlugwiseResponse | None = await self._request_queue.get()
            _LOGGER.debug("Processing %s", response)
            if isinstance(response, StickResponse):
                await self._notify_stick_response_subscribers(response)
            else:
                await self._notify_node_response_subscribers(response)
            self._request_queue.task_done()

    def _reset_buffer(self, new_buffer: bytes) -> None:
        if new_buffer[:2] == MESSAGE_FOOTER:
            new_buffer = new_buffer[2:]
        if new_buffer == b"\x83":
            # Skip additional byte sometimes appended after footer
            new_buffer = bytes([])
        self._buffer = new_buffer

    def subscribe_to_stick_events(
        self,
        stick_event_callback: Callable[[StickEvent], Awaitable[None]],
        events: tuple[StickEvent],
    ) -> Callable[[], None]:
        """Subscribe callback when specified StickEvent occurs.

        Returns the function to be called to unsubscribe later.
        """
        def remove_subscription() -> None:
            """Remove stick event subscription."""
            self._stick_event_subscribers.pop(remove_subscription)

        self._stick_event_subscribers[
            remove_subscription
        ] = (stick_event_callback, events)
        return remove_subscription

    async def _notify_stick_event_subscribers(
        self,
        event: StickEvent,
    ) -> None:
        """Call callback for stick event subscribers"""
        callback_list: list[Callable] = []
        for callback, filtered_events in (
            self._stick_event_subscribers.values()
        ):
            if event in filtered_events:
                callback_list.append(callback(event))
        if len(callback_list) > 0:
            await gather(*callback_list)

    def subscribe_to_stick_responses(
        self,
        callback: Callable[[StickResponse], Awaitable[None]],
    ) -> Callable[[], None]:
        """Subscribe to response messages from stick."""
        def remove_subscription() -> None:
            """Remove update listener."""
            self._stick_response_subscribers.pop(remove_subscription)

        self._stick_response_subscribers[
            remove_subscription
        ] = callback
        return remove_subscription

    async def _notify_stick_response_subscribers(
        self, stick_response: StickResponse
    ) -> None:
        """Call callback for all stick response message subscribers."""
        for callback in self._stick_response_subscribers.values():
            await callback(stick_response)

    def subscribe_to_node_responses(
        self,
        node_response_callback: Callable[[PlugwiseResponse], Awaitable[None]],
        mac: bytes | None = None,
        message_ids: tuple[bytes] | None = None,
    ) -> Callable[[], None]:
        """
        Subscribe a awaitable callback to be called when a specific
        message is received.
        Returns function to unsubscribe.
        """
        def remove_listener() -> None:
            """Remove update listener."""
            self._node_response_subscribers.pop(remove_listener)

        self._node_response_subscribers[
            remove_listener
        ] = (node_response_callback, mac, message_ids)
        return remove_listener

    async def _notify_node_response_subscribers(
        self, node_response: PlugwiseResponse
    ) -> None:
        """Call callback for all node response message subscribers"""
        callback_list: list[Callable] = []
        for callback, mac, message_ids in list(
            self._node_response_subscribers.values()
        ):
            if mac is not None:
                if mac != node_response.mac:
                    continue
            if message_ids is not None:
                if node_response.identifier not in message_ids:
                    continue
            callback_list.append(callback(node_response))

        if len(callback_list) > 0:
            await gather(*callback_list)
            return

        # No subscription for response, retry in 0.5 sec.
        node_response.notify_retries += 1
        if node_response.notify_retries > 10:
            _LOGGER.warning(
                "No subscriber to handle %s from %s",
                node_response.__class__.__name__,
                node_response.mac_decoded,
            )
            return
        create_task(
            delayed_run(
                self._notify_node_response_subscribers(
                    node_response
                ),
                0.5,
            )
        )
