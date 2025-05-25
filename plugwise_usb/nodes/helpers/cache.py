"""Caching for plugwise node."""

from __future__ import annotations

import logging

from ...helpers.cache import PlugwiseCache

_LOGGER = logging.getLogger(__name__)


class NodeCache(PlugwiseCache):
    """Class to cache specific node configuration and states."""

    def __init__(self, mac: str, cache_root_dir: str = "") -> None:
        """Initialize NodeCache class."""
        self._mac = mac
        self._node_cache_file_name = f"{mac}.cache"
        super().__init__(self._node_cache_file_name, cache_root_dir)
        self._states: dict[str, str] = {}

    @property
    def states(self) -> dict[str, str]:
        """Cached node state information."""
        return self._states

    def update_state(self, state: str, value: str) -> None:
        """Add configuration state to cache."""
        self._states[state] = value

    def remove_state(self, state: str) -> None:
        """Remove configuration state from cache."""
        if self._states.get(state) is not None:
            self._states.pop(state)

    def get_state(self, state: str) -> str | None:
        """Return current value for state."""
        return self._states.get(state, None)

    async def save_cache(self, rewrite: bool = False) -> None:
        """Save the node configuration to file."""
        await self.write_cache(self._states, rewrite)
        _LOGGER.debug(
            "Cached settings saved to cache file %s",
            str(self._cache_file),
        )

    async def clear_cache(self) -> None:
        """Clear current cache."""
        self._states = {}
        await self.delete_cache()

    async def restore_cache(self) -> bool:
        """Load the previously store state information."""
        data: dict[str, str] = await self.read_cache()
        self._states.clear()
        for key, value in data.items():
            self._states[key] = value
        return True
