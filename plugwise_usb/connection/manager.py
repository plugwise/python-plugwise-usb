"""
The 'connection controller' manage the communication flow through the USB-Stick
towards the Plugwise (propriety) Zigbee like network.
"""
from __future__ import annotations

from asyncio import Future, get_event_loop, wait_for, sleep
from collections.abc import Awaitable, Callable
import logging
from typing import Any

from serial import EIGHTBITS, PARITY_NONE, STOPBITS_ONE
from serial import SerialException
import serial_asyncio

from .sender import StickSender
from .receiver import STICK_RECEIVER_EVENTS, StickReceiver
from ..api import StickEvent
from ..exceptions import StickError
from ..messages.requests import PlugwiseRequest
from ..messages.responses import PlugwiseResponse, StickResponse

_LOGGER = logging.getLogger(__name__)


class StickConnectionManager():
    """Manage the message flow to and from USB-Stick."""

    def __init__(self) -> None:
        """Initialize Stick controller."""
        self._sender: StickSender | None = None
        self._receiver: StickReceiver | None = None
        self._port = "<not defined>"
        self._connected: bool = False

        self._stick_event_subscribers: dict[
            Callable[[], None],
            tuple[Callable[[StickEvent], Awaitable[None]], StickEvent | None]
        ] = {}

    @property
    def serial_path(self) -> str:
        """Return current port"""
        return self._port

    @property
    def is_connected(self) -> bool:
        """Returns True if UBS-Stick connection is active."""
        if not self._connected:
            return False
        if self._receiver is None:
            return False
        return self._receiver.is_connected

    def subscribe_to_stick_events(
        self,
        stick_event_callback: Callable[[StickEvent], Awaitable[None]],
        event: StickEvent | None,
    ) -> Callable[[], None]:
        """
        Subscribe callback when specified StickEvent occurs.
        Returns the function to be called to unsubscribe later.
        """
        def remove_subscription() -> None:
            """Remove stick event subscription."""
            self._stick_event_subscribers.pop(remove_subscription)

        if event in STICK_RECEIVER_EVENTS:
            return self._receiver.subscribe_to_stick_events(
                stick_event_callback, event
            )
        self._stick_event_subscribers[
            remove_subscription
        ] = (stick_event_callback, event)
        return remove_subscription

    def subscribe_to_stick_replies(
        self,
        callback: Callable[
            [StickResponse], Awaitable[None]
        ],
    ) -> Callable[[], None]:
        """Subscribe to response messages from stick."""
        if self._receiver is None or not self._receiver.is_connected:
            raise StickError(
                "Unable to subscribe to stick response when receiver " +
                "is not loaded"
            )
        return self._receiver.subscribe_to_stick_responses(callback)

    def subscribe_to_node_responses(
        self,
        node_response_callback: Callable[[PlugwiseResponse], Awaitable[None]],
        mac: bytes | None = None,
        identifiers: tuple[bytes] | None = None,
    ) -> Callable[[], None]:
        """
        Subscribe to response messages from node(s).
        Returns callable function to unsubscribe
        """
        if self._receiver is None or not self._receiver.is_connected:
            raise StickError(
                "Unable to subscribe to node response when receiver " +
                "is not loaded"
            )
        return self._receiver.subscribe_to_node_responses(
            node_response_callback, mac, identifiers
        )

    async def setup_connection_to_stick(
        self, serial_path: str
    ) -> None:
        """Setup serial connection to USB-stick."""
        if self._connected:
            raise StickError("Cannot setup connection, already connected")
        loop = get_event_loop()
        connected_future: Future[Any] = Future()
        self._receiver = StickReceiver(connected_future)
        self._port = serial_path

        try:
            (
                self._sender,
                self._receiver,
            ) = await wait_for(
                serial_asyncio.create_serial_connection(
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
        finally:
            connected_future.cancel()
        await sleep(0)
        await wait_for(connected_future, 5)
        self._connected = True
        if self._receiver is None:
            raise StickError("Protocol is not loaded")

    async def write_to_stick(
        self, request: PlugwiseRequest
    ) -> PlugwiseRequest:
        """
        Write message to USB stick.
        Returns the updated request object.
        """
        if not request.resend:
            raise StickError(
                f"Failed to send {request.__class__.__name__} " +
                f"to node {request.mac_decoded}, maximum number " +
                f"of retries ({request.max_retries}) has been reached"
            )
        if self._sender is None:
            raise StickError(
                f"Failed to send {request.__class__.__name__}" +
                "because USB-Stick connection is not setup"
            )
        return await self._sender.write_request_to_port(request)

    async def disconnect_from_stick(self) -> None:
        """Disconnect from USB-Stick."""
        _LOGGER.debug("Disconnecting manager")
        self._connected = False
        if self._receiver is not None:
            await self._receiver.close()
            self._receiver = None
        _LOGGER.debug("Manager disconnected")
