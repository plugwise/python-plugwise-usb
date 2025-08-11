"""Plugwise Scan node object."""

from __future__ import annotations

from asyncio import Task, gather
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import replace
from datetime import UTC, datetime
import logging
from typing import Any, Final

from ..api import (
    MotionConfig,
    MotionSensitivity,
    MotionState,
    NodeEvent,
    NodeFeature,
    NodeType,
)
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
from .helpers.firmware import SCAN_FIRMWARE_SUPPORT

_LOGGER = logging.getLogger(__name__)

CACHE_SCAN_MOTION_STATE = "motion_state"
CACHE_SCAN_MOTION_TIMESTAMP = "motion_timestamp"

CACHE_SCAN_CONFIG_DAYLIGHT_MODE = "scan_daylight_mode"
CACHE_SCAN_CONFIG_DIRTY = "scan_config_dirty"
CACHE_SCAN_CONFIG_RESET_TIMER = "motion_reset_timer"
CACHE_SCAN_CONFIG_SENSITIVITY = "scan_sensitivity_level"


# region Defaults for Scan Devices

DEFAULT_MOTION_STATE: Final = False

# Time in minutes the motion sensor should not sense motion to
# report "no motion" state [Source: 1min - 4uur]
DEFAULT_RESET_TIMER: Final = 10

# Default sensitivity of the motion sensors
DEFAULT_SENSITIVITY = MotionSensitivity.MEDIUM

# Light override
DEFAULT_DAYLIGHT_MODE: Final = False

# Default firmware if not known
DEFAULT_FIRMWARE: Final = datetime(2010, 11, 4, 16, 58, 46, tzinfo=UTC)


# Scan Features
SCAN_FEATURES: Final = (
    NodeFeature.MOTION,
    NodeFeature.MOTION_CONFIG,
)

# endregion


class PlugwiseScan(NodeSED):
    """Plugwise Scan node."""

    def __init__(
        self,
        mac: str,
        node_type: NodeType,
        controller: StickController,
        loaded_callback: Callable[[NodeEvent, str], Awaitable[None]],
    ):
        """Initialize Scan Device."""
        super().__init__(mac, node_type, controller, loaded_callback)
        self._unsubscribe_switch_group: Callable[[], None] | None = None
        self._reset_timer_motion_on: datetime | None = None
        self._scan_subscription: Callable[[], None] | None = None

        self._motion_state = MotionState()
        self._motion_config = MotionConfig()

        self._configure_daylight_mode_task: Task[Coroutine[Any, Any, None]] | None = (
            None
        )

    # region Load & Initialize

    async def load(self) -> None:
        """Load and activate Scan node features."""
        if self._loaded:
            return

        _LOGGER.debug("Loading Scan node %s", self._node_info.mac)
        await super().load()

        self._setup_protocol(SCAN_FIRMWARE_SUPPORT, SCAN_FEATURES)
        await self.initialize()
        await self._loaded_callback(NodeEvent.LOADED, self.mac)

    async def initialize(self) -> None:
        """Initialize Scan node."""
        if self._initialized:
            return

        self._unsubscribe_switch_group = await self._message_subscribe(
            self._switch_group,
            self._mac_in_bytes,
            (NODE_SWITCH_GROUP_ID,),
        )
        await super().initialize()

    async def unload(self) -> None:
        """Unload node."""
        if self._unsubscribe_switch_group is not None:
            self._unsubscribe_switch_group()
        await super().unload()

    # region Caching
    async def _load_defaults(self) -> None:
        """Load default configuration settings."""
        await super()._load_defaults()
        if self._node_info.model is None:
            self._node_info.model = "Scan"
            self._sed_node_info_update_task_scheduled = True
        if self._node_info.name is None:
            self._node_info.name = f"Scan {self._node_info.mac[-5:]}"
            self._sed_node_info_update_task_scheduled = True
        if self._node_info.firmware is None:
            self._node_info.firmware = DEFAULT_FIRMWARE
            self._sed_node_info_update_task_scheduled = True
        if self._sed_node_info_update_task_scheduled:
            _LOGGER.debug(
                "NodeInfo cache-miss for node %s, assuming defaults", self._mac_in_str
            )

    async def _load_from_cache(self) -> bool:
        """Load states from previous cached information. Returns True if successful."""
        super_load_success = True
        if not await super()._load_from_cache():
            super_load_success = False
        self._motion_state = MotionState(
            state=self._motion_from_cache(),
            timestamp=self._motion_timestamp_from_cache(),
        )
        dirty = False
        if (daylight_mode := self._daylight_mode_from_cache()) is None:
            dirty = True
            daylight_mode = DEFAULT_DAYLIGHT_MODE
        if (reset_timer := self._reset_timer_from_cache()) is None:
            dirty = True
            reset_timer = DEFAULT_RESET_TIMER
        if (sensitivity_level := self._sensitivity_level_from_cache()) is None:
            dirty = True
            sensitivity_level = DEFAULT_SENSITIVITY
        dirty |= self._motion_config_dirty_from_cache()

        self._motion_config = MotionConfig(
            daylight_mode=daylight_mode,
            reset_timer=reset_timer,
            sensitivity_level=sensitivity_level,
            dirty=dirty,
        )
        if dirty:
            await self._scan_configure_update()
        return super_load_success

    def _daylight_mode_from_cache(self) -> bool | None:
        """Load awake duration from cache."""
        return self._get_cache_as_bool(CACHE_SCAN_CONFIG_DAYLIGHT_MODE)

    def _motion_from_cache(self) -> bool:
        """Load motion state from cache."""
        if (
            cached_motion_state := self._get_cache(CACHE_SCAN_MOTION_STATE)
        ) is not None:
            if (
                cached_motion_state == "True"
                and (motion_timestamp := self._motion_timestamp_from_cache())
                is not None
                and int((datetime.now(tz=UTC) - motion_timestamp).total_seconds())
                < self._reset_timer_from_cache() * 60
            ):
                return True
            return False
        return DEFAULT_MOTION_STATE

    def _reset_timer_from_cache(self) -> int | None:
        """Load reset timer from cache."""
        if (reset_timer := self._get_cache(CACHE_SCAN_CONFIG_RESET_TIMER)) is not None:
            return int(reset_timer)
        return None

    def _sensitivity_level_from_cache(self) -> int | None:
        """Load sensitivity level from cache."""
        if (
            sensitivity_level := self._get_cache(
                CACHE_SCAN_CONFIG_SENSITIVITY
            )  # returns level in string CAPITALS
        ) is not None:
            return MotionSensitivity[sensitivity_level]
        return None

    def _motion_config_dirty_from_cache(self) -> bool:
        """Load dirty  from cache."""
        if (dirty := self._get_cache_as_bool(CACHE_SCAN_CONFIG_DIRTY)) is not None:
            return dirty
        return True

    def _motion_timestamp_from_cache(self) -> datetime | None:
        """Load motion timestamp from cache."""
        if (
            motion_timestamp := self._get_cache_as_datetime(CACHE_SCAN_MOTION_TIMESTAMP)
        ) is not None:
            return motion_timestamp
        return None

    # endregion

    # region Properties
    @property
    def dirty(self) -> bool:
        """Motion configuration dirty flag."""
        return self._motion_config.dirty

    @property
    def daylight_mode(self) -> bool:
        """Daylight mode of motion sensor."""
        if self._motion_config.daylight_mode is not None:
            return self._motion_config.daylight_mode
        return DEFAULT_DAYLIGHT_MODE

    @property
    def motion(self) -> bool:
        """Motion detection value."""
        if self._motion_state.state is not None:
            return self._motion_state.state
        raise NodeError(f"Motion state is not available for {self.name}")

    @property
    def motion_state(self) -> MotionState:
        """Motion detection state."""
        return self._motion_state

    @property
    def motion_timestamp(self) -> datetime:
        """Timestamp of last motion state change."""
        if self._motion_state.timestamp is not None:
            return self._motion_state.timestamp
        raise NodeError(f"Motion timestamp is currently not available for {self.name}")

    @property
    def motion_config(self) -> MotionConfig:
        """Motion configuration."""
        return MotionConfig(
            reset_timer=self.reset_timer,
            daylight_mode=self.daylight_mode,
            sensitivity_level=self.sensitivity_level,
            dirty=self.dirty,
        )

    @property
    def reset_timer(self) -> int:
        """Total minutes without motion before no motion is reported."""
        if self._motion_config.reset_timer is not None:
            return self._motion_config.reset_timer
        return DEFAULT_RESET_TIMER

    @property
    def sensitivity_level(self) -> int:
        """Sensitivity level of motion sensor."""
        if self._motion_config.sensitivity_level is not None:
            return self._motion_config.sensitivity_level
        return DEFAULT_SENSITIVITY

    # endregion
    # region Configuration actions

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
        if self._motion_config.daylight_mode == state:
            return False
        self._motion_config = replace(
            self._motion_config,
            daylight_mode=state,
            dirty=True,
        )
        await self._scan_configure_update()
        return True

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
        if self._motion_config.reset_timer == minutes:
            return False
        self._motion_config = replace(
            self._motion_config,
            reset_timer=minutes,
            dirty=True,
        )
        await self._scan_configure_update()
        return True

    async def set_motion_sensitivity_level(self, level: int) -> bool:
        """Configure the motion sensitivity level."""
        _LOGGER.debug(
            "set_motion_sensitivity_level | Device %s | %s -> %s",
            self.name,
            self._motion_config.sensitivity_level,
            level,
        )
        if self._motion_config.sensitivity_level == level:
            return False
        self._motion_config = replace(
            self._motion_config,
            sensitivity_level=level,
            dirty=True,
        )
        await self._scan_configure_update()
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
            self._set_cache(CACHE_SCAN_MOTION_STATE, "True")
            if self._motion_state.state is None or not self._motion_state.state:
                self._reset_timer_motion_on = timestamp
                state_update = True
        else:
            self._set_cache(CACHE_SCAN_MOTION_STATE, "False")
            if self._motion_state.state is None or self._motion_state.state:
                if self._reset_timer_motion_on is not None:
                    reset_timer = int(
                        (timestamp - self._reset_timer_motion_on).total_seconds()
                    )
                    if self._motion_config.reset_timer is None:
                        self._motion_config = replace(
                            self._motion_config, reset_timer=reset_timer, dirty=True
                        )
                        await self._scan_configure_update()
                    elif reset_timer < self._motion_config.reset_timer:
                        _LOGGER.warning(
                            "Adjust reset timer for %s from %s -> %s",
                            self.name,
                            self._motion_config.reset_timer,
                            reset_timer,
                        )
                        self._motion_config = replace(
                            self._motion_config, reset_timer=reset_timer, dirty=True
                        )
                        await self._scan_configure_update()
                state_update = True
        self._set_cache(CACHE_SCAN_MOTION_TIMESTAMP, timestamp)
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

    async def _run_awake_tasks(self) -> None:
        """Execute all awake tasks."""
        await super()._run_awake_tasks()
        if self._motion_config.dirty:
            await self._configure_scan_task()

    async def _configure_scan_task(self) -> bool:
        """Configure Scan device settings. Returns True if successful."""
        if not self._motion_config.dirty:
            return True
        if not await self.scan_configure():
            _LOGGER.debug("Motion Configuration for %s failed", self._mac_in_str)
            return False
        return True

    async def scan_configure(self) -> bool:
        """Configure Scan device settings. Returns True if successful."""
        # Default to medium
        request = ScanConfigureRequest(
            self._send,
            self._mac_in_bytes,
            self._motion_config.reset_timer,
            self._motion_config.sensitivity_level,
            self._motion_config.daylight_mode,
        )
        if (response := await request.send()) is None:
            _LOGGER.warning(
                "No response from %s to configure motion settings request", self.name
            )
            return False
        if response.node_ack_type == NodeAckResponseType.SCAN_CONFIG_FAILED:
            _LOGGER.warning("Failed to configure scan settings for %s", self.name)
            return False
        if response.node_ack_type == NodeAckResponseType.SCAN_CONFIG_ACCEPTED:
            _LOGGER.debug("Successful configure scan settings for %s", self.name)
            self._motion_config = replace(self._motion_config, dirty=False)
            await self._scan_configure_update()
            return True

        _LOGGER.warning(
            "Unexpected response ack type %s for %s",
            response.node_ack_type,
            self.name,
        )
        return False

    async def _scan_configure_update(self) -> None:
        """Push scan configuration update to cache."""
        self._set_cache(
            CACHE_SCAN_CONFIG_RESET_TIMER, str(self._motion_config.reset_timer)
        )
        self._set_cache(
            CACHE_SCAN_CONFIG_SENSITIVITY,
            str(MotionSensitivity(self._motion_config.sensitivity_level).name),
        )
        self._set_cache(
            CACHE_SCAN_CONFIG_DAYLIGHT_MODE, str(self._motion_config.daylight_mode)
        )
        self._set_cache(CACHE_SCAN_CONFIG_DIRTY, str(self._motion_config.dirty))
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
