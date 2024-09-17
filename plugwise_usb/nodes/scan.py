"""Plugwise Scan node object."""

from __future__ import annotations

from asyncio import Task
from collections.abc import Awaitable, Callable, Coroutine
from datetime import datetime
import logging
from typing import Any, Final

from ..api import MotionSensitivity, NodeEvent, NodeFeature
from ..connection import StickController
from ..exceptions import MessageError, NodeError, NodeTimeout
from ..messages.requests import ScanConfigureRequest, ScanLightCalibrateRequest
from ..messages.responses import (
    NODE_SWITCH_GROUP_ID,
    NodeAckResponseType,
    NodeSwitchGroupResponse,
    PlugwiseResponse,
)
from ..nodes.sed import NodeSED
from .helpers import raise_not_loaded
from .helpers.firmware import SCAN_FIRMWARE_SUPPORT

_LOGGER = logging.getLogger(__name__)

CACHE_MOTION_STATE = "motion_state"
CACHE_MOTION_TIMESTAMP = "motion_timestamp"
CACHE_MOTION_RESET_TIMER = "motion_reset_timer"

CACHE_SCAN_SENSITIVITY = "scan_sensitivity_level"
CACHE_SCAN_DAYLIGHT_MODE = "scan_daylight_mode"


# region Defaults for Scan Devices

# Time in minutes the motion sensor should not sense motion to
# report "no motion" state
SCAN_DEFAULT_MOTION_RESET_TIMER: Final = 5

# Default sensitivity of the motion sensors
SCAN_DEFAULT_SENSITIVITY: Final = MotionSensitivity.MEDIUM

# Light override
SCAN_DEFAULT_DAYLIGHT_MODE: Final = False

# endregion


class PlugwiseScan(NodeSED):
    """Plugwise Scan node."""

    def __init__(
        self,
        mac: str,
        address: int,
        controller: StickController,
        loaded_callback: Callable[[NodeEvent, str], Awaitable[None]],
    ):
        """Initialize Scan Device."""
        super().__init__(mac, address, controller, loaded_callback)
        self._config_task_scheduled = False
        self._new_motion_reset_timer: int | None = None
        self._new_daylight_mode: bool | None = None
        self._configure_daylight_mode_task: Task[Coroutine[Any, Any, None]] | None = (
            None
        )
        self._new_sensitivity_level: MotionSensitivity | None = None

    # region Load & Initialize

    async def load(self) -> bool:
        """Load and activate Scan node features."""
        if self._loaded:
            return True
        if self._cache_enabled:
            _LOGGER.debug("Load Scan node %s from cache", self._node_info.mac)
            await self._load_from_cache()

        self._loaded = True
        self._setup_protocol(
            SCAN_FIRMWARE_SUPPORT,
            (NodeFeature.BATTERY, NodeFeature.INFO, NodeFeature.MOTION),
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

    # region Caching
    async def _load_from_cache(self) -> bool:
        """Load states from previous cached information. Returns True if successful."""
        if not await super()._load_from_cache():
            return False
        if not await self.motion_from_cache() or not self.config_from_cache():
            return False
        return True

    async def motion_from_cache(self) -> bool:
        """Load motion state and timestamp from cache."""
        if (
            cached_motion_timestamp := self._get_cache_as_datetime(
                CACHE_MOTION_TIMESTAMP
            )
        ) is not None and (
            cached_motion_state := self._get_cache(CACHE_MOTION_STATE)
        ) is not None:
            motion_state = False
            if cached_motion_state == "True":
                motion_state = True
            await self._motion_state_update(motion_state, cached_motion_timestamp)
            _LOGGER.debug(
                "Restore motion state (%s) and timestamp (%s) cache for node %s",
                cached_motion_state,
                cached_motion_timestamp,
                self._mac_in_str,
            )
        return True

    def config_from_cache(self) -> bool:
        """Load motion state and timestamp from cache."""
        if (
            cached_reset_timer := self._get_cache(CACHE_MOTION_RESET_TIMER)
        ) is not None:
            self._motion_state.reset_timer = int(cached_reset_timer)
        else:
            self._motion_state.reset_timer = SCAN_DEFAULT_MOTION_RESET_TIMER

        if (
            cached_sensitivity_level := self._get_cache(CACHE_SCAN_SENSITIVITY)
        ) is not None:
            self._sensitivity_level = MotionSensitivity[cached_sensitivity_level]
        else:
            self._sensitivity_level = SCAN_DEFAULT_SENSITIVITY

        if (
            cached_daylight_mode := self._get_cache(CACHE_SCAN_DAYLIGHT_MODE)
        ) is not None:
            self._motion_state.daylight_mode = False
            if cached_daylight_mode == "True":
                self._motion_state.daylight_mode = True
        else:
            self._motion_state.daylight_mode = SCAN_DEFAULT_DAYLIGHT_MODE
        return True

    # endregion

    # region Properties

    @property
    @raise_not_loaded
    def daylight_mode(self) -> bool:
        """Daylight mode of motion sensor."""
        if self._config_task_scheduled and self._new_daylight_mode is not None:
            _LOGGER.debug(
                "Return the new (scheduled to be changed) daylight_mode for %s",
                self.mac,
            )
            return self._new_daylight_mode
        if self._motion_state.daylight_mode is None:
            raise NodeError(f"Daylight mode is unknown for node {self.mac}")
        return self._motion_state.daylight_mode

    @raise_not_loaded
    async def update_daylight_mode(self, state: bool) -> None:
        """Reconfigure daylight mode of motion sensor.

        Configuration will be applied next time when node is online.
        """
        if state == self._motion_state.daylight_mode:
            if self._new_daylight_mode is not None:
                self._new_daylight_mode = None
            return
        self._new_daylight_mode = state
        if self._config_task_scheduled:
            return
        await self.schedule_task_when_awake(self._configure_scan_task())

    @property
    @raise_not_loaded
    def motion_reset_timer(self) -> int:
        """Total minutes without motion before no motion is reported."""
        if self._config_task_scheduled and self._new_motion_reset_timer is not None:
            _LOGGER.debug(
                "Return the new (scheduled to be changed) motion reset timer for %s",
                self.mac,
            )
            return self._new_motion_reset_timer
        if self._motion_state.reset_timer is None:
            raise NodeError(f"Motion reset timer is unknown for node {self.mac}")
        return self._motion_state.reset_timer

    @raise_not_loaded
    async def update_motion_reset_timer(self, reset_timer: int) -> None:
        """Reconfigure minutes without motion before no motion is reported.

        Configuration will be applied next time when node is online.
        """
        if reset_timer == self._motion_state.reset_timer:
            return
        self._new_motion_reset_timer = reset_timer
        if self._config_task_scheduled:
            return
        await self.schedule_task_when_awake(self._configure_scan_task())

    @property
    @raise_not_loaded
    def sensitivity_level(self) -> MotionSensitivity:
        """Sensitivity level of motion sensor."""
        if self._config_task_scheduled and self._new_sensitivity_level is not None:
            return self._new_sensitivity_level
        if self._sensitivity_level is None:
            raise NodeError(f"Sensitivity value is unknown for node {self.mac}")
        return self._sensitivity_level

    @raise_not_loaded
    async def update_sensitivity_level(
        self, sensitivity_level: MotionSensitivity
    ) -> None:
        """Reconfigure the sensitivity level for motion sensor.

        Configuration will be applied next time when node is awake.
        """
        if sensitivity_level == self._sensitivity_level:
            return
        self._new_sensitivity_level = sensitivity_level
        if self._config_task_scheduled:
            return
        await self.schedule_task_when_awake(self._configure_scan_task())

    # endregion

    async def _switch_group(self, response: PlugwiseResponse) -> bool:
        """Switch group request from Scan.

        turn on => motion, turn off => clear motion
        """
        if not isinstance(response, NodeSwitchGroupResponse):
            raise MessageError(
                f"Invalid response message type ({response.__class__.__name__}) received, expected NodeSwitchGroupResponse"
            )
        await self._available_update_state(True)
        await self._motion_state_update(response.switch_state, response.timestamp)
        return True

    async def _motion_state_update(
        self, motion_state: bool, timestamp: datetime | None = None
    ) -> None:
        """Process motion state update."""
        self._motion_state.motion = motion_state
        self._motion_state.timestamp = timestamp
        state_update = False
        if motion_state:
            self._set_cache(CACHE_MOTION_STATE, "True")
            if self._motion is None or not self._motion:
                state_update = True
        if not motion_state:
            self._set_cache(CACHE_MOTION_STATE, "False")
            if self._motion is None or self._motion:
                state_update = True
        self._set_cache(CACHE_MOTION_TIMESTAMP, timestamp)
        if state_update:
            self._motion = motion_state
            await self.publish_feature_update_to_subscribers(
                NodeFeature.MOTION,
                self._motion_state,
            )
            await self.save_cache()

    async def _configure_scan_task(self) -> bool:
        """Configure Scan device settings. Returns True if successful."""
        change_required = False
        if self._new_motion_reset_timer is not None:
            change_required = True

        if self._new_sensitivity_level is not None:
            change_required = True

        if self._new_daylight_mode is not None:
            change_required = True

        if not change_required:
            return True

        if not await self.scan_configure(
            motion_reset_timer=self.motion_reset_timer,
            sensitivity_level=self.sensitivity_level,
            daylight_mode=self.daylight_mode,
        ):
            return False
        if self._new_motion_reset_timer is not None:
            _LOGGER.info(
                "Change of motion reset timer from %s to %s minutes has been accepted by %s",
                self._motion_state.reset_timer,
                self._new_motion_reset_timer,
                self.name,
            )
            self._new_motion_reset_timer = None
        if self._new_sensitivity_level is not None:
            _LOGGER.info(
                "Change of sensitivity level from %s to %s has been accepted by %s",
                self._sensitivity_level,
                self._new_sensitivity_level,
                self.name,
            )
            self._new_sensitivity_level = None
        if self._new_daylight_mode is not None:
            _LOGGER.info(
                "Change of daylight mode from %s to %s has been accepted by %s",
                "On" if self._motion_state.daylight_mode else "Off",
                "On" if self._new_daylight_mode else "Off",
                self.name,
            )
            self._new_daylight_mode = None
        return True

    async def scan_configure(
        self,
        motion_reset_timer: int,
        sensitivity_level: MotionSensitivity,
        daylight_mode: bool,
    ) -> bool:
        """Configure Scan device settings. Returns True if successful."""
        # Default to medium:
        sensitivity_value = 30  # b'1E'
        if sensitivity_level == MotionSensitivity.HIGH:
            sensitivity_value = 20  # b'14'
        if sensitivity_level == MotionSensitivity.OFF:
            sensitivity_value = 255  # b'FF'

        request = ScanConfigureRequest(
            self._send,
            self._mac_in_bytes,
            motion_reset_timer,
            sensitivity_value,
            daylight_mode,
        )
        if (response := await request.send()) is not None:
            if response.node_ack_type == NodeAckResponseType.SCAN_CONFIG_FAILED:
                raise NodeError(f"Scan {self.mac} failed to configure scan settings")
            if response.node_ack_type == NodeAckResponseType.SCAN_CONFIG_ACCEPTED:
                await self._scan_configure_update(
                    motion_reset_timer, sensitivity_level, daylight_mode
                )
                return True
        else:
            raise NodeTimeout(
                f"No response from Scan device {self.mac} "
                + "for configuration request."
            )
        return False

    async def _scan_configure_update(
        self,
        motion_reset_timer: int,
        sensitivity_level: MotionSensitivity,
        daylight_mode: bool,
    ) -> None:
        """Process result of scan configuration update."""
        self._motion_state.reset_timer = motion_reset_timer
        self._set_cache(CACHE_MOTION_RESET_TIMER, str(motion_reset_timer))
        self._sensitivity_level = sensitivity_level
        self._set_cache(CACHE_SCAN_SENSITIVITY, sensitivity_level.value)
        self._motion_state.daylight_mode = daylight_mode
        if daylight_mode:
            self._set_cache(CACHE_SCAN_DAYLIGHT_MODE, "True")
        else:
            self._set_cache(CACHE_SCAN_DAYLIGHT_MODE, "False")
        await self.save_cache()

    async def scan_calibrate_light(self) -> bool:
        """Request to calibration light sensitivity of Scan device."""
        request = ScanLightCalibrateRequest(self._send, self._mac_in_bytes)
        if (response := await request.send()) is not None:
            if (
                response.node_ack_type
                == NodeAckResponseType.SCAN_LIGHT_CALIBRATION_ACCEPTED
            ):
                return True
            return False
        raise NodeTimeout(
            f"No response from Scan device {self.mac} "
            + "to light calibration request."
        )

    @raise_not_loaded
    async def get_state(self, features: tuple[NodeFeature]) -> dict[NodeFeature, Any]:
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
        return states
