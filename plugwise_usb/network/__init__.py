"""Plugwise network."""

# region - Imports

from __future__ import annotations

from asyncio import gather
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
import logging

from ..api import NodeEvent, NodeType, StickEvent
from ..connection import StickController
from ..constants import UTF8
from ..exceptions import MessageError, NodeError, StickError, StickTimeout
from ..messages.requests import (
    CirclePlusAllowJoiningRequest,
    NodeInfoRequest,
    NodePingRequest,
)
from ..messages.responses import (
    NODE_AWAKE_RESPONSE_ID,
    NODE_JOIN_ID,
    NodeAckResponse,
    NodeAwakeResponse,
    NodeInfoResponse,
    NodeJoinAvailableResponse,
    NodePingResponse,
    NodeResponseType,
)
from ..nodes import PlugwiseNode
from ..nodes.circle import PlugwiseCircle
from ..nodes.circle_plus import PlugwiseCirclePlus
from ..nodes.scan import PlugwiseScan
from ..nodes.sense import PlugwiseSense
from ..nodes.stealth import PlugwiseStealth
from ..nodes.switch import PlugwiseSwitch
from ..util import validate_mac
from .registry import StickNetworkRegister

_LOGGER = logging.getLogger(__name__)
# endregion


class StickNetwork():
    """USB-Stick zigbee network class."""

    accept_join_request = False
    join_available: Callable | None = None
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

        self._discover: bool = False
        self._nodes: dict[str, PlugwiseNode] = {}
        self._awake_discovery: dict[str, datetime] = {}

        self._node_event_subscribers: dict[
            Callable[[], None],
            tuple[Callable[[NodeEvent], Awaitable[None]], NodeEvent | None]
        ] = {}

        self._unsubscribe_stick_event: Callable[[], None] | None = None
        self._unsubscribe_node_awake: Callable[[], None] | None = None
        self._unsubscribe_node_join: Callable[[], None] | None = None

# region - Properties

    @property
    def cache_enabled(self) -> bool:
        """Return usage of cache of network register."""
        return self._cache_enabled

    @cache_enabled.setter
    def cache_enabled(self, enable: bool = True) -> None:
        """Enable or disable usage of cache of network register."""
        self._register.cache_enabled = enable
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

    async def register_node(self, mac: str) -> None:
        """Register node to Plugwise network."""
        if not validate_mac(mac):
            raise NodeError(f"Invalid mac '{mac}' to register")
        address = await self._register.register_node(mac)
        self._discover_node(address, mac, None)

    async def clear_cache(self) -> None:
        """Clear register cache."""
        await self._register.clear_register_cache()

    async def unregister_node(self, mac: str) -> None:
        """Unregister node from current Plugwise network."""
        await self._register.unregister_node(mac)
        await self._nodes[mac].unload()
        self._nodes.pop(mac)

# region - Handle stick connect/disconnect events
    def _subscribe_to_protocol_events(self) -> None:
        """Subscribe to events from protocol."""
        self._unsubscribe_stick_event = (
            self._controller.subscribe_to_stick_events(
                self._handle_stick_event,
                (StickEvent.CONNECTED, StickEvent.DISCONNECTED),
            )
        )
        self._unsubscribe_node_awake = (
            self._controller.subscribe_to_node_responses(
                self.node_awake_message,
                None,
                (NODE_AWAKE_RESPONSE_ID,),
            )
        )
        self._unsubscribe_node_join = (
            self._controller.subscribe_to_node_responses(
                self.node_join_available_message,
                None,
                (NODE_JOIN_ID,),
            )
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
            await gather(
                *[
                    node.disconnect()
                    for node in self._nodes.values()
                ]
            )
            self._is_running = False

    async def node_awake_message(self, response: NodeAwakeResponse) -> None:
        """Handle NodeAwakeResponse message."""
        mac = response.mac_decoded
        if self._awake_discovery.get(mac) is None:
            self._awake_discovery[mac] = (
                response.timestamp - timedelta(seconds=15)
            )
        if mac in self._nodes:
            if self._awake_discovery[mac] < (
                response.timestamp - timedelta(seconds=10)
            ):
                await self._notify_node_event_subscribers(NodeEvent.AWAKE, mac)
            self._awake_discovery[mac] = response.timestamp
            return
        if self._register.network_address(mac) is None:
            _LOGGER.warning(
                "Skip node awake message for %s because network registry address is unknown",
                mac
            )
            return
        address: int | None = self._register.network_address(mac)
        if self._nodes.get(mac) is None:
            await self._discover_node(address, mac, None)
            await self._load_node(mac)
            await self._notify_node_event_subscribers(NodeEvent.AWAKE, mac)

    async def node_join_available_message(
        self, response: NodeJoinAvailableResponse
    ) -> None:
        """Handle NodeJoinAvailableResponse messages."""
        mac = response.mac_decoded
        await self._notify_node_event_subscribers(NodeEvent.JOIN, mac)

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
    async def discover_network_coordinator(
        self, load: bool = False
    ) -> bool:
        """Discover the Zigbee network coordinator (Circle+/Stealth+)."""
        if self._controller.mac_coordinator is None:
            raise NodeError("Unknown mac address for network coordinator.")
        if load and await self._load_node(self._controller.mac_coordinator):
            return True

        # Validate the network controller is online
        # try to ping first and raise error at stick timeout
        ping_response: NodePingResponse | None = None
        try:
            ping_response = await self._controller.send(
                NodePingRequest(
                    bytes(self._controller.mac_coordinator, UTF8),
                    retries=1
                ),
            )  # type: ignore [assignment]
        except StickTimeout as err:
            raise StickError(
                "The zigbee network coordinator (Circle+/Stealth+) with mac " +
                "'%s' did not respond to ping request. Make " +
                "sure the Circle+/Stealth+ is within reach of the USB-stick !",
                self._controller.mac_coordinator
            ) from err
        if ping_response is None:
            return False

        address, node_type = self._register.network_controller()
        if await self._discover_node(
            address, self._controller.mac_coordinator, node_type,
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
            _LOGGER.warning(
                "Skip creating node object because node object for mac " +
                "%s already exists",
                mac
            )
            return
        supported_type = True
        if node_type == NodeType.CIRCLE_PLUS:
            self._nodes[mac] = PlugwiseCirclePlus(
                mac,
                address,
                self._controller,
            )
            _LOGGER.debug("Circle+ node %s added", mac)
        elif node_type == NodeType.CIRCLE:
            self._nodes[mac] = PlugwiseCircle(
                mac,
                address,
                self._controller,
            )
            _LOGGER.debug("Circle node %s added", mac)
        elif node_type == NodeType.SWITCH:
            self._nodes[mac] = PlugwiseSwitch(
                mac,
                address,
                self._controller,
            )
            _LOGGER.debug("Switch node %s added", mac)
        elif node_type == NodeType.SENSE:
            self._nodes[mac] = PlugwiseSense(
                mac,
                address,
                self._controller,
            )
            _LOGGER.debug("Sense node %s added", mac)
        elif node_type == NodeType.SCAN:
            self._nodes[mac] = PlugwiseScan(
                mac,
                address,
                self._controller,
            )
            _LOGGER.debug("Scan node %s added", mac)
        elif node_type == NodeType.STEALTH:
            self._nodes[mac] = PlugwiseStealth(
                mac,
                address,
                self._controller,
            )
            _LOGGER.debug("Stealth node %s added", mac)
        else:
            supported_type = False
            _LOGGER.warning(
                "Node %s of type %s is unsupported",
                mac,
                str(node_type)
            )
        if supported_type:
            self._register.update_network_registration(address, mac, node_type)

        if self._cache_enabled and supported_type:
            _LOGGER.debug(
                "Enable caching for node %s to folder '%s'",
                mac,
                self._cache_folder,
            )
            self._nodes[mac].cache_folder = self._cache_folder
            self._nodes[mac].cache_enabled = True

    async def get_node_details(
        self, mac: str, ping_first: bool
    ) -> tuple[NodeInfoResponse | None, NodePingResponse | None]:
        """Return node discovery type."""
        ping_response: NodePingResponse | None = None
        if ping_first:
            # Define ping request with custom timeout
            ping_request = NodePingRequest(bytes(mac, UTF8), retries=1)
            # ping_request.timeout = 3

            ping_response: NodePingResponse | None = (
                await self._controller.send(
                    ping_request
                )
            )
            if ping_response is None:
                return (None, None)

        info_response: NodeInfoResponse | None = await self._controller.send(
            NodeInfoRequest(bytes(mac, UTF8), retries=1)
        )  # type: ignore [assignment]
        return (info_response, ping_response)

    async def _discover_node(
        self,
        address: int,
        mac: str,
        node_type: NodeType | None
    ) -> bool:
        """Discover node and add it to list of nodes.

        Return True if discovery succeeded.
        """
        if self._nodes.get(mac) is not None:
            _LOGGER.warning("Skip discovery of already known node %s ", mac)
            return True

        if node_type is not None:
            self._create_node_object(mac, address, node_type)
            _LOGGER.debug("Publish NODE_DISCOVERED for %s", mac)
            await self._notify_node_event_subscribers(
                NodeEvent.DISCOVERED, mac
            )
            return True

        # Node type is unknown, so we need to discover it first
        _LOGGER.debug("Starting the discovery of node %s", mac)
        node_info, node_ping = await self.get_node_details(mac, True)
        if node_info is None:
            return False
        self._create_node_object(mac, address, node_info.node_type)

        # Forward received NodeInfoResponse message to node object
        await self._nodes[mac].node_info_update(node_info)
        if node_ping is not None:
            await self._nodes[mac].ping_update(node_ping)
        _LOGGER.debug("Publish NODE_DISCOVERED for %s", mac)
        await self._notify_node_event_subscribers(NodeEvent.DISCOVERED, mac)

    async def _discover_registered_nodes(self) -> None:
        """Discover nodes."""
        _LOGGER.debug("Start discovery of registered nodes")
        counter = 0
        for address, registration in self._register.registry.items():
            mac, node_type = registration
            if mac != "":
                if self._nodes.get(mac) is None:
                    await self._discover_node(
                        address, mac, node_type
                    )
                counter += 1
        _LOGGER.debug(
            "Total %s registered node(s)",
            str(counter)
        )

    async def _load_node(self, mac: str) -> bool:
        """Load node."""
        if self._nodes.get(mac) is None:
            return False
        if self._nodes[mac].loaded:
            return True
        if await self._nodes[mac].load():
            await self._notify_node_event_subscribers(NodeEvent.LOADED, mac)
            return True
        return False

    async def _load_discovered_nodes(self) -> None:
        await gather(
        """Load all nodes currently discovered."""
            *[
                self._load_node(mac)
                for mac, node in self._nodes.items()
                if not node.loaded
            ]
        )

    async def _unload_discovered_nodes(self) -> None:
        """Unload all nodes."""
        await gather(
            *[
                node.unload()
                for node in self._nodes.values()
            ]
        )

# endregion

# region - Network instance
    async def start(self) -> None:
        """Start and activate network."""
        self._register.quick_scan_finished(self._discover_registered_nodes)
        self._register.full_scan_finished(self._discover_registered_nodes)
        await self._register.start()
        self._subscribe_to_protocol_events()
        self._is_running = True

    async def discover_nodes(self, load: bool = True) -> None:
        """Discover nodes."""
        if not self._is_running:
            await self.start()
        await self.discover_network_coordinator()
        await self._discover_registered_nodes()
        await sleep(0)
        if load:
            await self._load_discovered_nodes()

    async def stop(self) -> None:
        """Stop network discovery."""
        _LOGGER.debug("Stopping")
        self._is_running = False
        self._unsubscribe_to_protocol_events()
        await sleep(0)
        await self._unload_discovered_nodes()
        await sleep(0)
        await self._register.stop()
        _LOGGER.debug("Stopping finished")

# endregion

    async def allow_join_requests(self, state: bool) -> None:
        """Enable or disable Plugwise network."""
        response: NodeAckResponse | None = await self._controller.send(
            CirclePlusAllowJoiningRequest(state)
        )  # type: ignore [assignment]
        if response is None:
            raise NodeError(
                "No response to get notifications for join request."
            )
        if response.ack_id != NodeResponseType.JOIN_ACCEPTED:
            raise MessageError(
                f"Unknown NodeResponseType '{response.ack_id!r}' received"
            )

    def subscribe_to_network_events(
        self,
        node_event_callback: Callable[[NodeEvent, str], Awaitable[None]],
        events: tuple[NodeEvent],
    ) -> Callable[[], None]:
        """Subscribe callback when specified NodeEvent occurs.

        Returns the function to be called to unsubscribe later.
        """
        def remove_subscription() -> None:
            """Remove stick event subscription."""
            self._node_event_subscribers.pop(remove_subscription)

        self._node_event_subscribers[
            remove_subscription
        ] = (node_event_callback, events)
        return remove_subscription

    async def _notify_node_event_subscribers(
        self,
        event: NodeEvent,
        mac: str
    ) -> None:
        """Call callback for node event subscribers."""
        callback_list: list[Callable] = []
        for callback, filtered_events in list(
            self._node_event_subscribers.values()
        ):
            if event in filtered_events:
                callback_list.append(callback(event, mac))
        if len(callback_list) > 0:
            await gather(*callback_list)
