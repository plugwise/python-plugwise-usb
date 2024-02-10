"""Manage the connection and communication flow through the USB-Stick."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from concurrent import futures
import logging

from ..api import StickEvent
from ..exceptions import NodeError, StickError
from ..messages.requests import PlugwiseRequest, StickInitRequest
from ..messages.responses import PlugwiseResponse, StickInitResponse
from .manager import StickConnectionManager
from .queue import StickQueue

_LOGGER = logging.getLogger(__name__)


class StickController:
    """Manage the connection and communication towards USB-Stick."""

    def __init__(self) -> None:
        """Initialize Stick controller."""
        self._manager = StickConnectionManager()
        self._queue = StickQueue()
        self._unsubscribe_stick_event: Callable[[], None] | None = None

        self._init_sequence_id: bytes | None = None
        self._init_future: futures.Future | None = None

        self._is_initialized = False
        self._mac_stick: str | None = None
        self._mac_nc: str | None = None
        self._network_id: int | None = None
        self._network_online = False

    @property
    def is_initialized(self) -> bool:
        """Returns True if UBS-Stick connection is active and initialized."""
        if not self._manager.is_connected:
            return False
        return self._is_initialized

    @property
    def is_connected(self) -> bool:
        """Return connection state from connection manager."""
        return self._manager.is_connected

    @property
    def mac_stick(self) -> str:
        """MAC address of USB-Stick. Raises StickError when not connected."""
        if not self._manager.is_connected or self._mac_stick is None:
            raise StickError(
                "No mac address available. " +
                "Connect and initialize USB-Stick first."
            )
        return self._mac_stick

    @property
    def mac_coordinator(self) -> str:
        """Return MAC address of the Zigbee network coordinator (Circle+).

        Raises StickError when not connected.
        """
        if not self._manager.is_connected or self._mac_nc is None:
            raise StickError(
                "No mac address available. Connect and initialize USB-Stick first."
            )
        return self._mac_nc

    @property
    def network_id(self) -> int:
        """Returns the Zigbee network ID. Raises StickError when not connected."""
        if not self._manager.is_connected or self._network_id is None:
            raise StickError(
                "No network ID available. " +
                "Connect and initialize USB-Stick first."
            )
        return self._network_id

    @property
    def network_online(self) -> bool:
        """Return the network state."""
        if not self._manager.is_connected:
            raise StickError(
                "Network status not available. " +
                "Connect and initialize USB-Stick first."
            )
        return self._network_online

    async def connect_to_stick(self, serial_path: str) -> None:
        """Connect to USB stick."""
        if self._manager.is_connected:
            raise StickError("Already connected")
        await self._manager.setup_connection_to_stick(serial_path)
        if self._unsubscribe_stick_event is None:
            self._unsubscribe_stick_event = (
                self._manager.subscribe_to_stick_events(
                    self._handle_stick_event,
                    (StickEvent.CONNECTED, StickEvent.DISCONNECTED),
                )
            )
        self._queue.start(self._manager)

    def subscribe_to_stick_events(
        self,
        stick_event_callback: Callable[[StickEvent], Awaitable[None]],
        events: tuple[StickEvent],
    ) -> Callable[[], None]:
        """Subscribe callback when specified StickEvent occurs.

        Returns the function to be called to unsubscribe later.
        """
        if self._manager is None:
            raise StickError("Connect to stick before subscribing to events")
        return self._manager.subscribe_to_stick_events(
            stick_event_callback,
            events,
        )

    def subscribe_to_node_responses(
        self,
        node_response_callback: Callable[[PlugwiseResponse], Awaitable[None]],
        mac: bytes | None = None,
        message_ids: tuple[bytes] | None = None,
    ) -> Callable[[], None]:
        """Subscribe a awaitable callback to be called when a specific message is received.

        Returns function to unsubscribe.
        """

        return self._manager.subscribe_to_node_responses(
            node_response_callback,
            mac,
            message_ids,
        )

    async def _handle_stick_event(self, event: StickEvent) -> None:
        """Handle stick event."""
        if event == StickEvent.CONNECTED:
            if not self._queue.is_running:
                self._queue.start(self._manager)
                await self.initialize_stick()
        elif event == StickEvent.DISCONNECTED:
            if self._queue.is_running:
                await self._queue.stop()

    async def initialize_stick(self) -> None:
        """Initialize connection to the USB-stick."""
        if not self._manager.is_connected:
            raise StickError(
                "Cannot initialize USB-stick, connected to USB-stick first"
            )
        if not self._queue.is_running:
            raise StickError("Cannot initialize, queue manager not running")

        try:
            init_response: StickInitResponse = await self._queue.submit(
                StickInitRequest()
            )
        except StickError as err:
            raise StickError(
                "No response from USB-Stick to initialization request." +
                " Validate USB-stick is connected to port " +
                f"' {self._manager.serial_path}'"
            ) from err
        self._mac_stick = init_response.mac_decoded
        self._network_online = init_response.network_online

        # Replace first 2 characters by 00 for mac of circle+ node
        self._mac_nc = init_response.mac_network_controller
        self._network_id = init_response.network_id
        self._is_initialized = True

        if not self._network_online:
            raise StickError(
                "Zigbee network connection to Circle+ is down."
            )

    async def send(
        self, request: PlugwiseRequest, suppress_node_errors: bool = True
    ) -> PlugwiseResponse | None:
        """Submit request to queue and return response."""
        if not suppress_node_errors:
            return await self._queue.submit(request)
        try:
            return await self._queue.submit(request)
        except (NodeError, StickError) as e:
            logging.warning('%s : %s', request, str(e))
            return None
        except BaseException as e:
            logging.error('Uncaught async exception on %s : %s', request, str(e))  

    def _reset_states(self) -> None:
        """Reset internal connection information."""
        self._mac_stick = None
        self._mac_nc = None
        self._network_id = None
        self._network_online = False

    async def disconnect_from_stick(self) -> None:
        """Disconnect from USB-Stick."""
        if self._queue.is_running:
            await self._queue.stop()
        if self._unsubscribe_stick_event is not None:
            self._unsubscribe_stick_event()
            self._unsubscribe_stick_event = None
        await self._manager.disconnect_from_stick()
