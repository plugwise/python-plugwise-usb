"""Caching for plugwise network."""

from __future__ import annotations

import logging

from ..api import NodeType
from ..helpers.cache import PlugwiseCache

_LOGGER = logging.getLogger(__name__)
_NETWORK_CACHE_FILE_NAME = "nodetype.cache"


class NetworkRegistrationCache(PlugwiseCache):
    """Class to cache node network information."""

    def __init__(self, cache_root_dir: str = "") -> None:
        """Initialize NetworkCache class."""
        super().__init__(_NETWORK_CACHE_FILE_NAME, cache_root_dir)
        self._nodetypes: dict[str, NodeType] = {}

    @property
    def nodetypes(self) -> dict[str, NodeType | None]:
        """Cached network information."""
        return self._nodetypes

    async def save_cache(self) -> None:
        """Save the node information to file."""
        cache_data_to_save: dict[str, str] = {}
        for mac, node_type in self._nodetypes.items():
            node_value = str(node_type)
            cache_data_to_save[mac] = node_value
        _LOGGER.debug("Save NodeTypes %s", str(len(cache_data_to_save)))
        await self.write_cache(cache_data_to_save)

    async def clear_cache(self) -> None:
        """Clear current cache."""
        self._nodetypes = {}
        await self.delete_cache()

    async def restore_cache(self) -> None:
        """Load the previously stored information."""
        data: dict[str, str] = await self.read_cache()
        self._nodetypes = {}
        for mac, node_value in data.items():
            node_type: NodeType | None = None
            if len(node_value) >= 10:
                node_type = NodeType[node_value[9:]]
            if node_type is not None:
                self._nodetypes[mac] = node_type
            _LOGGER.debug(
                "Restore NodeType for mac %s with node type %s",
                mac,
                str(node_type),
            )

    async def update_nodetypes(self, mac: str, node_type: NodeType | None) -> None:
        """Save node information in cache."""
        if node_type is None:
            return
        if (current_node_type := self._nodetypes.get(mac)) is not None:
            if current_node_type == node_type:
                return
            _LOGGER.warning(
                "Cache contained mismatched NodeType %s replacing with %s",
                str(current_node_type),
                str(node_type),
            )
        self._nodetypes[mac] = node_type
        await self.save_cache()
