"""Plugwise Scan node object."""

from __future__ import annotations

from asyncio import Task, gather
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import replace
from datetime import UTC, datetime
import logging
from typing import Any, Final

from ..api import MotionConfig, MotionSensitivity, MotionState, NodeEvent, NodeFeature
from ..connection import StickController
from ..constants import MAX_UINT_2
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

SCAN_DEFAULT_MOTION_STATE: Final = False

# Time in minutes the motion sensor should not sense motion to
# report "no motion" state [Source: 1min - 4uur]
SCAN_DEFAULT_MOTION_RESET_TIMER: Final = 10

# Default sensitivity of the motion sensors
SCAN_DEFAULT_SENSITIVITY: Final = MotionSensitivity.MEDIUM

# Light override
SCAN_DEFAULT_DAYLIGHT_MODE: Final = False

# Sensitivity values for motion sensor configuration
SENSITIVITY_HIGH_VALUE = 20  # 0x14
SENSITIVITY_MEDIUM_VALUE = 30  # 0x1E
SENSITIVITY_OFF_VALUE = 255  # 0xFF

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
        self._unsubscribe_switch_group: Callable[[], None] | None = None
        self._reset_timer_motion_on: datetime | None = None
        self._scan_subscription: Callable[[], None] | None = None

        self._motion_state = MotionState()
        self._motion_config = MotionConfig()
        self._new_daylight_mode: bool | None = None
        self._new_reset_timer: int | None = None
        self._new_sensitivity_level: MotionSensitivity | None = None

        self._scan_config_task_scheduled = False
        self._configure_daylight_mode_task: Task[Coroutine[Any, Any, None]] | None = (
            None
        )

    # region Load & Initialize

    async def load(self) -> bool:
        """Load and activate Scan node features."""
        if self._loaded:
            return True
        if self._cache_enabled:
            _LOGGER.debug("Load Scan node %s from cache", self._node_info.mac)
            await self._load_from_cache()
        else:
            self._load_defaults()
        self._loaded = True
        self._setup_protocol(
            SCAN_FIRMWARE_SUPPORT,
            (
                NodeFeature.BATTERY,
                NodeFeature.INFO,
                NodeFeature.PING,
                NodeFeature.MOTION,
                NodeFeature.MOTION_CONFIG,
            ),
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

        self._unsubscribe_switch_group = await self._message_subscribe(
            self._switch_group,
            self._mac_in_bytes,
            (NODE_SWITCH_GROUP_ID,),
        )
        await super().initialize()
        return True

    async def unload(self) -> None:
        """Unload node."""
        if self._unsubscribe_switch_group is not None:
            self._unsubscribe_switch_group()
        await super().unload()

    # region Caching
    def _load_defaults(self) -> None:
        """Load default configuration settings."""
        super()._load_defaults()
        self._motion_state = MotionState(
            state=SCAN_DEFAULT_MOTION_STATE,
            timestamp=None,
        )
        self._motion_config = MotionConfig(
            reset_timer=SCAN_DEFAULT_MOTION_RESET_TIMER,
            daylight_mode=SCAN_DEFAULT_DAYLIGHT_MODE,
            sensitivity_level=SCAN_DEFAULT_SENSITIVITY,
        )

    async def _load_from_cache(self) -> bool:
        """Load states from previous cached information. Returns True if successful."""
        if not await super()._load_from_cache():
            self._load_defaults()
            return False
        self._motion_state = MotionState(
            state=self._motion_from_cache(),
            timestamp=self._motion_timestamp_from_cache(),
        )
        self._motion_config = MotionConfig(
            daylight_mode=self._daylight_mode_from_cache(),
            reset_timer=self._reset_timer_from_cache(),
            sensitivity_level=self._sensitivity_level_from_cache(),
        )
        return True

    def _daylight_mode_from_cache(self) -> bool:
        """Load awake duration from cache."""
        if (daylight_mode := self._get_cache(CACHE_SCAN_DAYLIGHT_MODE)) is not None:
            if daylight_mode == "True":
                return True
            return False
        return SCAN_DEFAULT_DAYLIGHT_MODE

    def _motion_from_cache(self) -> bool:
        """Load motion state from cache."""
        if (cached_motion_state := self._get_cache(CACHE_MOTION_STATE)) is not None:
            if (
                cached_motion_state == "True"
                and (motion_timestamp := self._motion_timestamp_from_cache())
                is not None
                and int((datetime.now(tz=UTC) - motion_timestamp).total_seconds())
                < self._reset_timer_from_cache() * 60
            ):
                return True
            return False
        return SCAN_DEFAULT_MOTION_STATE

    def _reset_timer_from_cache(self) -> int:
        """Load reset timer from cache."""
        if (reset_timer := self._get_cache(CACHE_MOTION_RESET_TIMER)) is not None:
            return int(reset_timer)
        return SCAN_DEFAULT_MOTION_RESET_TIMER

    def _sensitivity_level_from_cache(self) -> MotionSensitivity:
        """Load sensitivity level from cache."""
        if (sensitivity_level := self._get_cache(CACHE_SCAN_SENSITIVITY)) is not None:
            return MotionSensitivity[sensitivity_level]
        return SCAN_DEFAULT_SENSITIVITY

    def _motion_timestamp_from_cache(self) -> datetime | None:
        """Load motion timestamp from cache."""
        if (
            motion_timestamp := self._get_cache_as_datetime(CACHE_MOTION_TIMESTAMP)
        ) is not None:
            return motion_timestamp
        return None

    # endregion

    # region Properties

    @property
    @raise_not_loaded
    def daylight_mode(self) -> bool:
        """Daylight mode of motion sensor."""
        if self._new_daylight_mode is not None:
            return self._new_daylight_mode
        if self._motion_config.daylight_mode is not None:
            return self._motion_config.daylight_mode
        return SCAN_DEFAULT_DAYLIGHT_MODE

    @property
    @raise_not_loaded
    def motion(self) -> bool:
        """Motion detection value."""
        if self._motion_state.state is not None:
            return self._motion_state.state
        raise NodeError(f"Motion state is not available for {self.name}")

    @property
    @raise_not_loaded
    def motion_state(self) -> MotionState:
        """Motion detection state."""
        return self._motion_state

    @property
    @raise_not_loaded
    def motion_timestamp(self) -> datetime:
        """Timestamp of last motion state change."""
        if self._motion_state.timestamp is not None:
            return self._motion_state.timestamp
        raise NodeError(f"Motion timestamp is currently not available for {self.name}")

    @property
    @raise_not_loaded
    def motion_config(self) -> MotionConfig:
        """Motion configuration."""
        return MotionConfig(
            reset_timer=self.reset_timer,
            daylight_mode=self.daylight_mode,
            sensitivity_level=self.sensitivity_level,
        )

    @property
    @raise_not_loaded
    def reset_timer(self) -> int:
        """Total minutes without motion before no motion is reported."""
        if self._new_reset_timer is not None:
            return self._new_reset_timer
        if self._motion_config.reset_timer is not None:
            return self._motion_config.reset_timer
        return SCAN_DEFAULT_MOTION_RESET_TIMER

    @property
    def scan_config_task_scheduled(self) -> bool:
        """Check if a configuration task is scheduled."""
        return self._scan_config_task_scheduled

    @property
    def sensitivity_level(self) -> MotionSensitivity:
        """Sensitivity level of motion sensor."""
        if self._new_sensitivity_level is not None:
            return self._new_sensitivity_level
        if self._motion_config.sensitivity_level is not None:
            return self._motion_config.sensitivity_level
        return SCAN_DEFAULT_SENSITIVITY

    # endregion
    # region Configuration actions

    @raise_not_loaded
    async def set_motion_daylight_mode(self, state: bool) -> bool:
        """Configure if motion must be detected when light level is below threshold.

        Configuration request will be queued and will be applied the next time when node is awake for maintenance.
        """
        _LOGGER.debug(
            "set_motion_daylight_mode | Device %s | %s -> %s",
            self.name,
            self._motion_config.daylight_mode,
            state,
        )
        self._new_daylight_mode = state
        if self._motion_config.daylight_mode == state:
            return False
        if not self._scan_config_task_scheduled:
            await self.schedule_task_when_awake(self._configure_scan_task())
            self._scan_config_task_scheduled = True
            _LOGGER.debug(
                "set_motion_daylight_mode | Device %s | config scheduled",
                self.name,
            )
        return True

    @raise_not_loaded
    async def set_motion_reset_timer(self, minutes: int) -> bool:
        """Configure the motion reset timer in minutes."""
        _LOGGER.debug(
            "set_motion_reset_timer | Device %s | %s -> %s",
            self.name,
            self._motion_config.reset_timer,
            minutes,
        )
        if minutes < 1 or minutes > MAX_UINT_2:
            raise ValueError(
                f"Invalid motion reset timer ({minutes}). It must be between 1 and 255 minutes."
            )
        self._new_reset_timer = minutes
        if self._motion_config.reset_timer == minutes:
            return False
        if not self._scan_config_task_scheduled:
            await self.schedule_task_when_awake(self._configure_scan_task())
            self._scan_config_task_scheduled = True
            _LOGGER.debug(
                "set_motion_reset_timer | Device %s | config scheduled",
                self.name,
            )
        return True

    @raise_not_loaded
    async def set_motion_sensitivity_level(self, level: MotionSensitivity) -> bool:
        """Configure the motion sensitivity level."""
        _LOGGER.debug(
            "set_motion_sensitivity_level | Device %s | %s -> %s",
            self.name,
            self._motion_config.sensitivity_level,
            level,
        )
        self._new_sensitivity_level = level
        if self._motion_config.sensitivity_level == level:
            return False
        if not self._scan_config_task_scheduled:
            await self.schedule_task_when_awake(self._configure_scan_task())
            self._scan_config_task_scheduled = True
            _LOGGER.debug(
                "set_motion_sensitivity_level | Device %s | config scheduled",
                self.name,
            )
        return True

    # endregion

    # endregion

    async def _switch_group(self, response: PlugwiseResponse) -> bool:
        """Switch group request from Scan.

        turn on => motion, turn off => clear motion
        """
        if not isinstance(response, NodeSwitchGroupResponse):
            raise MessageError(
                f"Invalid response message type ({response.__class__.__name__}) received, expected NodeSwitchGroupResponse"
            )
        _LOGGER.warning("%s received %s", self.name, response)
        await gather(
            self._available_update_state(True, response.timestamp),
            self._motion_state_update(response.switch_state, response.timestamp),
        )
        return True

    async def _motion_state_update(
        self, motion_state: bool, timestamp: datetime
    ) -> None:
        """Process motion state update."""
        _LOGGER.debug(
            "motion_state_update for %s: %s -> %s",
            self.name,
            self._motion_state.state,
            motion_state,
        )
        state_update = False
        if motion_state:
            self._set_cache(CACHE_MOTION_STATE, "True")
            if self._motion_state.state is None or not self._motion_state.state:
                self._reset_timer_motion_on = timestamp
                state_update = True
        else:
            self._set_cache(CACHE_MOTION_STATE, "False")
            if self._motion_state.state is None or self._motion_state.state:
                if self._reset_timer_motion_on is not None:
                    reset_timer = int(
                        (timestamp - self._reset_timer_motion_on).total_seconds()
                    )
                    if self._motion_config.reset_timer is None:
                        self._motion_config = replace(
                            self._motion_config,
                            reset_timer=reset_timer,
                        )
                    elif reset_timer < self._motion_config.reset_timer:
                        _LOGGER.warning(
                            "Adjust reset timer for %s from %s -> %s",
                            self.name,
                            self._motion_config.reset_timer,
                            reset_timer,
                        )
                        self._motion_config = replace(
                            self._motion_config,
                            reset_timer=reset_timer,
                        )
                state_update = True
        self._set_cache(CACHE_MOTION_TIMESTAMP, timestamp)
        if state_update:
            self._motion_state = replace(
                self._motion_state,
                state=motion_state,
                timestamp=timestamp,
            )
            await gather(
                *[
                    self.publish_feature_update_to_subscribers(
                        NodeFeature.MOTION,
                        self._motion_state,
                    ),
                    self.save_cache(),
                ]
            )

    async def _configure_scan_task(self) -> bool:
        """Configure Scan device settings. Returns True if successful."""
        self._scan_config_task_scheduled = False
        change_required = False
        if self._new_reset_timer is not None:
            change_required = True
        if self._new_sensitivity_level is not None:
            change_required = True
        if self._new_daylight_mode is not None:
            change_required = True
        if not change_required:
            return True
        if not await self.scan_configure(
            motion_reset_timer=self.reset_timer,
            sensitivity_level=self.sensitivity_level,
            daylight_mode=self.daylight_mode,
        ):
            return False
        if self._new_reset_timer is not None:
            _LOGGER.info(
                "Change of motion reset timer from %s to %s minutes has been accepted by %s",
                self._motion_config.reset_timer,
                self._new_reset_timer,
                self.name,
            )
            self._new_reset_timer = None
        if self._new_sensitivity_level is not None:
            _LOGGER.info(
                "Change of sensitivity level from %s to %s has been accepted by %s",
                self._motion_config.sensitivity_level,
                self._new_sensitivity_level,
                self.name,
            )
            self._new_sensitivity_level = None
        if self._new_daylight_mode is not None:
            _LOGGER.info(
                "Change of daylight mode from %s to %s has been accepted by %s",
                "On" if self._motion_config.daylight_mode else "Off",
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
        sensitivity_map = {
            MotionSensitivity.HIGH: SENSITIVITY_HIGH_VALUE,
            MotionSensitivity.MEDIUM: SENSITIVITY_MEDIUM_VALUE,
            MotionSensitivity.OFF: SENSITIVITY_OFF_VALUE,
        }
        # Default to medium
        sensitivity_value = sensitivity_map.get(
            sensitivity_level, SENSITIVITY_MEDIUM_VALUE
        )
        request = ScanConfigureRequest(
            self._send,
            self._mac_in_bytes,
            motion_reset_timer,
            sensitivity_value,
            daylight_mode,
        )
        if (response := await request.send()) is not None:
            if response.node_ack_type == NodeAckResponseType.SCAN_CONFIG_FAILED:
                self._new_reset_timer = None
                self._new_sensitivity_level = None
                self._new_daylight_mode = None
                _LOGGER.warning("Failed to configure scan settings for %s", self.name)
                return False

            if response.node_ack_type == NodeAckResponseType.SCAN_CONFIG_ACCEPTED:
                await self._scan_configure_update(
                    motion_reset_timer, sensitivity_level, daylight_mode
                )
                return True

            _LOGGER.warning(
                "Unexpected response ack type %s for %s",
                response.node_ack_type,
                self.name,
            )
            return False

        self._new_reset_timer = None
        self._new_sensitivity_level = None
        self._new_daylight_mode = None
        return False

    async def _scan_configure_update(
        self,
        motion_reset_timer: int,
        sensitivity_level: MotionSensitivity,
        daylight_mode: bool,
    ) -> None:
        """Process result of scan configuration update."""
        self._motion_config = replace(
            self._motion_config,
            reset_timer=motion_reset_timer,
            sensitivity_level=sensitivity_level,
            daylight_mode=daylight_mode,
        )
        self._set_cache(CACHE_MOTION_RESET_TIMER, str(motion_reset_timer))
        self._set_cache(CACHE_SCAN_SENSITIVITY, sensitivity_level.value)
        if daylight_mode:
            self._set_cache(CACHE_SCAN_DAYLIGHT_MODE, "True")
        else:
            self._set_cache(CACHE_SCAN_DAYLIGHT_MODE, "False")
        await gather(
            self.publish_feature_update_to_subscribers(
                NodeFeature.MOTION_CONFIG,
                self._motion_config,
            ),
            self.save_cache(),
        )

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

            match feature:
                case NodeFeature.MOTION:
                    states[NodeFeature.MOTION] = self._motion_state
                case NodeFeature.MOTION_CONFIG:
                    states[NodeFeature.MOTION_CONFIG] = self._motion_config
                case _:
                    state_result = await super().get_state((feature,))
                    states[feature] = state_result[feature]

        if NodeFeature.AVAILABLE not in states:
            states[NodeFeature.AVAILABLE] = self.available_state
        return states
