"""Receive data from USB-Stick.

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

from asyncio import Future, Protocol, Queue, Task, gather, get_running_loop, sleep, wait
from collections.abc import Awaitable, Callable
from concurrent import futures
import logging
from typing import Final

from serial_asyncio import SerialTransport

from ..api import StickEvent
from ..constants import MESSAGE_FOOTER, MESSAGE_HEADER
from ..exceptions import MessageError
from ..messages.responses import (
    PlugwiseResponse,
    StickResponse,
    StickResponseType,
    get_message_object,
)

_LOGGER = logging.getLogger(__name__)
STICK_RECEIVER_EVENTS = (
    StickEvent.CONNECTED,
    StickEvent.DISCONNECTED
)
CACHED_REQUESTS: Final = 50


async def delayed_run(coroutine: Callable, seconds: float):
    """Postpone a coroutine to be executed after given delay."""
    await sleep(seconds)
    await coroutine


class StickReceiver(Protocol):
    """Receive data from USB Stick connection and convert it into response messages."""

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
        self._reduce_logging = True
        self._receive_queue: Queue[PlugwiseResponse | None] = Queue()
        self._last_processed_messages: list[bytes] = []
        self._stick_future: futures.Future | None = None
        self._responses: dict[bytes, Callable[[PlugwiseResponse], None]] = {}
        self._stick_response_future: futures.Future | None = None
        self._msg_processing_task: Task | None = None
        # Subscribers
        self._stick_event_subscribers: dict[
            Callable[[], None],
            tuple[Callable[[StickEvent], Awaitable[None]], StickEvent | None]
        ] = {}

        self._stick_response_subscribers: dict[
            Callable[[], None],
            tuple[
                Callable[[StickResponse], Awaitable[None]],
                bytes | None
            ]
        ] = {}

        self._node_response_subscribers: dict[
            Callable[[], None],
            tuple[
                Callable[[PlugwiseResponse], Awaitable[bool]], bytes | None,
                tuple[bytes] | None,
            ]
        ] = {}

    def connection_lost(self, exc: Exception | None = None) -> None:
        """Call when port was closed expectedly or unexpectedly."""
        _LOGGER.debug("Connection lost")
        if exc is not None:
            _LOGGER.warning("Connection lost %s", exc)
        self._loop.create_task(self.close())
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

    @property
    def reduce_logging(self) -> bool:
        """Return if logging must reduced."""
        return self._reduce_logging

    @reduce_logging.setter
    def reduce_logging(self, reduce_logging: bool) -> None:
        """Reduce logging."""
        self._reduce_logging = reduce_logging

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
        await self._stop_running_tasks()

    async def _stop_running_tasks(self) -> None:
        """Cancel and stop any running task."""
        await self._receive_queue.put(None)
        if self._msg_processing_task is not None and not self._msg_processing_task.done():
            self._msg_processing_task.cancel()
            await wait([self._msg_processing_task])

    def data_received(self, data: bytes) -> None:
        """Receive data from USB-Stick connection.

        This function is called by inherited asyncio.Protocol class
        """
        self._buffer += data
        if MESSAGE_FOOTER in self._buffer:
            msgs = self._buffer.split(MESSAGE_FOOTER)
            for msg in msgs[:-1]:
                if (response := self.extract_message_from_line_buffer(msg)):
                    self._put_message_in_receiver_queue(response)
            if len(msgs) > 4:
                _LOGGER.debug("Reading %d messages at once from USB-Stick", len(msgs))
            self._buffer = msgs[-1]  # whatever was left over
            if self._buffer == b"\x83":
                self._buffer = b""

    def _put_message_in_receiver_queue(self, response: PlugwiseResponse) -> None:
        """Put message in queue."""
        self._receive_queue.put_nowait(response)
        if self._msg_processing_task is None or self._msg_processing_task.done():
            self._msg_processing_task = self._loop.create_task(
                self._msg_queue_processing_function(),
                name="Process received messages"
            )

    def extract_message_from_line_buffer(self, msg: bytes) -> PlugwiseResponse:
        """Extract message from buffer."""
        # Lookup header of message, there are stray \x83
        if (_header_index := msg.find(MESSAGE_HEADER)) == -1:
            return False
        msg = msg[_header_index:]
        # Detect response message type
        identifier = msg[4:8]
        seq_id = msg[8:12]
        msg_length = len(msg)
        if (response := get_message_object(identifier, msg_length, seq_id)) is None:
            _raw_msg_data = msg[2:][: msg_length - 4]
            _LOGGER.warning("Drop unknown message type %s", str(_raw_msg_data))
            return None

        # Populate response message object with data
        try:
            response.deserialize(msg, has_footer=False)
        except MessageError as err:
            _LOGGER.warning(err)
            return None

        _LOGGER.debug("Reading '%s' from USB-Stick", response)
        return response

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
        """Process queue items."""
        while self.is_connected:
            response: PlugwiseResponse | None = await self._receive_queue.get()
            _LOGGER.debug("Processing started for %s", response)
            if isinstance(response, StickResponse):
                await self._notify_stick_response_subscribers(response)
            elif response is None:
                self._receive_queue.task_done()
                return
            else:
                await self._notify_node_response_subscribers(response)
            _LOGGER.debug("Processing finished for %s", response)
            self._receive_queue.task_done()
            await sleep(0)

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
        """Call callback for stick event subscribers."""
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
        seq_id: bytes | None = None,
        response_type: StickResponseType | None = None
    ) -> Callable[[], None]:
        """Subscribe to response messages from stick."""
        def remove_subscription() -> None:
            """Remove update listener."""
            self._stick_response_subscribers.pop(remove_subscription)

        self._stick_response_subscribers[
            remove_subscription
        ] = callback, seq_id, response_type
        return remove_subscription

    async def _notify_stick_response_subscribers(
        self, stick_response: StickResponse
    ) -> None:
        """Call callback for all stick response message subscribers."""
        for callback, seq_id, response_type in list(self._stick_response_subscribers.values()):
            if seq_id is not None:
                if seq_id != stick_response.seq_id:
                    continue
            if response_type is not None and response_type != stick_response.response_type:
                continue
            await callback(stick_response)

    def subscribe_to_node_responses(
        self,
        node_response_callback: Callable[[PlugwiseResponse], Awaitable[bool]],
        mac: bytes | None = None,
        message_ids: tuple[bytes] | None = None,
        seq_id: bytes | None = None,
    ) -> Callable[[], None]:
        """Subscribe a awaitable callback to be called when a specific message is received.

        Returns function to unsubscribe.
        """
        def remove_listener() -> None:
            """Remove update listener."""
            self._node_response_subscribers.pop(remove_listener)

        self._node_response_subscribers[
            remove_listener
        ] = (node_response_callback, mac, message_ids, seq_id)
        return remove_listener

    async def _notify_node_response_subscribers(self, node_response: PlugwiseResponse) -> None:
        """Call callback for all node response message subscribers."""
        processed = False
        for callback, mac, message_ids, seq_id in list(
            self._node_response_subscribers.values()
        ):
            if mac is not None and mac != node_response.mac:
                continue
            if message_ids is not None and node_response.identifier not in message_ids:
                continue
            if seq_id is not None and seq_id != node_response.seq_id:
                continue
            processed = True
            try:
                await callback(node_response)
            except Exception as err:
                _LOGGER.error("ERROR AT _notify_node_response_subscribers: %s", err)

        if processed:
            self._last_processed_messages.append(node_response.seq_id)
            if len(self._last_processed_messages) > CACHED_REQUESTS:
                self._last_processed_messages = self._last_processed_messages[:-CACHED_REQUESTS]
            return

        if node_response.seq_id in self._last_processed_messages:
            _LOGGER.debug("Drop duplicate %s", node_response)
            return

        if node_response.retries > 10:
            if self._reduce_logging:
                _LOGGER.debug(
                    "No subscriber to handle %s, seq_id=%s from %s after 10 retries",
                    node_response.__class__.__name__,
                    node_response.seq_id,
                    node_response.mac_decoded,
                )
            else:
                _LOGGER.warning(
                    "No subscriber to handle %s, seq_id=%s from %s after 10 retries",
                    node_response.__class__.__name__,
                    node_response.seq_id,
                    node_response.mac_decoded,
                )
            return
        node_response.retries += 1
        await sleep(0.01)
        self._put_message_in_receiver_queue(node_response)
