"""Caching for plugwise network"""

from __future__ import annotations

import aiofiles
import aiofiles.os
import logging
from pathlib import Path, PurePath

from ..util import get_writable_cache_dir
from ..api import NodeType
from ..constants import CACHE_SEPARATOR, UTF8
from ..exceptions import CacheError

_LOGGER = logging.getLogger(__name__)


class NetworkRegistrationCache:
    """Class to cache node network information"""

    def __init__(self, cache_root_dir: str = "") -> None:
        """Initialize NetworkCache class."""
        self._registrations: dict[int, tuple[str, NodeType | None]] = {}
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
        self._cache_file = Path(f"{self._cache_root_dir}/nodes.cache")
        _LOGGER.info(
            "Start using network cache file: %s/nodes.cache",
            self._cache_root_dir
        )

    @property
    def registrations(self) -> dict[int, tuple[str, NodeType]]:
        """Cached network information"""
        return self._registrations

    async def async_save_cache(self) -> None:
        """Save the node information to file."""
        _LOGGER.debug("Save network cache %s", str(self._cache_file))
        counter = 0
        async with aiofiles.open(
            file=self._cache_file,
            mode="w",
            encoding=UTF8,
        ) as file_data:
            for address in sorted(self._registrations.keys()):
                counter += 1
                mac, node_reg = self._registrations[address]
                if node_reg is None:
                    node_type = ""
                else:
                    node_type = str(node_reg)
                await file_data.write(
                    f"{address}{CACHE_SEPARATOR}" +
                    f"{mac}{CACHE_SEPARATOR}" +
                    f"{node_type}\n"
                )
        _LOGGER.info(
            "Saved %s lines to network cache %s",
            str(counter),
            str(self._cache_file)
        )

    async def async_clear_cache(self) -> None:
        """Clear current cache."""
        self._registrations = {}
        await self.async_delete_cache_file()

    async def async_restore_cache(self) -> bool:
        """Load the previously stored information."""
        if self._cache_file is None:
            raise CacheError(
                "Cannot restore cached information " +
                "without reference to cache file"
            )
        if not await aiofiles.os.path.exists(self._cache_file):
            _LOGGER.warning(
                "Unable to restore from cache because " +
                "file '%s' does not exists",
                self._cache_file.name,
            )
            return False
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
        else:
            self._registrations = {}
            for line in lines:
                data = line.strip().split(CACHE_SEPARATOR)
                if len(data) != 3:
                    _LOGGER.warning(
                        "Skip invalid line '%s' in cache file %s",
                        line,
                        self._cache_file.name,
                    )
                    break
                address = int(data[0])
                mac = data[1]
                node_type: NodeType | None = None
                if data[2] != "":
                    try:
                        node_type = NodeType[data[2][9:]]
                    except KeyError:
                        _LOGGER.warning(
                            "Skip invalid NodeType '%s' " +
                            "in data '%s' in cache file '%s'",
                            data[2][9:],
                            line,
                            self._cache_file.name,
                        )
                        break
                self._registrations[address] = (mac, node_type)
                _LOGGER.debug(
                    "Restore registry address %s with mac %s " +
                    "with node type %s",
                    address,
                    mac if mac != "" else "<empty>",
                    str(node_type),
                )
            return True

    async def async_delete_cache_file(self) -> None:
        """Delete cache file"""
        if self._cache_file is None:
            return
        if not await aiofiles.os.path.exists(self._cache_file):
            return
        await aiofiles.os.remove(self._cache_file)

    def update_registration(
        self, address: int, mac: str, node_type: NodeType | None
    ) -> None:
        """Save node information in cache"""
        if self._registrations.get(address) is not None:
            _, current_node_type = self._registrations[address]
            if current_node_type is not None and node_type is None:
                return
        self._registrations[address] = (mac, node_type)
