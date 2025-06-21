"""Plugwise network."""

# region - Imports

from __future__ import annotations

from asyncio import Task, create_task, gather, sleep
from collections.abc import Callable, Coroutine
from datetime import datetime, timedelta
import logging
from typing import Any

from ..api import NodeEvent, NodeType, PlugwiseNode, StickEvent
from ..connection import StickController
from ..constants import ENERGY_NODE_TYPES, UTF8
from ..exceptions import CacheError, MessageError, NodeError, StickError, StickTimeout
from ..helpers.util import validate_mac
from ..messages.requests import CircleMeasureIntervalRequest, NodePingRequest
from ..messages.responses import (
    NODE_AWAKE_RESPONSE_ID,
    NODE_JOIN_ID,
    NODE_REJOIN_ID,
    NodeAwakeResponse,
    NodeJoinAvailableResponse,
    NodeRejoinResponse,
    NodeResponseType,
    PlugwiseResponse,
)
from ..nodes import get_plugwise_node
from .registry import StickNetworkRegister

_LOGGER = logging.getLogger(__name__)
# endregion


class StickNetwork:
    """USB-Stick zigbee network class."""

    _event_subscriptions: dict[StickEvent, int] = {}

    def __init__(
        self,
        controller: StickController,
    ) -> None:
        """Initialize the USB-Stick zigbee network class."""
        self._controller = controller
        self._register = StickNetworkRegister(
            bytes(controller.mac_coordinator, encoding=UTF8),
            controller.send,
        )
        self._is_running: bool = False

        self._cache_folder: str = ""
        self._cache_enabled: bool = False
        self._cache_folder_create = False

        self._discover: bool = False
        self._nodes: dict[str, PlugwiseNode] = {}
        self._awake_discovery: dict[str, datetime] = {}

        self._node_event_subscribers: dict[
            Callable[[], None],
            tuple[
                Callable[[NodeEvent, str], Coroutine[Any, Any, None]],
                tuple[NodeEvent, ...],
            ],
        ] = {}

        self._unsubscribe_stick_event: Callable[[], None] | None = None
        self._unsubscribe_node_awake: Callable[[], None] | None = None
        self._unsubscribe_node_join: Callable[[], None] | None = None
        self._unsubscribe_node_rejoin: Callable[[], None] | None = None

        self._discover_sed_tasks: dict[str, Task[bool]] = {}

    # region - Properties

    @property
    def cache_enabled(self) -> bool:
        """Return usage of cache of network register."""
        return self._cache_enabled

    @cache_enabled.setter
    def cache_enabled(self, enable: bool = True) -> None:
        """Enable or disable usage of cache of network register."""
        self._register.cache_enabled = enable
        if self._cache_enabled != enable:
            for node in self._nodes.values():
                node.cache_enabled = enable
        self._cache_enabled = enable

    @property
    def cache_folder(self) -> str:
        """Path to cache data of network register."""
        return self._cache_folder

    @cache_folder.setter
    def cache_folder(self, cache_folder: str) -> None:
        """Set path to cache data of network register."""
        self._cache_folder = cache_folder
        self._register.cache_folder = cache_folder
        for node in self._nodes.values():
            node.cache_folder = cache_folder

    @property
    def cache_folder_create(self) -> bool:
        """Return if cache folder must be create when it does not exists."""
        return self._cache_folder_create

    @cache_folder_create.setter
    def cache_folder_create(self, enable: bool = True) -> None:
        """Enable or disable creation of cache folder."""
        self._cache_folder_create = enable

    async def initialize_cache(self) -> None:
        """Initialize the cache folder."""
        if not self._cache_enabled:
            raise CacheError("Unable to initialize cache, enable cache first.")
        await self._register.initialize_cache(self._cache_folder_create)

    @property
    def controller_active(self) -> bool:
        """Return True if network controller (Circle+) is discovered and active."""
        if self._controller.mac_coordinator in self._nodes:
            return self._nodes[self._controller.mac_coordinator].available
        return False

    @property
    def is_running(self) -> bool:
        """Return state of network discovery."""
        return self._is_running

    @property
    def nodes(
        self,
    ) -> dict[str, PlugwiseNode]:
        """Dictionary with all discovered network nodes with the mac address as the key."""
        return self._nodes

    @property
    def registry(self) -> dict[int, tuple[str, NodeType | None]]:
        """Return dictionary with all registered (joined) nodes."""
        return self._register.registry

    # endregion

    async def register_node(self, mac: str) -> bool:
        """Register node to Plugwise network."""
        try:
            await self._register.register_node(mac)
        except NodeError as exc:
            raise NodeError(f"{exc}") from exc
        return True

    async def clear_cache(self) -> None:
        """Clear register cache."""
        await self._register.clear_register_cache()

    async def unregister_node(self, mac: str) -> None:
        """Unregister node from current Plugwise network."""
        try:
            await self._register.unregister_node(mac)
        except (KeyError, NodeError) as exc:
            raise MessageError("Mac not registered, already deleted?") from exc

        await self._nodes[mac].unload()
        self._nodes.pop(mac)

    # region - Handle stick connect/disconnect events
    def _subscribe_to_protocol_events(self) -> None:
        """Subscribe to events from protocol."""
        self._unsubscribe_stick_event = self._controller.subscribe_to_stick_events(
            self._handle_stick_event,
            (StickEvent.CONNECTED, StickEvent.DISCONNECTED),
        )

    async def _subscribe_to_node_events(self) -> None:
        """Subscribe to events from protocol."""
        self._unsubscribe_node_awake = await self._controller.subscribe_to_messages(
            self.node_awake_message,
            None,
            (NODE_AWAKE_RESPONSE_ID,),
            None,
        )
        self._unsubscribe_node_join = await self._controller.subscribe_to_messages(
            self.node_join_available_message, None, (NODE_JOIN_ID,), None
        )
        self._unsubscribe_node_rejoin = await self._controller.subscribe_to_messages(
            self.node_rejoin_message, None, (NODE_REJOIN_ID,), None
        )

    async def _handle_stick_event(self, event: StickEvent) -> None:
        """Handle stick events."""
        if event == StickEvent.CONNECTED:
            await gather(
                *[
                    node.reconnect()
                    for node in self._nodes.values()
                    if not node.available
                ]
            )
            self._is_running = True
            await self.discover_nodes()
        elif event == StickEvent.DISCONNECTED:
            await gather(*[node.disconnect() for node in self._nodes.values()])
            self._is_running = False

    async def node_awake_message(self, response: PlugwiseResponse) -> None:
        """Handle NodeAwakeResponse message."""
        if not isinstance(response, NodeAwakeResponse):
            raise MessageError(
                f"Invalid response message type ({response.__class__.__name__}) received, expected NodeAwakeResponse"
            )

        mac = response.mac_decoded
        if self._awake_discovery.get(mac) is None:
            self._awake_discovery[mac] = response.timestamp - timedelta(seconds=15)

        if mac in self._nodes:
            if self._awake_discovery[mac] < (
                response.timestamp - timedelta(seconds=10)
            ):
                await self._notify_node_event_subscribers(NodeEvent.AWAKE, mac)
            self._awake_discovery[mac] = response.timestamp
            return

        if (address := self._register.network_address(mac)) is None:
            if self._register.scan_completed:
                return

            _LOGGER.debug(
                "Skip node awake message for %s because network registry address is unknown",
                mac,
            )
            return

        if self._nodes.get(mac) is None:
            if (
                self._discover_sed_tasks.get(mac) is None
                or self._discover_sed_tasks[mac].done()
            ):
                self._discover_sed_tasks[mac] = create_task(
                    self._discover_battery_powered_node(address, mac)
                )
            else:
                _LOGGER.debug("duplicate maintenance awake discovery for %s", mac)

    async def node_join_available_message(self, response: PlugwiseResponse) -> bool:
        """Handle NodeJoinAvailableResponse messages."""
        if not isinstance(response, NodeJoinAvailableResponse):
            raise MessageError(
                f"Invalid response message type ({response.__class__.__name__}) received, expected NodeJoinAvailableResponse"
            )

        mac = response.mac_decoded
        _LOGGER.debug(
            "node_join_available_message | sending NodeAddRequest for %s", mac
        )
        try:
            result = await self.register_node(mac)
        except NodeError as exc:
            raise NodeError(f"Unable to add Node ({mac}): {exc}") from exc
        if result:
            return True

        return False

    async def node_rejoin_message(self, response: PlugwiseResponse) -> bool:
        """Handle NodeRejoinResponse messages."""
        if not isinstance(response, NodeRejoinResponse):
            raise MessageError(
                f"Invalid response message type ({response.__class__.__name__}) received, expected NodeRejoinResponse"
            )
        mac = response.mac_decoded
        if (address := self._register.network_address(mac)) is None:
            if (address := self._register.update_node_registration(mac)) is None:
                raise NodeError(f"Failed to obtain address for node {mac}")

        if self._nodes.get(mac) is None:
            task = self._discover_sed_tasks.get(mac)
            if task is None or task.done():
                self._discover_sed_tasks[mac] = create_task(
                    self._discover_battery_powered_node(address, mac)
                )
            else:
                _LOGGER.debug("duplicate awake discovery for %s", mac)
            return True

        return False

    def _unsubscribe_to_protocol_events(self) -> None:
        """Unsubscribe to events from protocol."""
        if self._unsubscribe_node_awake is not None:
            self._unsubscribe_node_awake()
            self._unsubscribe_node_awake = None
        if self._unsubscribe_stick_event is not None:
            self._unsubscribe_stick_event()
            self._unsubscribe_stick_event = None

    # endregion

    # region - Coordinator
    async def discover_network_coordinator(self, load: bool = False) -> bool:
        """Discover the Zigbee network coordinator (Circle+/Stealth+)."""
        if self._controller.mac_coordinator is None:
            raise NodeError("Unknown mac address for network coordinator.")
        if load and await self._load_node(self._controller.mac_coordinator):
            return True

        # Validate the network controller is online
        # try to ping first and raise error at stick timeout
        ping_request = NodePingRequest(
            self._controller.send,
            bytes(self._controller.mac_coordinator, UTF8),
            retries=1,
        )
        try:
            ping_response = await ping_request.send()
        except StickTimeout as err:
            raise StickError(
                "The zigbee network coordinator (Circle+/Stealth+) with mac "
                + "'%s' did not respond to ping request. Make "
                + "sure the Circle+/Stealth+ is within reach of the USB-stick !",
                self._controller.mac_coordinator,
            ) from err
        if ping_response is None:
            return False

        if await self._discover_node(
            -1, self._controller.mac_coordinator, None, ping_first=False
        ):
            if load:
                return await self._load_node(self._controller.mac_coordinator)
            return True

        return False

    # endregion

    # region - Nodes
    def _create_node_object(
        self,
        mac: str,
        address: int,
        node_type: NodeType,
    ) -> None:
        """Create node object and update network registry."""
        if self._nodes.get(mac) is not None:
            _LOGGER.debug(
                "Skip creating node object because node object for mac %s already exists",
                mac,
            )
            return
        node = get_plugwise_node(
            mac,
            address,
            self._controller,
            self._notify_node_event_subscribers,
            node_type,
        )
        if node is None:
            _LOGGER.warning("Node %s of type %s is unsupported", mac, str(node_type))
            return
        self._nodes[mac] = node
        _LOGGER.debug("%s node %s added", node.__class__.__name__, mac)
        self._register.update_network_registration(address, mac, node_type)

        if self._cache_enabled:
            _LOGGER.debug(
                "Enable caching for node %s to folder '%s'",
                mac,
                self._cache_folder,
            )
            self._nodes[mac].cache_folder = self._cache_folder
            self._nodes[mac].cache_folder_create = self._cache_folder_create
            self._nodes[mac].cache_enabled = True

    async def _discover_battery_powered_node(
        self,
        address: int,
        mac: str,
    ) -> bool:
        """Discover a battery powered node and add it to list of nodes.

        Return True if discovery succeeded.
        """
        if not await self._discover_node(
            address, mac, node_type=None, ping_first=False
        ):
            return False
        if await self._load_node(mac):
            await self._notify_node_event_subscribers(NodeEvent.AWAKE, mac)
            return True
        return False

    async def _discover_node(
        self,
        address: int,
        mac: str,
        node_type: NodeType | None,
        ping_first: bool = True,
    ) -> bool:
        """Discover node and add it to list of nodes.

        Return True if discovery succeeded.
        """
        _LOGGER.debug("Start discovery of node %s ", mac)
        if self._nodes.get(mac) is not None:
            _LOGGER.debug("Skip discovery of already known node %s ", mac)
            return True

        if node_type is not None:
            self._create_node_object(mac, address, node_type)
            await self._notify_node_event_subscribers(NodeEvent.DISCOVERED, mac)
            return True

        # Node type is unknown, so we need to discover it first
        _LOGGER.debug("Starting the discovery of node %s", mac)
        node_info, node_ping = await self._controller.get_node_details(mac, ping_first)
        if node_info is None:
            return False
        self._create_node_object(mac, address, node_info.node_type)

        # Forward received NodeInfoResponse message to node
        await self._nodes[mac].message_for_node(node_info)
        if node_ping is not None:
            await self._nodes[mac].message_for_node(node_ping)
        await self._notify_node_event_subscribers(NodeEvent.DISCOVERED, mac)
        return True

    async def _discover_registered_nodes(self) -> None:
        """Discover nodes."""
        _LOGGER.debug("Start discovery of registered nodes")
        counter = 0
        for address, registration in self._register.registry.items():
            mac, node_type = registration
            if mac != "":
                if self._nodes.get(mac) is None:
                    await self._discover_node(address, mac, node_type)
                counter += 1
                await sleep(0)
        _LOGGER.debug("Total %s registered node(s)", str(counter))

    async def _load_node(self, mac: str) -> bool:
        """Load node."""
        if self._nodes.get(mac) is None:
            return False
        if self._nodes[mac].is_loaded:
            return True
        if await self._nodes[mac].load():
            await self._notify_node_event_subscribers(NodeEvent.LOADED, mac)
            return True
        return False

    async def _load_discovered_nodes(self) -> bool:
        """Load all nodes currently discovered."""
        _LOGGER.debug("_load_discovered_nodes | START | %s", len(self._nodes))
        for mac, node in self._nodes.items():
            _LOGGER.debug(
                "_load_discovered_nodes | mac=%s | loaded=%s", mac, node.is_loaded
            )

        nodes_not_loaded = tuple(
            mac for mac, node in self._nodes.items() if not node.is_loaded
        )
        _LOGGER.debug("_load_discovered_nodes | nodes_not_loaded=%s", nodes_not_loaded)
        load_result = await gather(*[self._load_node(mac) for mac in nodes_not_loaded])
        _LOGGER.debug("_load_discovered_nodes | load_result=%s", load_result)
        result_index = 0
        for mac in nodes_not_loaded:
            if load_result[result_index]:
                await self._notify_node_event_subscribers(NodeEvent.LOADED, mac)
            else:
                _LOGGER.debug(
                    "_load_discovered_nodes | Load request for %s failed", mac
                )
            result_index += 1
        _LOGGER.debug("_load_discovered_nodes | END")
        return all(load_result)

    async def _unload_discovered_nodes(self) -> None:
        """Unload all nodes."""
        await gather(*[node.unload() for node in self._nodes.values()])

    # endregion

    # region - Network instance
    async def start(self) -> None:
        """Start and activate network."""
        self._register.quick_scan_finished(self._discover_registered_nodes)
        self._register.full_scan_finished(self._discover_registered_nodes)
        await self._register.start()
        self._subscribe_to_protocol_events()
        await self._subscribe_to_node_events()
        self._is_running = True

    async def discover_nodes(self, load: bool = True) -> bool:
        """Discover nodes."""
        await self.discover_network_coordinator(load=load)
        if not self._is_running:
            await self.start()

        await self._discover_registered_nodes()
        if load:
            return await self._load_discovered_nodes()

        return True

    async def stop(self) -> None:
        """Stop network discovery."""
        _LOGGER.debug("Stopping")
        for task in self._discover_sed_tasks.values():
            if not task.done():
                task.cancel()
        self._is_running = False
        self._unsubscribe_to_protocol_events()
        await self._unload_discovered_nodes()
        await self._register.stop()
        _LOGGER.debug("Stopping finished")

    # endregion

    async def set_energy_intervals(
        self, mac: str, consumption: int, production: int
    ) -> None:
        """Set the logging intervals for both energy consumption and production.

        Default: consumption = 60, production = 0.
        For logging energy in both directions set both to 60.
        """
        self._validate_energy_node(mac)
        if consumption <= 0:
            raise ValueError("Consumption interval must be positive")
        if production < 0:
            raise ValueError("Production interval must be non-negative")
        if production > 0 and production % consumption != 0:
            raise ValueError(
                "Production interval must be a multiple of consumption interval"
            )

        _LOGGER.debug(
            "set_energy_intervals | cons=%s, prod=%s", consumption, production
        )
        request = CircleMeasureIntervalRequest(
            self._controller.send, bytes(mac, UTF8), consumption, production
        )
        if (response := await request.send()) is None:
            raise NodeError("No response for CircleMeasureIntervalRequest.")

        if response.response_type != NodeResponseType.POWER_LOG_INTERVAL_ACCEPTED:
            raise MessageError(
                f"Unknown NodeResponseType '{response.response_type.name}' received"
            )

    def _validate_energy_node(self, mac: str) -> None:
        """Validate node for energy operations."""
        if not validate_mac(mac):
            raise NodeError(f"MAC '{mac}' invalid")

        if mac not in self._nodes:
            raise NodeError(f"Node {mac} not present in network")

        if self._nodes[mac].node_info.node_type.value not in ENERGY_NODE_TYPES:
            raise NodeError(
                f"Energy operations not supported for {self._nodes[mac].node_info.node_type.name}"
            )

    def subscribe_to_node_events(
        self,
        node_event_callback: Callable[[NodeEvent, str], Coroutine[Any, Any, None]],
        events: tuple[NodeEvent, ...],
    ) -> Callable[[], None]:
        """Subscribe callback when specified NodeEvent occurs.

        Returns the function to be called to unsubscribe later.
        """

        def remove_subscription() -> None:
            """Remove stick event subscription."""
            self._node_event_subscribers.pop(remove_subscription)

        self._node_event_subscribers[remove_subscription] = (
            node_event_callback,
            events,
        )
        return remove_subscription

    async def _notify_node_event_subscribers(self, event: NodeEvent, mac: str) -> None:
        """Call callback for node event subscribers."""
        callback_list: list[Coroutine[Any, Any, None]] = []
        for callback, events in self._node_event_subscribers.values():
            if event in events:
                _LOGGER.debug("Publish %s for %s", event, mac)
                callback_list.append(callback(event, mac))
        if len(callback_list) > 0:
            await gather(*callback_list)
