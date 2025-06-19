"""Manage the communication flow through the USB-Stick towards the Plugwise (propriety) Zigbee like network."""

from __future__ import annotations

from asyncio import Future, gather, get_event_loop, wait_for
from collections.abc import Awaitable, Callable, Coroutine
import logging
from typing import Any

from serial import EIGHTBITS, PARITY_NONE, STOPBITS_ONE, SerialException
from serial_asyncio_fast import SerialTransport, create_serial_connection

from ..api import StickEvent
from ..exceptions import StickError
from ..messages.requests import PlugwiseRequest
from ..messages.responses import PlugwiseResponse
from .receiver import StickReceiver
from .sender import StickSender

_LOGGER = logging.getLogger(__name__)


class StickConnectionManager:
    """Manage the message flow to and from USB-Stick."""

    def __init__(self) -> None:
        """Initialize Stick controller."""
        self._sender: StickSender | None = None
        self._receiver: StickReceiver | None = None
        self._serial_transport: SerialTransport | None = None
        self._port = "<not defined>"
        self._connected: bool = False
        self._stick_event_subscribers: dict[
            Callable[[], None],
            tuple[Callable[[StickEvent], Awaitable[None]], tuple[StickEvent, ...]],
        ] = {}
        self._unsubscribe_stick_events: Callable[[], None] | None = None

    @property
    def queue_depth(self) -> int:
        """Return estimated size of pending responses."""
        return self._sender.processed_messages - self._receiver.processed_messages

    def correct_received_messages(self, correction: int) -> None:
        """Correct received messages count."""
        self._receiver.correct_processed_messages(correction)

    @property
    def serial_path(self) -> str:
        """Return current port."""
        return self._port

    @property
    def is_connected(self) -> bool:
        """Returns True if UBS-Stick connection is active."""
        if not self._connected:
            return False
        if self._receiver is None:
            return False
        return self._receiver.is_connected

    def _subscribe_to_stick_events(self) -> None:
        """Subscribe to handle stick events by manager."""
        if not self.is_connected or self._receiver is None:
            raise StickError("Unable to subscribe to events")
        if self._unsubscribe_stick_events is None:
            self._unsubscribe_stick_events = self._receiver.subscribe_to_stick_events(
                self._handle_stick_event,
                (StickEvent.CONNECTED, StickEvent.DISCONNECTED),
            )

    async def _handle_stick_event(
        self,
        event: StickEvent,
    ) -> None:
        """Call callback for stick event subscribers."""
        if len(self._stick_event_subscribers) == 0:
            return
        callback_list: list[Awaitable[None]] = []
        for callback, stick_events in self._stick_event_subscribers.values():
            if event in stick_events:
                callback_list.append(callback(event))
        if len(callback_list) > 0:
            await gather(*callback_list)

    def subscribe_to_stick_events(
        self,
        stick_event_callback: Callable[[StickEvent], Awaitable[None]],
        events: tuple[StickEvent, ...],
    ) -> Callable[[], None]:
        """Subscribe callback when specified StickEvent occurs.

        Returns the function to be called to unsubscribe later.
        """

        def remove_subscription() -> None:
            """Remove stick event subscription."""
            self._stick_event_subscribers.pop(remove_subscription)

        self._stick_event_subscribers[remove_subscription] = (
            stick_event_callback,
            events,
        )
        return remove_subscription

    async def subscribe_to_messages(
        self,
        node_response_callback: Callable[[PlugwiseResponse], Coroutine[Any, Any, bool]],
        mac: bytes | None = None,
        message_ids: tuple[bytes] | None = None,
        seq_id: bytes | None = None,
    ) -> Callable[[], None]:
        """Subscribe a awaitable callback to be called when a specific message is received.

        Returns function to unsubscribe.
        """
        if self._receiver is None or not self._receiver.is_connected:
            raise StickError(
                "Unable to subscribe to node response when receiver " + "is not loaded"
            )
        return await self._receiver.subscribe_to_node_responses(
            node_response_callback, mac, message_ids, seq_id
        )

    async def setup_connection_to_stick(self, serial_path: str) -> None:
        """Create serial connection to USB-stick."""
        if self._connected:
            raise StickError("Cannot setup connection, already connected")
        loop = get_event_loop()
        connected_future: Future[bool] = Future()
        self._receiver = StickReceiver(connected_future)
        self._port = serial_path

        try:
            (
                self._serial_transport,
                self._receiver,
            ) = await wait_for(
                create_serial_connection(
                    loop,
                    lambda: self._receiver,
                    url=serial_path,
                    baudrate=115200,
                    bytesize=EIGHTBITS,
                    stopbits=STOPBITS_ONE,
                    parity=PARITY_NONE,
                    xonxoff=False,
                ),
                timeout=5,
            )
        except SerialException as err:
            raise StickError(
                f"Failed to open serial connection to {serial_path}"
            ) from err
        except TimeoutError as err:
            raise StickError(
                f"Failed to open serial connection to {serial_path}"
            ) from err

        if self._receiver is None:
            raise StickError("Protocol is not loaded")
        self._sender = StickSender(self._receiver, self._serial_transport)
        await self._sender.start()
        await connected_future
        if connected_future.result():
            await self._handle_stick_event(StickEvent.CONNECTED)
        self._connected = True
        self._subscribe_to_stick_events()

    async def write_to_stick(self, request: PlugwiseRequest) -> None:
        """Write message to USB stick."""
        _LOGGER.debug("Write to USB-stick: %s", request)
        if not request.resend:
            raise StickError(
                f"Failed to send {request.__class__.__name__} "
                + f"to node {request.mac_decoded}, maximum number "
                + f"of retries ({request.max_retries}) has been reached"
            )
        if self._sender is None:
            raise StickError(
                f"Failed to send {request.__class__.__name__}"
                + "because USB-Stick connection is not setup"
            )
        await self._sender.write_request_to_port(request)

    async def disconnect_from_stick(self) -> None:
        """Disconnect from USB-Stick."""
        _LOGGER.debug("Disconnecting manager")
        if self._unsubscribe_stick_events is not None:
            self._unsubscribe_stick_events()
            self._unsubscribe_stick_events = None
        self._connected = False
        if self._sender is not None:
            self._sender.stop()
        if self._receiver is not None:
            await self._receiver.close()
            self._receiver = None
        _LOGGER.debug("Manager disconnected")
