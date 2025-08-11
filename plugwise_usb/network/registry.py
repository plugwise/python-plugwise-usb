"""Register of network configuration."""

from __future__ import annotations

from asyncio import CancelledError, Task, create_task, sleep
from collections.abc import Awaitable, Callable
from copy import deepcopy
import logging
from typing import Final

from ..api import NodeType
from ..constants import UTF8
from ..exceptions import CacheError, NodeError, StickError
from ..helpers.util import validate_mac
from ..messages.requests import (
    CirclePlusScanRequest,
    NodeAddRequest,
    NodeRemoveRequest,
    PlugwiseRequest,
)
from ..messages.responses import PlugwiseResponse
from .cache import NetworkRegistrationCache

_LOGGER = logging.getLogger(__name__)

CIRCLEPLUS_SCANREQUEST_MAINTENANCE: Final = 10
CIRCLEPLUS_SCANREQUEST_QUICK: Final = 0.1


class StickNetworkRegister:
    """Network register."""

    def __init__(
        self,
        mac_network_controller: bytes,
        send_fn: Callable[[PlugwiseRequest, bool], Awaitable[PlugwiseResponse | None]],
    ) -> None:
        """Initialize network register."""
        self._mac_nc = mac_network_controller
        self._send_to_controller = send_fn
        self._cache_folder: str = ""
        self._cache_restored = False
        self._cache_enabled = False
        self._network_cache: NetworkRegistrationCache | None = None
        self._loaded: bool = False
        self._registry: list[str] = []
        self._registration_task: Task[None] | None = None
        self._start_node_discover: (
            Callable[[str, NodeType | None, bool], Awaitable[bool]] | None
        ) = None
        self._registration_scan_delay: float = CIRCLEPLUS_SCANREQUEST_MAINTENANCE
        self._scan_completed = False
        self._scan_completed_callback: Callable[[], Awaitable[None]] | None = None

    # region Properties

    @property
    def cache_enabled(self) -> bool:
        """Return usage of cache."""
        return self._cache_enabled

    @cache_enabled.setter
    def cache_enabled(self, enable: bool = True) -> None:
        """Enable or disable usage of cache."""
        if enable and not self._cache_enabled:
            _LOGGER.debug("Enable cache")
            self._network_cache = NetworkRegistrationCache(self._cache_folder)
        elif not enable and self._cache_enabled:
            _LOGGER.debug("Disable cache")
        self._cache_enabled = enable

    async def initialize_cache(self, create_root_folder: bool = False) -> None:
        """Initialize cache."""
        if not self._cache_enabled or self._network_cache is None:
            raise CacheError("Unable to initialize cache, enable cache first.")
        await self._network_cache.initialize_cache(create_root_folder)

    @property
    def cache_folder(self) -> str:
        """Path to folder to store cached data."""
        return self._cache_folder

    @cache_folder.setter
    def cache_folder(self, cache_folder: str) -> None:
        """Set path to cache data."""
        if cache_folder == self._cache_folder:
            return
        self._cache_folder = cache_folder
        if self._network_cache is not None:
            self._network_cache.cache_root_directory = cache_folder

    @property
    def registry(self) -> list[str]:
        """Return list with mac's of all joined nodes."""
        return deepcopy(self._registry)

    @property
    def scan_completed(self) -> bool:
        """Indicate if scan is completed."""
        return self._scan_completed

    def start_node_discover(
        self, callback: Callable[[str, NodeType | None, bool], Awaitable[bool]]
    ) -> None:
        """Register async callback invoked when a node is found."""
        self._start_node_discover = callback

    def scan_completed_callback(self, callback: Callable[[], Awaitable[None]]) -> None:
        """Register async callback invoked when a node is found.

        Args:
            callback: Async callable with signature
                (mac: str, node_type: NodeType | None, ping_first: bool) -> bool.
                It must return True when discovery succeeded; return False to allow the caller
                to fall back (e.g., SED discovery path).

        Returns:
            None

        """
        self._scan_completed_callback = callback

    async def _exec_node_discover_callback(
        self,
        mac: str,
        node_type: NodeType | None,
        ping_first: bool,
    ) -> None:
        """Protect _start_node_discover() callback execution."""
        if self._start_node_discover is not None:
            try:
                await self._start_node_discover(mac, node_type, ping_first)
            except CancelledError:
                raise
            except Exception:
                _LOGGER.exception("start_node_discover callback failed for %s", mac)
        else:
            _LOGGER.debug("No start_node_discover callback set; skipping for %s", mac)

    # endregion

    async def start(self) -> None:
        """Initialize load the network registry."""
        if self._cache_enabled:
            await self.restore_network_cache()
        await self.load_registrations_from_cache()
        self._registration_task = create_task(
            self.update_missing_registrations_circleplus()
        )

    async def restore_network_cache(self) -> None:
        """Restore previously saved cached network and node information."""
        if self._network_cache is None:
            _LOGGER.error("Unable to restore cache when cache is not initialized")
            return
        if not self._cache_restored:
            if not self._network_cache.initialized:
                await self._network_cache.initialize_cache()
            await self._network_cache.restore_cache()
        self._cache_restored = True

    async def retrieve_network_registration(
        self, address: int, retry: bool = True
    ) -> tuple[int, str] | None:
        """Return the network mac registration of specified address."""
        request = CirclePlusScanRequest(self._send_to_controller, self._mac_nc, address)
        if (response := await request.send()) is None:
            if retry:
                return await self.retrieve_network_registration(address, retry=False)
            return None
        address = response.network_address
        if (mac_of_node := response.registered_mac) == "FFFFFFFFFFFFFFFF":
            mac_of_node = ""
        return (address, mac_of_node)

    def node_is_registered(self, mac: str) -> bool:
        """Return the network registration address for given mac."""
        if mac in self._registry:
            _LOGGER.debug("mac found in registry: %s", mac)
            return True
        return False

    async def update_network_nodetype(self, mac: str, node_type: NodeType) -> None:
        """Update NodeType inside registry and cache."""
        if self._network_cache is None or mac == "":
            return
        await self._network_cache.update_nodetypes(mac, node_type)

    def update_network_registration(self, mac: str) -> bool:
        """Add a mac to the network registration list return True if it was newly added."""
        if mac == "" or mac in self._registry:
            return False
        self._registry.append(mac)
        return True

    async def remove_network_registration(self, mac: str) -> None:
        """Remove a mac from the network registration list."""
        if mac in self._registry:
            self._registry.remove(mac)
            if self._network_cache is not None:
                await self._network_cache.prune_cache(self._registry)

    async def update_missing_registrations_circleplus(self) -> None:
        """Full retrieval of all (unknown) network registrations from network controller."""
        _maintenance_registry = []
        for address in range(0, 64):
            registration = await self.retrieve_network_registration(address, False)
            if registration is not None:
                currentaddress, mac = registration
                _LOGGER.debug(
                    "Network registration at address %s is %s",
                    str(currentaddress),
                    "'empty'" if mac == "" else f"set to {mac}",
                )
                if mac == "":
                    continue
                _maintenance_registry.append(mac)
                if self.update_network_registration(mac):
                    await self._exec_node_discover_callback(mac, None, False)
            await sleep(self._registration_scan_delay)
        _LOGGER.debug("CirclePlus registry scan finished")
        self._scan_completed = True
        if self._network_cache is not None:
            await self._network_cache.prune_cache(_maintenance_registry)
        if self._scan_completed_callback is not None:
            await self._scan_completed_callback()

    async def load_registrations_from_cache(self) -> None:
        """Quick retrieval of all unknown network registrations from cache."""
        if self._network_cache is None:
            self._registration_scan_delay = CIRCLEPLUS_SCANREQUEST_QUICK
            return
        if len(self._network_cache.nodetypes) < 4:
            self._registration_scan_delay = CIRCLEPLUS_SCANREQUEST_QUICK
            _LOGGER.warning(
                "Cache contains less than 4 nodes, fast registry scan enabled"
            )
        for mac, nodetype in self._network_cache.nodetypes.items():
            self.update_network_registration(mac)
            await self._exec_node_discover_callback(mac, nodetype, True)
            await sleep(0.1)
        _LOGGER.info("Cache network registration discovery finished")
        if self._scan_completed_callback is not None:
            await self._scan_completed_callback()

    def update_node_registration(self, mac: str) -> bool:
        """Register (re)joined node to Plugwise network and return True if newly added."""
        return self.update_network_registration(mac)

    def _stop_registration_task(self) -> None:
        """Stop the background registration task."""
        if self._registration_task is None:
            return
        self._registration_task.cancel()

    async def register_node(self, mac: str) -> None:
        """Register node to Plugwise network and return network address."""
        if not validate_mac(mac):
            raise NodeError(f"MAC '{mac}' invalid")

        request = NodeAddRequest(self._send_to_controller, bytes(mac, UTF8), True)
        try:
            await request.send()
        except StickError as exc:
            raise NodeError(f"{exc}") from exc
        if self.update_network_registration(mac):
            await self._exec_node_discover_callback(mac, None, False)

    async def unregister_node(self, mac: str) -> None:
        """Unregister node from current Plugwise network."""
        if not validate_mac(mac):
            raise NodeError(f"MAC {mac} invalid")

        if mac not in self._registry:
            raise NodeError(f"No existing Node ({mac}) found to unregister")

        request = NodeRemoveRequest(self._send_to_controller, self._mac_nc, mac)
        if (response := await request.send()) is None:
            raise NodeError(
                f"The Zigbee network coordinator '{self._mac_nc!r}'"
                + f" did not respond to unregister node '{mac}'"
            )
        if response.status.value != 1:
            raise NodeError(
                f"The Zigbee network coordinator '{self._mac_nc!r}'"
                + f" failed to unregister node '{mac}'"
            )

        await self.remove_network_registration(mac)

    async def clear_register_cache(self) -> None:
        """Clear current cache."""
        if self._network_cache is not None:
            await self._network_cache.clear_cache()
            self._cache_restored = False

    def stop(self) -> None:
        """Unload the network registry."""
        self._stop_registration_task()
