"""Plugwise Scan node object."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any, Final

from ..api import NodeEvent, NodeFeature
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
from .helpers import raise_not_loaded
from .helpers.firmware import SCAN_FIRMWARE_SUPPORT

_LOGGER = logging.getLogger(__name__)

CACHE_MOTION = "motion"

# Defaults for Scan Devices

# Time in minutes the motion sensor should not sense motion to
# report "no motion" state
SCAN_MOTION_RESET_TIMER: Final = 5

# Default sensitivity of the motion sensors
SCAN_SENSITIVITY = MotionSensitivity.MEDIUM

# Light override
SCAN_DAYLIGHT_MODE: Final = False


class PlugwiseScan(NodeSED):
    """Plugwise Scan node."""

    async def load(self) -> bool:
        """Load and activate Scan node features."""
        if self._loaded:
            return True
        if self._cache_enabled:
            _LOGGER.debug(
                "Load Scan node %s from cache", self._node_info.mac
            )
            await self._load_from_cache()

        self._loaded = True
        self._setup_protocol(
            SCAN_FIRMWARE_SUPPORT,
            (NodeFeature.INFO, NodeFeature.MOTION),
        )
        if await self.initialize():
            await self._loaded_callback(NodeEvent.LOADED, self.mac)
            return True
        _LOGGER.debug("Load of Scan node %s failed", self._node_info.mac)
        return False

    @raise_not_loaded
    async def initialize(self) -> bool:
        """Initialize Scan node."""
        if self._initialized:
            return True
        self._scan_subscription = self._message_subscribe(
            self._switch_group,
            self._mac_in_bytes,
            (NODE_SWITCH_GROUP_ID,),
        )
        return await super().initialize()

    async def unload(self) -> None:
        """Unload node."""
        if self._scan_subscription is not None:
            self._scan_subscription()
        await super().unload()

    async def _switch_group(self, message: NodeSwitchGroupResponse) -> bool:
        """Switch group request from Scan."""
        await self._available_update_state(True)
        if message.power_state.value == 0:
            # turn off => clear motion
            await self.motion_state_update(False, message.timestamp)
            return True
        if message.power_state.value == 1:
            # turn on => motion
            await self.motion_state_update(True, message.timestamp)
            return True
        raise MessageError(
            f"Unknown power_state '{message.power_state.value}' "
            + f"received from {self.mac}"
        )

    async def motion_state_update(
        self, motion_state: bool, timestamp: datetime | None = None
    ) -> None:
        """Process motion state update."""
        self._motion_state.motion = motion_state
        self._motion_state.timestamp = timestamp
        state_update = False
        if motion_state:
            self._set_cache(CACHE_MOTION, "True")
            if self._motion is None or not self._motion:
                state_update = True
        if not motion_state:
            self._set_cache(CACHE_MOTION, "False")
            if self._motion is None or self._motion:
                state_update = True
        if state_update:
            self._motion = motion_state
            await self.publish_feature_update_to_subscribers(
                NodeFeature.MOTION, self._motion_state,
            )
            await self.save_cache()

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
        if response.node_ack_type == NodeAckResponseType.SCAN_CONFIG_FAILED:
            raise NodeError(
                f"Scan {self.mac} failed to configure scan settings"
            )
        if response.node_ack_type == NodeAckResponseType.SCAN_CONFIG_ACCEPTED:
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
        if response.node_ack_type == NodeAckResponseType.SCAN_LIGHT_CALIBRATION_ACCEPTED:
            return True
        return False

    @raise_not_loaded
    async def get_state(
        self, features: tuple[NodeFeature]
    ) -> dict[NodeFeature, Any]:
        """Update latest state for given feature."""
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
                state_result = await super().get_state((feature,))
                states[feature] = state_result[feature]

        states[NodeFeature.AVAILABLE] = self._available
        return states
