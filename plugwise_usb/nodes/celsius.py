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

        if self._cache_enabled:
            _LOGGER.debug(
                "Load Celsius node %s from cache", self._node_info.mac
            )
            if await self._load_from_cache():
                pass

        self._loaded = True
        self._setup_protocol(
            CELSIUS_FIRMWARE_SUPPORT,
            (NodeFeature.INFO, NodeFeature.TEMPERATURE),
        )
        if await self.initialize():
            await self._loaded_callback(NodeEvent.LOADED, self.mac)
            return True
        _LOGGER.debug("Load of Celsius node %s failed", self._node_info.mac)
        return False
