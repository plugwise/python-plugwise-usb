"""Plugwise Scan node object."""

from __future__ import annotations
from asyncio import create_task

from datetime import datetime, UTC
import logging
from typing import Any, Final

from .helpers import raise_not_loaded
from ..api import NodeFeature
from ..constants import MotionSensitivity
from ..exceptions import MessageError, NodeError, NodeTimeout
from ..messages.requests import ScanConfigureRequest, ScanLightCalibrateRequest
from ..messages.responses import (
    NODE_SWITCH_GROUP_ID,
    NodeAckResponse,
    NodeAckResponseType,
    NodeSwitchGroupResponse,
)
from ..nodes.sed import NodeSED

_LOGGER = logging.getLogger(__name__)


# Defaults for Scan Devices

# Time in minutes the motion sensor should not sense motion to
# report "no motion" state
SCAN_MOTION_RESET_TIMER: Final = 5

# Default sensitivity of the motion sensors
SCAN_SENSITIVITY = MotionSensitivity.MEDIUM

# Light override
SCAN_DAYLIGHT_MODE: Final = False

# Minimum and maximum supported (custom) zigbee protocol version based on
# utc timestamp of firmware extracted from "Plugwise.IO.dll" file of Plugwise
# source installation
SCAN_FIRMWARE: Final = {
    datetime(2010, 11, 4, 16, 58, 46, tzinfo=UTC): (
        "2.0",
        "2.6",
    ),  # Beta Scan Release
    datetime(2011, 1, 12, 8, 32, 56, tzinfo=UTC): (
        "2.0",
        "2.5",
    ),  # Beta Scan Release
    datetime(2011, 3, 4, 14, 43, 31, tzinfo=UTC): ("2.0", "2.5"),  # Scan RC1
    datetime(2011, 3, 28, 9, 0, 24, tzinfo=UTC): ("2.0", "2.5"),
    datetime(2011, 5, 13, 7, 21, 55, tzinfo=UTC): ("2.0", "2.5"),
    datetime(2011, 11, 3, 13, 0, 56, tzinfo=UTC): ("2.0", "2.6"),  # Legrand
    datetime(2011, 6, 27, 8, 55, 44, tzinfo=UTC): ("2.0", "2.5"),
    datetime(2017, 7, 11, 16, 8, 3, tzinfo=UTC): (
        "2.0",
        "2.6",
    ),  # New Flash Update
}
SCAN_FEATURES: Final = (NodeFeature.INFO, NodeFeature.MOTION)


class PlugwiseScan(NodeSED):
    """provides interface to the Plugwise Scan nodes"""

    async def async_load(self) -> bool:
        """Load and activate Scan node features."""
        if self._loaded:
            return True
        self._node_info.battery_powered = True
        if self._cache_enabled:
            _LOGGER.debug(
                "Load Scan node %s from cache", self._node_info.mac
            )
            if await self._async_load_from_cache():
                self._loaded = True
                self._load_features()
                return await self.async_initialize()

        _LOGGER.debug("Load of Scan node %s failed", self._node_info.mac)
        return False

    @raise_not_loaded
    async def async_initialize(self) -> bool:
        """Initialize Scan node."""
        if self._initialized:
            return True
        self._initialized = True
        if not await super().async_initialize():
            self._initialized = False
            return False
        self._scan_subscription = self._message_subscribe(
            self._switch_group,
            self._mac_in_bytes,
            (NODE_SWITCH_GROUP_ID,),
        )
        self._initialized = True
        return True

    async def async_unload(self) -> None:
        """Unload node."""
        if self._scan_subscription is not None:
            self._scan_subscription()
        await super().async_unload()

    async def _switch_group(self, message: NodeSwitchGroupResponse) -> None:
        """Switch group request from Scan."""
        self._available_update_state(True)
        if message.power_state.value == 0:
            # turn off => clear motion
            await self.async_motion_state_update(False, message.timestamp)
        elif message.power_state.value == 1:
            # turn on => motion
            await self.async_motion_state_update(True, message.timestamp)
        else:
            raise MessageError(
                f"Unknown power_state '{message.power_state.value}' "
                + f"received from {self.mac}"
            )

    async def async_motion_state_update(
        self, motion_state: bool, timestamp: datetime | None = None
    ) -> None:
        """Process motion state update."""
        self._motion_state.motion = motion_state
        self._motion_state.timestamp = timestamp
        state_update = False
        if motion_state:
            self._set_cache("motion", "True")
            if self._motion is None or not self._motion:
                state_update = True
        if not motion_state:
            self._set_cache("motion", "False")
            if self._motion is None or self._motion:
                state_update = True
        if state_update:
            self._motion = motion_state
            create_task(
                self.publish_event(
                    NodeFeature.MOTION,
                    self._motion_state,
                )
            )
            if self.cache_enabled and self._loaded and self._initialized:
                create_task(self.async_save_cache())

    async def scan_configure(
        self,
        motion_reset_timer: int = SCAN_MOTION_RESET_TIMER,
        sensitivity_level: MotionSensitivity = MotionSensitivity.MEDIUM,
        daylight_mode: bool = SCAN_DAYLIGHT_MODE,
    ) -> bool:
        """Configure Scan device settings. Returns True if successful."""
        # Default to medium:
        sensitivity_value = 30  # b'1E'
        if sensitivity_level == MotionSensitivity.HIGH:
            sensitivity_value = 20  # b'14'
        if sensitivity_level == MotionSensitivity.OFF:
            sensitivity_value = 255  # b'FF'

        response: NodeAckResponse | None = await self._send(
            ScanConfigureRequest(
                self._mac_in_bytes,
                motion_reset_timer,
                sensitivity_value,
                daylight_mode,
            )
        )
        if response is None:
            raise NodeTimeout(
                f"No response from Scan device {self.mac} "
                + "for configuration request."
            )
        if response.ack_id == NodeAckResponseType.SCAN_CONFIG_FAILED:
            raise NodeError(
                f"Scan {self.mac} failed to configure scan settings"
            )
        if response.ack_id == NodeAckResponseType.SCAN_CONFIG_ACCEPTED:
            self._motion_reset_timer = motion_reset_timer
            self._sensitivity_level = sensitivity_level
            self._daylight_mode = daylight_mode
            return True
        return False

    async def scan_calibrate_light(self) -> bool:
        """Request to calibration light sensitivity of Scan device."""
        response: NodeAckResponse | None = await self._send(
            ScanLightCalibrateRequest(self._mac_in_bytes)
        )
        if response is None:
            raise NodeTimeout(
                f"No response from Scan device {self.mac} "
                + "to light calibration request."
            )
        if (
            response.ack_id
            == NodeAckResponseType.SCAN_LIGHT_CALIBRATION_ACCEPTED
        ):
            return True
        return False

    def _load_features(self) -> None:
        """Enable additional supported feature(s)"""
        self._setup_protocol(SCAN_FIRMWARE)
        self._features += SCAN_FEATURES
        self._node_info.features = self._features

    async def async_get_state(
        self, features: tuple[NodeFeature]
    ) -> dict[NodeFeature, Any]:
        """Update latest state for given feature."""
        if not self._loaded:
            if not await self.async_load():
                _LOGGER.warning(
                    "Unable to update state because load node %s failed",
                    self.mac
                )
        states: dict[NodeFeature, Any] = {}
        for feature in features:
            _LOGGER.debug(
                "Updating node %s - feature '%s'",
                self._node_info.mac,
                feature,
            )
            if feature not in self._features:
                raise NodeError(
                    f"Update of feature '{feature.name}' is "
                    + f"not supported for {self.mac}"
                )
            if feature == NodeFeature.MOTION:
                states[NodeFeature.MOTION] = self._motion_state
            else:
                state_result = await super().async_get_state([feature])
                states[feature] = state_result[feature]
        return states
