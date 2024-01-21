"""Caching for plugwise node"""

from __future__ import annotations

import logging
from pathlib import Path, PurePath

import aiofiles
import aiofiles.os

from ...constants import CACHE_SEPARATOR, UTF8
from ...util import get_writable_cache_dir

_LOGGER = logging.getLogger(__name__)


class NodeCache:
    """Class to cache specific node configuration and states"""

    def __init__(self, mac: str, cache_root_dir: str = "") -> None:
        """Initialize NodeCache class."""
        self._mac = mac
        self._states: dict[str, str] = {}
        self._cache_file: PurePath | None = None
        self._cache_root_dir = cache_root_dir
        self._set_cache_file(cache_root_dir)

    @property
    def cache_root_directory(self) -> str:
        """Root directory to store the plugwise cache directory."""
        return self._cache_root_dir

    @cache_root_directory.setter
    def cache_root_directory(self, cache_root_dir: str = "") -> None:
        """Root directory to store the plugwise cache directory."""
        self._cache_root_dir = cache_root_dir
        self._set_cache_file(cache_root_dir)

    def _set_cache_file(self, cache_root_dir: str) -> None:
        """Set (and create) the plugwise cache directory to store cache."""
        self._cache_root_dir = get_writable_cache_dir(cache_root_dir)
        Path(self._cache_root_dir).mkdir(parents=True, exist_ok=True)
        self._cache_file = Path(f"{self._cache_root_dir}/{self._mac}.cache")

    @property
    def states(self) -> dict[str, str]:
        """cached node state information"""
        return self._states

    def add_state(self, state: str, value: str) -> None:
        """Add configuration state to cache."""
        self._states[state] = value

    def remove_state(self, state: str) -> None:
        """Remove configuration state from cache."""
        if self._states.get(state) is not None:
            self._states.pop(state)

    def get_state(self, state: str) -> str | None:
        """Return current value for state"""
        return self._states.get(state, None)

    async def save_cache(self) -> None:
        """Save the node configuration to file."""
        async with aiofiles.open(
            file=self._cache_file,
            mode="w",
            encoding=UTF8,
        ) as file_data:
            for key, state in self._states.copy().items():
                await file_data.write(
                    f"{key}{CACHE_SEPARATOR}{state}\n"
                )
        _LOGGER.debug(
            "Cached settings saved to cache file %s",
            str(self._cache_file),
        )

    async def clear_cache(self) -> None:
        """Clear current cache."""
        self._states = {}
        await self.delete_cache_file()

    async def restore_cache(self) -> bool:
        """Load the previously store state information."""
        try:
            async with aiofiles.open(
                file=self._cache_file,
                mode="r",
                encoding=UTF8,
            ) as file_data:
                lines = await file_data.readlines()
        except OSError:
            _LOGGER.warning(
                "Failed to read cache file %s", str(self._cache_file)
            )
            return False
        self._states.clear()
        for line in lines:
            data = line.strip().split(CACHE_SEPARATOR)
            if len(data) != 2:
                _LOGGER.warning(
                    "Skip invalid line '%s' in cache file %s",
                    line,
                    str(self._cache_file)
                )
                break
            self._states[data[0]] = data[1]
        _LOGGER.debug(
            "Cached settings restored %s lines from cache file %s",
            str(len(self._states)),
            str(self._cache_file),
        )
        return True

    async def delete_cache_file(self) -> None:
        """Delete cache file"""
        if self._cache_file is None:
            return
        if not await aiofiles.os.path.exists(self._cache_file):
            return
        await aiofiles.os.remove(self._cache_file)
