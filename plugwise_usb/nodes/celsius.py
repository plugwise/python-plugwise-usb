"""Plugwise Celsius node.

TODO: Finish node
"""

from __future__ import annotations

import logging
from typing import Final

from ..api import NodeEvent, NodeFeature
from ..nodes.sed import NodeSED
from .helpers.firmware import CELSIUS_FIRMWARE_SUPPORT

_LOGGER = logging.getLogger(__name__)

CELSIUS_FEATURES: Final = (
    NodeFeature.INFO,
    NodeFeature.TEMPERATURE,
    NodeFeature.HUMIDITY,
)


class PlugwiseCelsius(NodeSED):
    """provides interface to the Plugwise Celsius nodes."""

    async def load(self) -> bool:
        """Load and activate node features."""
        if self._loaded:
            return True

        self._node_info.is_battery_powered = True
        mac = self._node_info.mac
        if self._cache_enabled:
            _LOGGER.debug("Loading Celsius node %s from cache", mac)
            if not await self._load_from_cache():
                _LOGGER.debug("Loading Celsius node %s from cache failed", mac)

        self._loaded = True
        self._setup_protocol(
            CELSIUS_FIRMWARE_SUPPORT,
            (NodeFeature.INFO, NodeFeature.TEMPERATURE),
        )
        if await self.initialize():
            await self._loaded_callback(NodeEvent.LOADED, mac)
            return True

        _LOGGER.debug("Loading of Celsius node %s failed", mac)
        return False
