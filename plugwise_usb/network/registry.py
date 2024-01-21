"""Network register"""

from __future__ import annotations
from asyncio import Task, create_task, sleep
from collections.abc import Awaitable, Callable, Coroutine
from copy import deepcopy
import logging
from typing import Any

from .cache import NetworkRegistrationCache
from ..api import NodeType
from ..constants import UTF8
from ..exceptions import NodeError
from ..messages.responses import (
    CirclePlusScanResponse,
    NodeRemoveResponse,
    NodeResponse,
    NodeResponseType,
    PlugwiseResponse,
)
from ..messages.requests import (
    CirclePlusScanRequest,
    NodeRemoveRequest,
    NodeAddRequest,
)
from ..util import validate_mac

_LOGGER = logging.getLogger(__name__)


class StickNetworkRegister():
    """Network register"""

    def __init__(
        self,
        mac_network_controller: bytes,
        send_fn: Callable[[Any], Coroutine[Any, Any, PlugwiseResponse]]
    ) -> None:
        """Initialize network register"""
        self._mac_nc = mac_network_controller
        self._send_to_controller = send_fn
        self._cache_folder: str = ""
        self._cache_restored = False
        self._cache_enabled = False
        self._network_cache: NetworkRegistrationCache | None = None
        self._loaded: bool = False
        self._registry: dict[int, tuple[str, NodeType | None]] = {}
        self._first_free_address: int = 65
        self._registration_task: Task | None = None
        self._quick_scan_finished: Awaitable | None = None
        self._full_scan_finished: Awaitable | None = None
# region Properties

    @property
    def cache_enabled(self) -> bool:
        """Return usage of cache."""
        return self._cache_enabled

    @cache_enabled.setter
    def cache_enabled(self, enable: bool = True) -> None:
        """Enable or disable usage of cache."""
        if enable and not self._cache_enabled:
            _LOGGER.debug("Cache is enabled")
            self._network_cache = NetworkRegistrationCache(self._cache_folder)
        elif not enable and self._cache_enabled:
            if self._network_cache is not None:
                create_task(
                    self._network_cache.delete_cache_file()
                )
            _LOGGER.debug("Cache is disabled")
        self._cache_enabled = enable

    @property
    def cache_folder(self) -> str:
        """path to cache data"""
        return self._cache_folder

    @cache_folder.setter
    def cache_folder(self, cache_folder: str) -> None:
        """Set path to cache data"""
        if cache_folder == self._cache_folder:
            return
        self._cache_folder = cache_folder

    @property
    def registry(self) -> dict[int, tuple[str, NodeType | None]]:
        """Return dictionary with all joined nodes."""
        return deepcopy(self._registry)

    def quick_scan_finished(self, callback: Awaitable) -> None:
        """Register method to be called when quick scan is finished"""
        self._quick_scan_finished = callback

    def full_scan_finished(self, callback: Awaitable) -> None:
        """Register method to be called when full scan is finished"""
        self._full_scan_finished = callback

# endregion

    async def start(self) -> None:
        """Initialize load the network registry"""
        if self._cache_enabled:
            await self.restore_network_cache()
            await sleep(0)
            await self.load_registry_from_cache()
            await sleep(0)
        await self.update_missing_registrations(quick=True)

    async def restore_network_cache(self) -> None:
        """Restore previously saved cached network and node information"""
        if self._network_cache is None:
            _LOGGER.error(
                "Unable to restore cache when cache is not initialized"
            )
            return
        if not self._cache_restored:
            await self._network_cache.restore_cache()
        self._cache_restored = True

    async def load_registry_from_cache(self) -> None:
        """Load network registry from cache"""
        if self._network_cache is None:
            _LOGGER.error(
                "Unable to restore network registry because " +
                "cache is not initialized"
            )
            return
        if self._cache_restored:
            return
        for address, registration in self._network_cache.registrations.items():
            mac, node_type = registration
            if self._registry.get(address) is None:
                self._registry[address] = (mac, node_type)

    async def retrieve_network_registration(
        self, address: int, retry: bool = True
    ) -> tuple[int, str] | None:
        """Return the network mac registration of specified address."""
        response: CirclePlusScanResponse | None = (
            await self._send_to_controller(
                CirclePlusScanRequest(self._mac_nc, address),
            )  # type: ignore [assignment]
        )
        if response is None:
            if retry:
                return await self.retrieve_network_registration(
                    address, retry=False
                )
            return None
        address = response.network_address
        mac_of_node = response.registered_mac
        if (mac_of_node := response.registered_mac) == 'FFFFFFFFFFFFFFFF':
            mac_of_node = ""
        return (address, mac_of_node)

    def network_address(self, mac: str) -> int | None:
        """Return the network registration address for given mac"""
        for address, registration in self._registry.items():
            registered_mac, _ = registration
            if mac == registered_mac:
                return address
        return None

    def network_controller(self) -> tuple[int, NodeType | None]:
        """Return the registration for the network controller."""
        if self._registry.get(-1) is not None:
            return self.registry[-1]
        return (-1, None)

    def update_network_registration(
        self, address: int, mac: str, node_type: NodeType | None
    ) -> None:
        """Add a network registration"""
        if self._registry.get(address) is not None:
            _, current_type = self._registry[address]
            if current_type is not None and node_type is None:
                return
        self._registry[address] = (mac, node_type)
        if self._network_cache is not None:
            self._network_cache.update_registration(address, mac, node_type)

    async def update_missing_registrations(
        self, quick: bool = False
    ) -> None:
        """
        Retrieve all unknown network registrations
        from network controller
        """
        for address in range(0, 64):
            if self._registry.get(address) is not None and not quick:
                mac, _ = self._registry[address]
                if mac == "":
                    self._first_free_address = min(
                        self._first_free_address, address
                    )
                continue
            registration = await self.retrieve_network_registration(
                address, False
            )
            if registration is not None:
                address, mac = registration
                if mac == "":
                    self._first_free_address = min(
                        self._first_free_address, address
                    )
                    if quick:
                        break
                _LOGGER.debug(
                    "Network registration at address %s is %s",
                    str(address),
                    "'empty'" if mac == "" else f"set to {mac}",
                )
                self.update_network_registration(address, mac, None)
            await sleep(0.1)
            if not quick:
                await sleep(10)
        if quick:
            if (
                self._registration_task is None or
                self._registration_task.done()
            ):
                self._registration_task = create_task(
                    self.update_missing_registrations(quick=False)
                )
                if self._quick_scan_finished is not None:
                    await self._quick_scan_finished
                _LOGGER.info("Quick network registration discovery finished")
        else:
            _LOGGER.debug("Full network registration finished, save to cache")
            if self._cache_enabled:
                _LOGGER.debug("Full network registration finished, pre")
                await self.save_registry_to_cache()
                _LOGGER.debug("Full network registration finished, post")
            _LOGGER.info("Full network registration discovery completed")
            if self._full_scan_finished is not None:
                await self._full_scan_finished

    def _stop_registration_task(self) -> None:
        """Stop the background registration task"""
        if self._registration_task is None:
            return
        self._registration_task.cancel()

    async def save_registry_to_cache(self) -> None:
        """Save network registry to cache"""
        if self._network_cache is None:
            _LOGGER.error(
                "Unable to save network registry because " +
                "cache is not initialized"
            )
            return
        _LOGGER.debug(
            "save_registry_to_cache starting for %s items",
            str(len(self._registry))
        )
        for address, registration in self._registry.items():
            mac, node_type = registration
            self._network_cache.update_registration(address, mac, node_type)
        await self._network_cache.save_cache()
        _LOGGER.debug(
            "save_registry_to_cache finished"
        )

    async def register_node(self, mac: str) -> int:
        """
        Register node to Plugwise network.
        Return network address
        """
        if not validate_mac(mac):
            raise NodeError(f"Invalid mac '{mac}' to register")

        response: NodeResponse | None = await self._send_to_controller(
            NodeAddRequest(bytes(mac, UTF8), True)
        )  # type: ignore [assignment]
        if (
            response is None or
            response.ack_id != NodeResponseType.JOIN_ACCEPTED
        ):
            raise NodeError(f"Failed to register node {mac}")
        self.update_network_registration(self._first_free_address, mac, None)
        self._first_free_address += 1
        return self._first_free_address - 1

    async def unregister_node(self, mac: str) -> None:
        """Unregister node from current Plugwise network."""
        if not validate_mac(mac):
            raise NodeError(f"Invalid mac '{mac}' to unregister")
        if mac not in self._registry:
            raise NodeError(
                f"No existing registration '{mac}' found to unregister"
            )

        response: NodeRemoveResponse | None = await self._send_to_controller(
            NodeRemoveRequest(self._mac_nc, mac)
        )  # type: ignore [assignment]
        if response is None:
            raise NodeError(
                f"The Zigbee network coordinator '{self._mac_nc}'" +
                f" did not respond to unregister node '{mac}'"
            )
        if response.status.value != 1:
            raise NodeError(
                f"The Zigbee network coordinator '{self._mac_nc}'" +
                f" failed to unregister node '{mac}'"
            )
        if (address := self.network_address(mac)) is not None:
            self.update_network_registration(address, mac, None)

    async def clear_register_cache(self) -> None:
        """Clear current cache."""
        if self._network_cache is not None:
            await self._network_cache.clear_cache()
            self._cache_restored = False

    async def stop(self) -> None:
        """Unload the network registry"""
        self._stop_registration_task()
        if self._cache_enabled:
            await self.save_registry_to_cache()