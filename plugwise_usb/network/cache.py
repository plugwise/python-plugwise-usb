"""Caching for plugwise network."""

from __future__ import annotations

import logging

from ..api import NodeType
from ..constants import CACHE_DATA_SEPARATOR
from ..helpers.cache import PlugwiseCache

_LOGGER = logging.getLogger(__name__)
_NETWORK_CACHE_FILE_NAME = "nodes.cache"


class NetworkRegistrationCache(PlugwiseCache):
    """Class to cache node network information."""

    def __init__(self, cache_root_dir: str = "") -> None:
        """Initialize NetworkCache class."""
        super().__init__(_NETWORK_CACHE_FILE_NAME, cache_root_dir)
        self._registrations: dict[int, tuple[str, NodeType | None]] = {}

    @property
    def registrations(self) -> dict[int, tuple[str, NodeType | None]]:
        """Cached network information."""
        return self._registrations

    async def save_cache(self) -> None:
        """Save the node information to file."""
        cache_data_to_save: dict[str, str] = {}
        for address in range(-1, 64, 1):
            mac, node_type = self._registrations.get(address, ("", None))
            if node_type is None:
                node_value = ""
            else:
                node_value = str(node_type)
            cache_data_to_save[str(address)] = (
                f"{mac}{CACHE_DATA_SEPARATOR}{node_value}"
            )
        await self.write_cache(cache_data_to_save)

    async def clear_cache(self) -> None:
        """Clear current cache."""
        self._registrations = {}
        await self.delete_cache()

    async def restore_cache(self) -> None:
        """Load the previously stored information."""
        data: dict[str, str] = await self.read_cache()
        self._registrations = {}
        for _key, _data in data.items():
            address = int(_key)
            try:
                if CACHE_DATA_SEPARATOR in _data:
                    values = _data.split(CACHE_DATA_SEPARATOR)
                else:
                    # legacy data separator can by remove at next version
                    values = _data.split(";")
                mac = values[0]
                node_type: NodeType | None = None
                if values[1] != "":
                    node_type = NodeType[values[1][9:]]
                self._registrations[address] = (mac, node_type)
                _LOGGER.debug(
                    "Restore registry address %s with mac %s with node type %s",
                    address,
                    mac if mac != "" else "<empty>",
                    str(node_type),
                )
            except (KeyError, IndexError):
                _LOGGER.warning(
                    "Skip invalid data '%s' in cache file '%s'",
                    _data,
                    self._cache_file,
                )

    def update_registration(
        self, address: int, mac: str, node_type: NodeType | None
    ) -> None:
        """Save node information in cache."""
        if self._registrations.get(address) is not None:
            _, current_node_type = self._registrations[address]
            if current_node_type is not None and node_type is None:
                return
        self._registrations[address] = (mac, node_type)
