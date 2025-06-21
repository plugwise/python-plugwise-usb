"""Main stick object to control associated plugwise plugs.

Use of this source code is governed by the MIT license found
in the LICENSE file.
"""

from __future__ import annotations

from asyncio import get_running_loop
from collections.abc import Callable, Coroutine
from functools import wraps
import logging
from typing import Any, Final, TypeVar, cast

from .api import NodeEvent, PlugwiseNode, StickEvent
from .connection import StickController
from .exceptions import MessageError, NodeError, StickError, SubscriptionError
from .network import StickNetwork

FuncT = TypeVar("FuncT", bound=Callable[..., Any])

NOT_INITIALIZED_STICK_ERROR: Final[StickError] = StickError(
    "Cannot load nodes when network is not initialized"
)
_LOGGER = logging.getLogger(__name__)


def raise_not_connected(func: FuncT) -> FuncT:
    """Validate existence of an active connection to Stick. Raise StickError when there is no active connection."""

    @wraps(func)
    def decorated(*args: Any, **kwargs: Any) -> Any:
        if not args[0].is_connected:
            raise StickError("Not connected to USB-Stick, connect to USB-stick first.")
        return func(*args, **kwargs)

    return cast(FuncT, decorated)


def raise_not_initialized(func: FuncT) -> FuncT:
    """Validate if active connection is initialized. Raise StickError when not initialized."""

    @wraps(func)
    def decorated(*args: Any, **kwargs: Any) -> Any:
        if not args[0].is_initialized:
            raise StickError(
                "Connection to USB-Stick is not initialized, "
                + "initialize USB-stick first."
            )
        return func(*args, **kwargs)

    return cast(FuncT, decorated)


class Stick:
    """Plugwise connection stick."""

    def __init__(self, port: str | None = None, cache_enabled: bool = True) -> None:
        """Initialize Stick."""
        self._loop = get_running_loop()
        self._loop.set_debug(True)
        self._controller = StickController()
        self._network: StickNetwork | None = None
        self._cache_enabled = cache_enabled
        self._port = port
        self._cache_folder: str = ""

    @property
    def cache_folder(self) -> str:
        """Path to store cached data."""
        return self._cache_folder

    @cache_folder.setter
    def cache_folder(self, cache_folder: str) -> None:
        """Set path to store cached data."""
        if cache_folder == self._cache_folder:
            return
        if self._network is not None:
            self._network.cache_folder = cache_folder
        self._cache_folder = cache_folder

    @property
    def cache_enabled(self) -> bool:
        """Indicates if caching is active."""
        return self._cache_enabled

    @cache_enabled.setter
    def cache_enabled(self, enable: bool = True) -> None:
        """Enable or disable usage of cache."""
        if self._network is not None:
            self._network.cache_enabled = enable
        self._cache_enabled = enable

    @property
    def nodes(self) -> dict[str, PlugwiseNode]:
        """Dictionary with all discovered and supported plugwise devices with the MAC address as their key."""
        if self._network is None:
            return {}
        return self._network.nodes

    @property
    def is_connected(self) -> bool:
        """Current connection state to USB-Stick."""
        return self._controller.is_connected

    @property
    def is_initialized(self) -> bool:
        """Current initialization state of USB-Stick connection."""
        return self._controller.is_initialized

    @property
    def joined_nodes(self) -> int | None:
        """Total number of nodes registered to Circle+ including Circle+ itself."""
        if (
            not self._controller.is_connected
            or self._network is None
            or self._network.registry is None
        ):
            return None
        return len(self._network.registry) + 1

    @property
    def firmware(self) -> str:
        """Firmware of USB-Stick."""
        return self._controller.firmware_stick

    @property
    def hardware(self) -> str:
        """Hardware of USB-Stick."""
        return self._controller.hardware_stick

    @property
    def mac_stick(self) -> str:
        """MAC address of USB-Stick. Raises StickError is connection is missing."""
        return self._controller.mac_stick

    @property
    def mac_coordinator(self) -> str:
        """MAC address of the network coordinator (Circle+). Raises StickError is connection is missing."""
        return self._controller.mac_coordinator

    @property
    def name(self) -> str:
        """Return name of Stick."""
        return self._controller.stick_name

    @property
    def network_discovered(self) -> bool:
        """Indicate if discovery of network is active. Raises StickError is connection is missing."""
        if self._network is None:
            return False
        return self._network.is_running

    @property
    def network_state(self) -> bool:
        """Indicate state of the Plugwise network."""
        if not self._controller.is_connected:
            return False
        return self._controller.network_online

    @property
    def network_id(self) -> int:
        """Network id of the Plugwise network. Raises StickError is connection is missing."""
        return self._controller.network_id

    @property
    def port(self) -> str | None:
        """Return currently configured port to USB-Stick."""
        return self._port

    @port.setter
    def port(self, port: str) -> None:
        """Path to serial port of USB-Stick."""
        if self._controller.is_connected and port != self._port:
            raise StickError("Unable to change port while connected. Disconnect first")

        self._port = port

    async def set_energy_intervals(
        self, mac: str, cons_interval: int, prod_interval: int
    ) -> bool:
        """Configure the energy logging interval settings."""
        try:
            await self._network.set_energy_intervals(mac, cons_interval, prod_interval)
        except (MessageError, NodeError, ValueError) as exc:
            raise NodeError(f"{exc}") from exc
        return True

    async def clear_cache(self) -> None:
        """Clear current cache."""
        if self._network is not None:
            await self._network.clear_cache()

    def subscribe_to_stick_events(
        self,
        stick_event_callback: Callable[[StickEvent], Coroutine[Any, Any, None]],
        events: tuple[StickEvent],
    ) -> Callable[[], None]:
        """Subscribe callback when specified StickEvent occurs.

        Returns the function to be called to unsubscribe later.
        """
        return self._controller.subscribe_to_stick_events(
            stick_event_callback,
            events,
        )

    @raise_not_initialized
    def subscribe_to_node_events(
        self,
        node_event_callback: Callable[[NodeEvent, str], Coroutine[Any, Any, None]],
        events: tuple[NodeEvent, ...],
    ) -> Callable[[], None]:
        """Subscribe callback to be called when specific NodeEvent occurs.

        Returns the function to be called to unsubscribe later.
        """
        if self._network is None:
            raise SubscriptionError(
                "Unable to subscribe to node events without network connection initialized"
            )
        return self._network.subscribe_to_node_events(
            node_event_callback,
            events,
        )

    def _validate_node_discovery(self) -> None:
        """Validate if network discovery is running.

        Raises StickError if network is not active.
        """
        if self._network is None or not self._network.is_running:
            raise StickError("Plugwise network node discovery is not active.")

    async def setup(self, discover: bool = True, load: bool = True) -> None:
        """Fully connect, initialize USB-Stick and discover all connected nodes."""
        if not self.is_connected:
            await self.connect()
        if not self.is_initialized:
            await self.initialize()
        if discover:
            await self.start_network()
            await self.discover_coordinator()
            await self.discover_nodes()
        if load:
            await self.load_nodes()

    async def connect(self, port: str | None = None) -> None:
        """Connect to USB-Stick. Raises StickError if connection fails."""
        if self._controller.is_connected:
            raise StickError(
                f"Already connected to {self._port}, "
                + "Close existing connection before (re)connect."
            )

        if port is not None:
            self._port = port

        if self._port is None:
            raise StickError(
                "Unable to connect. "
                + "Path to USB-Stick is not defined, set port property first"
            )

        await self._controller.connect_to_stick(
            self._port,
        )

    @raise_not_connected
    async def initialize(self, create_root_cache_folder: bool = False) -> None:
        """Initialize connection to USB-Stick."""
        await self._controller.initialize_stick()
        if self._network is None:
            self._network = StickNetwork(self._controller)
            self._network.cache_folder = self._cache_folder
            self._network.cache_folder_create = create_root_cache_folder
            self._network.cache_enabled = self._cache_enabled
            if self._cache_enabled:
                await self._network.initialize_cache()

    @raise_not_connected
    @raise_not_initialized
    async def start_network(self) -> None:
        """Start zigbee network."""
        if self._network is None:
            self._network = StickNetwork(self._controller)
            self._network.cache_folder = self._cache_folder
            self._network.cache_enabled = self._cache_enabled
            if self._cache_enabled:
                await self._network.initialize_cache()
        await self._network.start()

    @raise_not_connected
    @raise_not_initialized
    async def load_nodes(self) -> bool:
        """Load all discovered nodes."""
        if self._network is None:
            raise NOT_INITIALIZED_STICK_ERROR
        if not self._network.is_running:
            raise StickError("Cannot load nodes when network is not started")
        return await self._network.discover_nodes(load=True)

    @raise_not_connected
    @raise_not_initialized
    async def discover_coordinator(self, load: bool = False) -> None:
        """Discover the network coordinator."""
        if self._network is None:
            raise NOT_INITIALIZED_STICK_ERROR
        await self._network.discover_network_coordinator(load=load)

    @raise_not_connected
    @raise_not_initialized
    async def discover_nodes(self, load: bool = False) -> None:
        """Discover all nodes."""
        if self._network is None:
            raise NOT_INITIALIZED_STICK_ERROR
        await self._network.discover_nodes(load=load)

    @raise_not_connected
    @raise_not_initialized
    async def register_node(self, mac: str) -> bool:
        """Add node to plugwise network."""
        if self._network is None:
            return False

        try:
            return await self._network.register_node(mac)
        except NodeError as exc:
            raise NodeError(f"Unable to add Node ({mac}): {exc}") from exc

    @raise_not_connected
    @raise_not_initialized
    async def unregister_node(self, mac: str) -> None:
        """Remove node to plugwise network."""
        if self._network is None:
            return
        try:
            await self._network.unregister_node(mac)
        except MessageError as exc:
            raise NodeError(f"Unable to remove Node ({mac}): {exc}") from exc

    async def disconnect(self) -> None:
        """Disconnect from USB-Stick."""
        if self._network is not None:
            await self._network.stop()
        await self._controller.disconnect_from_stick()
