"""Base class for local caching of data."""

from __future__ import annotations

from asyncio import get_running_loop
import logging
from os import getenv as os_getenv, name as os_name
from os.path import expanduser as os_path_expand_user, join as os_path_join

from aiofiles import open as aiofiles_open, ospath  # type: ignore[import-untyped]
from aiofiles.os import (  # type: ignore[import-untyped]
    makedirs,
    remove as aiofiles_os_remove,
)

from ..constants import CACHE_DIR, CACHE_KEY_SEPARATOR, UTF8
from ..exceptions import CacheError

_LOGGER = logging.getLogger(__name__)


class PlugwiseCache:
    """Base class to cache plugwise information."""

    def __init__(self, file_name: str, root_dir: str = "") -> None:
        """Initialize class."""
        self._root_dir = root_dir
        self._file_name = file_name
        self._cache_file_exists: bool = False
        self._cache_path: str | None = None
        self._cache_file: str | None = None
        self._initialized = False
        self._loop = get_running_loop()

    @property
    def initialized(self) -> bool:
        """Indicate if cache file is initialized."""
        return self._initialized

    @property
    def cache_root_directory(self) -> str:
        """Root directory to store the plugwise cache directory."""
        return self._root_dir

    @cache_root_directory.setter
    def cache_root_directory(self, cache_root_dir: str = "") -> None:
        """Root directory to store the plugwise cache directory."""
        if self._root_dir != cache_root_dir:
            self._initialized = False
        self._root_dir = cache_root_dir

    async def initialize_cache(self, create_root_folder: bool = False) -> None:
        """Set (and create) the plugwise cache directory to store cache file."""
        if self._root_dir != "":
            if not create_root_folder and not await ospath.exists(self._root_dir):
                raise CacheError(
                    f"Unable to initialize caching. Cache folder '{self._root_dir}' does not exists."
                )
            cache_dir = self._root_dir
        else:
            cache_dir = self._get_writable_os_dir()
        await makedirs(cache_dir, exist_ok=True)
        self._cache_path = cache_dir

        self._cache_file = os_path_join(self._cache_path, self._file_name)
        self._cache_file_exists = await ospath.exists(self._cache_file)
        self._initialized = True
        _LOGGER.debug("Start using network cache file: %s", self._cache_file)

    def _get_writable_os_dir(self) -> str:
        """Return the default caching directory based on the OS."""
        if self._root_dir != "":
            return self._root_dir
        if os_name == "nt":
            if (data_dir := os_getenv("APPDATA")) is not None:
                return os_path_join(data_dir, CACHE_DIR)
            raise CacheError(
                "Unable to detect writable cache folder based on 'APPDATA' environment variable."
            )
        return os_path_join(os_path_expand_user("~"), CACHE_DIR)

    async def write_cache(self, data: dict[str, str], rewrite: bool = False) -> None:
        """Save information to cache file."""
        if not self._initialized:
            raise CacheError(
                f"Unable to save cache. Initialize cache file '{self._file_name}' first."
            )

        current_data: dict[str, str] = {}
        if not rewrite:
            current_data = await self.read_cache()
        processed_keys: list[str] = []
        data_to_write: list[str] = []
        for _cur_key, _cur_val in current_data.items():
            _write_val = _cur_val
            if _cur_key in data:
                _write_val = data[_cur_key]
                processed_keys.append(_cur_key)
            data_to_write.append(f"{_cur_key}{CACHE_KEY_SEPARATOR}{_write_val}\n")
        # Write remaining new data
        for _key, _value in data.items():
            if _key not in processed_keys:
                data_to_write.append(f"{_key}{CACHE_KEY_SEPARATOR}{_value}\n")

        try:
            async with aiofiles_open(
                file=self._cache_file,
                mode="w",
                encoding=UTF8,
            ) as file_data:
                await file_data.writelines(data_to_write)
        except OSError as exc:
            _LOGGER.warning(
                "%s while writing data to cache file %s", exc, str(self._cache_file)
            )
        else:
            if not self._cache_file_exists:
                self._cache_file_exists = True
            _LOGGER.debug(
                "Saved %s lines to cache file %s", str(len(data)), self._cache_file
            )

    async def read_cache(self) -> dict[str, str]:
        """Return current data from cache file."""
        if not self._initialized:
            raise CacheError(
                f"Unable to save cache. Initialize cache file '{self._file_name}' first."
            )
        current_data: dict[str, str] = {}
        if not self._cache_file_exists:
            _LOGGER.debug(
                "Cache file '%s' does not exists, return empty cache data",
                self._cache_file,
            )
            return current_data
        try:
            async with aiofiles_open(
                file=self._cache_file,
                encoding=UTF8,
            ) as read_file_data:
                lines: list[str] = await read_file_data.readlines()
        except OSError as exc:
            # suppress file errors as this is expected the first time
            # when no cache file exists yet.
            _LOGGER.warning(
                "OS error %s while reading cache file %s", exc, str(self._cache_file)
            )
            return current_data

        for line in lines:
            data = line.strip()
            if (index_separator := data.find(CACHE_KEY_SEPARATOR)) == -1:
                _LOGGER.warning(
                    "Skip invalid line '%s' in cache file %s",
                    data,
                    str(self._cache_file),
                )
                break
            current_data[data[:index_separator]] = data[index_separator + 1 :]
        return current_data

    async def delete_cache(self) -> None:
        """Delete cache file."""
        if self._cache_file is None:
            return
        if await ospath.exists(self._cache_file):
            await aiofiles_os_remove(self._cache_file)
