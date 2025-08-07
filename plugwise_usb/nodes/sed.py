"""Plugwise SED (Sleeping Endpoint Device) base object."""

from __future__ import annotations

from asyncio import CancelledError, Future, Task, gather, get_running_loop, wait_for
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import replace
from datetime import datetime, timedelta
import logging
from typing import Any, Final

from ..api import BatteryConfig, NodeEvent, NodeFeature, NodeType
from ..connection import StickController
from ..constants import MAX_UINT_2, MAX_UINT_4
from ..exceptions import MessageError, NodeError
from ..messages.requests import NodeSleepConfigRequest
from ..messages.responses import (
    NODE_AWAKE_RESPONSE_ID,
    NodeAwakeResponse,
    NodeAwakeResponseType,
    NodeResponseType,
    PlugwiseResponse,
)
from .node import PlugwiseBaseNode

CACHE_SED_AWAKE_DURATION = "awake_duration"
CACHE_SED_CLOCK_INTERVAL = "clock_interval"
CACHE_SED_SLEEP_DURATION = "sleep_duration"
CACHE_SED_DIRTY = "sed_dirty"
CACHE_SED_CLOCK_SYNC = "clock_sync"
CACHE_SED_MAINTENANCE_INTERVAL = "maintenance_interval"
CACHE_SED_AWAKE_TIMESTAMP = "awake_timestamp"
CACHE_SED_AWAKE_REASON = "awake_reason"

# Number of seconds to ignore duplicate awake messages
AWAKE_RETRY: Final = 5

# Defaults for 'Sleeping End Devices'

# Time in seconds the SED keep itself awake to receive
# and respond to other messages
SED_DEFAULT_AWAKE_DURATION: Final = 10

# 7 days, duration in minutes the node synchronize its clock
SED_DEFAULT_CLOCK_INTERVAL: Final = 25200

# Enable or disable synchronizing clock
SED_DEFAULT_CLOCK_SYNC: Final = False

# Interval in minutes the SED will awake for maintenance purposes
# Source [5min - 24h]
SED_DEFAULT_MAINTENANCE_INTERVAL: Final = 60  # Assume standard interval of 1 hour
SED_MAX_MAINTENANCE_INTERVAL_OFFSET: Final = 30  # seconds

# Time in minutes the SED will sleep
SED_DEFAULT_SLEEP_DURATION: Final = 60

# Default firmware if not known
DEFAULT_FIRMWARE: Final = None

# SED BaseNode Features
SED_FEATURES: Final = (NodeFeature.BATTERY,)

# Value limits
MAX_MINUTE_INTERVAL: Final = 1440

_LOGGER = logging.getLogger(__name__)


class NodeSED(PlugwiseBaseNode):
    """provides base class for SED based nodes like Scan, Sense & Switch."""

    # Maintenance
    _awake_timer_task: Task[None] | None = None
    _ping_at_awake: bool = False

    _awake_subscription: Callable[[], None] | None = None

    def __init__(
        self,
        mac: str,
        node_type: NodeType,
        controller: StickController,
        loaded_callback: Callable[[NodeEvent, str], Awaitable[None]],
    ):
        """Initialize base class for Sleeping End Device."""
        super().__init__(mac, node_type, controller, loaded_callback)
        self._loop = get_running_loop()
        self._node_info.is_battery_powered = True

        # Configure SED
        self._battery_config = BatteryConfig()
        self._sed_node_info_update_task_scheduled = False
        self._delayed_task: Task[None] | None = None

        self._last_awake: dict[NodeAwakeResponseType, datetime] = {}
        self._last_awake_reason: str = "Unknown"
        self._awake_future: Future[bool] | None = None

        # Maintenance
        self._maintenance_last_awake: datetime | None = None
        self._maintenance_interval_restored_from_cache = False

    async def load(self) -> bool:
        """Load and activate SED node features."""
        if self._loaded:
            return True

        _LOGGER.debug("Load SED node %s from cache", self._node_info.mac)
        if await self._load_from_cache():
            self._loaded = True
        if not self._loaded:
            _LOGGER.debug("Load SED node %s defaults", self._node_info.mac)
            await self._load_defaults()
        self._loaded = True
        self._features += SED_FEATURES
        return self._loaded

    async def unload(self) -> None:
        """Deactivate and unload node features."""
        if self._awake_future is not None and not self._awake_future.done():
            self._awake_future.set_result(True)
        if self._awake_timer_task is not None and not self._awake_timer_task.done():
            await self._awake_timer_task
        if self._awake_subscription is not None:
            self._awake_subscription()
        if self._delayed_task is not None and not self._delayed_task.done():
            await self._delayed_task
        await super().unload()

    async def initialize(self) -> None:
        """Initialize SED node."""
        if self._initialized:
            return

        self._awake_subscription = await self._message_subscribe(
            self._awake_response,
            self._mac_in_bytes,
            (NODE_AWAKE_RESPONSE_ID,),
        )
        await super().initialize()

    async def _load_defaults(self) -> None:
        """Load default configuration settings."""

    async def _load_from_cache(self) -> bool:
        """Load states from previous cached information. Returns True if successful."""
        super_load_success = True
        if not await super()._load_from_cache():
            super_load_success = False
        dirty = False
        if (awake_duration := self._awake_duration_from_cache()) is None:
            dirty = True
            awake_duration = SED_DEFAULT_AWAKE_DURATION
        if (clock_interval := self._clock_interval_from_cache()) is None:
            dirty = True
            clock_interval = SED_DEFAULT_CLOCK_INTERVAL
        if (clock_sync := self._clock_sync_from_cache()) is None:
            dirty = True
            clock_sync = SED_DEFAULT_CLOCK_SYNC
        if (maintenance_interval := self._maintenance_interval_from_cache()) is None:
            dirty = True
            maintenance_interval = SED_DEFAULT_MAINTENANCE_INTERVAL
        if (sleep_duration := self._sleep_duration_from_cache()) is None:
            dirty = True
            sleep_duration = SED_DEFAULT_SLEEP_DURATION
        dirty |= self._sed_config_dirty_from_cache()
        self._battery_config = BatteryConfig(
            awake_duration=awake_duration,
            clock_interval=clock_interval,
            clock_sync=clock_sync,
            maintenance_interval=maintenance_interval,
            sleep_duration=sleep_duration,
            dirty=dirty,
        )
        if dirty:
            await self._sed_configure_update()
        self._awake_timestamp_from_cache()
        self._awake_reason_from_cache()
        return super_load_success

    def _awake_duration_from_cache(self) -> int | None:
        """Load awake duration from cache."""
        if (awake_duration := self._get_cache(CACHE_SED_AWAKE_DURATION)) is not None:
            return int(awake_duration)
        return None

    def _clock_interval_from_cache(self) -> int | None:
        """Load clock interval from cache."""
        if (clock_interval := self._get_cache(CACHE_SED_CLOCK_INTERVAL)) is not None:
            return int(clock_interval)
        return None

    def _clock_sync_from_cache(self) -> bool | None:
        """Load clock sync state from cache."""
        return self._get_cache_as_bool(CACHE_SED_CLOCK_SYNC)

    def _maintenance_interval_from_cache(self) -> int | None:
        """Load maintenance interval from cache."""
        if (
            maintenance_interval := self._get_cache(CACHE_SED_MAINTENANCE_INTERVAL)
        ) is not None:
            self._maintenance_interval_restored_from_cache = True
            return int(maintenance_interval)
        return None

    def _sleep_duration_from_cache(self) -> int | None:
        """Load sleep duration from cache."""
        if (sleep_duration := self._get_cache(CACHE_SED_SLEEP_DURATION)) is not None:
            return int(sleep_duration)
        return None

    def _awake_timestamp_from_cache(self) -> datetime | None:
        """Load last awake timestamp from cache."""
        return self._get_cache_as_datetime(CACHE_SED_AWAKE_TIMESTAMP)

    def _awake_reason_from_cache(self) -> str | None:
        """Load last awake state from cache."""
        return self._get_cache(CACHE_SED_AWAKE_REASON)

    def _sed_config_dirty_from_cache(self) -> bool:
        """Load battery config dirty  from cache."""
        if (dirty := self._get_cache_as_bool(CACHE_SED_DIRTY)) is not None:
            return dirty
        return True

    # region Configuration actions
    async def set_awake_duration(self, seconds: int) -> bool:
        """Change the awake duration."""
        _LOGGER.debug(
            "set_awake_duration | Device %s | %s -> %s",
            self.name,
            self._battery_config.awake_duration,
            seconds,
        )
        if seconds < 1 or seconds > MAX_UINT_2:
            raise ValueError(
                f"Invalid awake duration ({seconds}). It must be between 1 and 255 seconds."
            )

        self._battery_config = replace(
            self._battery_config,
            awake_duration=seconds,
            dirty=True,
        )
        return True

    async def set_clock_interval(self, minutes: int) -> bool:
        """Change the clock interval."""
        _LOGGER.debug(
            "set_clock_interval | Device %s | %s -> %s",
            self.name,
            self._battery_config.clock_interval,
            minutes,
        )
        if minutes < 1 or minutes > MAX_UINT_4:
            raise ValueError(
                f"Invalid clock interval ({minutes}). It must be between 1 and 65535 minutes."
            )

        if self.battery_config.clock_interval == minutes:
            return False

        self._battery_config = replace(
            self._battery_config, clock_interval=minutes, dirty=True
        )
        return True

    async def set_clock_sync(self, sync: bool) -> bool:
        """Change the clock synchronization setting."""
        _LOGGER.debug(
            "set_clock_sync | Device %s | %s -> %s",
            self.name,
            self._battery_config.clock_sync,
            sync,
        )
        if self._battery_config.clock_sync == sync:
            return False

        self._battery_config = replace(
            self._battery_config, clock_sync=sync, dirty=True
        )
        return True

    async def set_maintenance_interval(self, minutes: int) -> bool:
        """Change the maintenance interval."""
        _LOGGER.debug(
            "set_maintenance_interval | Device %s | %s -> %s",
            self.name,
            self._battery_config.maintenance_interval,
            minutes,
        )
        if minutes < 1 or minutes > MAX_MINUTE_INTERVAL:
            raise ValueError(
                f"Invalid maintenance interval ({minutes}). It must be between 1 and 1440 minutes."
            )

        if self.battery_config.maintenance_interval == minutes:
            return False

        self._battery_config = replace(
            self._battery_config, maintenance_interval=minutes, dirty=True
        )
        return True

    async def set_sleep_duration(self, minutes: int) -> bool:
        """Reconfigure the sleep duration in minutes for a Sleeping Endpoint Device.

        Configuration will be applied next time when node is awake for maintenance.
        """
        _LOGGER.debug(
            "set_sleep_duration | Device %s | %s -> %s",
            self.name,
            self._battery_config.sleep_duration,
            minutes,
        )
        if minutes < 1 or minutes > MAX_UINT_4:
            raise ValueError(
                f"Invalid sleep duration ({minutes}). It must be between 1 and 65535 minutes."
            )

        if self._battery_config.sleep_duration == minutes:
            return False

        self._battery_config = replace(
            self._battery_config, sleep_duration=minutes, dirty=True
        )
        return True

    # endregion
    # region Properties
    @property
    def dirty(self) -> bool:
        """Battery configuration dirty flag."""
        return self._battery_config.dirty

    @property
    def awake_duration(self) -> int:
        """Duration in seconds a battery powered devices is awake."""
        if self._battery_config.awake_duration is not None:
            return self._battery_config.awake_duration
        return SED_DEFAULT_AWAKE_DURATION

    @property
    def battery_config(self) -> BatteryConfig:
        """Battery related configuration settings."""
        return BatteryConfig(
            awake_duration=self.awake_duration,
            clock_interval=self.clock_interval,
            clock_sync=self.clock_sync,
            maintenance_interval=self.maintenance_interval,
            sleep_duration=self.sleep_duration,
            dirty=self.dirty,
        )

    @property
    def clock_interval(self) -> int:
        """Return the clock interval value."""
        if self._battery_config.clock_interval is not None:
            return self._battery_config.clock_interval
        return SED_DEFAULT_CLOCK_INTERVAL

    @property
    def clock_sync(self) -> bool:
        """Indicate if the internal clock must be synced."""
        if self._battery_config.clock_sync is not None:
            return self._battery_config.clock_sync
        return SED_DEFAULT_CLOCK_SYNC

    @property
    def maintenance_interval(self) -> int:
        """Return the maintenance interval value.

        When value is scheduled to be changed the return value is the optimistic value.
        """
        if self._battery_config.maintenance_interval is not None:
            return self._battery_config.maintenance_interval
        return SED_DEFAULT_MAINTENANCE_INTERVAL

    @property
    def sleep_duration(self) -> int:
        """Return the sleep duration value in minutes.

        When value is scheduled to be changed the return value is the optimistic value.
        """
        if self._battery_config.sleep_duration is not None:
            return self._battery_config.sleep_duration
        return SED_DEFAULT_SLEEP_DURATION

    # endregion
    async def _configure_sed_task(self) -> bool:
        """Configure SED settings. Returns True if successful."""
        if not self._battery_config.dirty:
            return True
        _LOGGER.debug(
            "_configure_sed_task | Node %s | request change",
            self._mac_in_str,
        )
        if not await self.sed_configure():
            _LOGGER.debug("Battery Configuration for %s failed", self._mac_in_str)
            return False

        return True

    async def _awake_response(self, response: PlugwiseResponse) -> bool:
        """Process awake message."""
        if not isinstance(response, NodeAwakeResponse):
            raise MessageError(
                f"Invalid response message type ({response.__class__.__name__}) received, expected NodeAwakeResponse"
            )

        _LOGGER.debug("Device %s is awake for %s", self.name, response.awake_type)
        self._set_cache(CACHE_SED_AWAKE_TIMESTAMP, response.timestamp)
        await self._available_update_state(True, response.timestamp)

        # Pre populate the last awake timestamp
        if self._last_awake.get(response.awake_type) is None:
            self._last_awake[response.awake_type] = response.timestamp
        # Skip awake messages when they are shortly after each other
        elif (
            self._last_awake[response.awake_type] + timedelta(seconds=AWAKE_RETRY)
            > response.timestamp
        ):
            return True

        self._last_awake[response.awake_type] = response.timestamp

        tasks: list[Coroutine[Any, Any, None]] = [
            self._reset_awake(response.timestamp),
            self.publish_feature_update_to_subscribers(
                NodeFeature.BATTERY,
                self._battery_config,
            ),
            self.save_cache(),
        ]
        self._delayed_task = self._loop.create_task(
            self._run_awake_tasks(), name=f"Delayed update for {self._mac_in_str}"
        )
        if response.awake_type == NodeAwakeResponseType.MAINTENANCE:
            self._last_awake_reason = "Maintenance"
            self._set_cache(CACHE_SED_AWAKE_REASON, "Maintenance")
            if not self._maintenance_interval_restored_from_cache:
                self._detect_maintenance_interval(response.timestamp)
            if self._ping_at_awake:
                tasks.append(self.update_ping_at_awake())
        elif response.awake_type == NodeAwakeResponseType.FIRST:
            self._last_awake_reason = "First"
            self._set_cache(CACHE_SED_AWAKE_REASON, "First")
        elif response.awake_type == NodeAwakeResponseType.STARTUP:
            self._last_awake_reason = "Startup"
            self._set_cache(CACHE_SED_AWAKE_REASON, "Startup")
        elif response.awake_type == NodeAwakeResponseType.STATE:
            self._last_awake_reason = "State update"
            self._set_cache(CACHE_SED_AWAKE_REASON, "State update")
        elif response.awake_type == NodeAwakeResponseType.BUTTON:
            self._last_awake_reason = "Button press"
            self._set_cache(CACHE_SED_AWAKE_REASON, "Button press")
            if self._ping_at_awake:
                tasks.append(self.update_ping_at_awake())

        await gather(*tasks)
        return True

    async def update_ping_at_awake(self) -> None:
        """Get ping statistics."""
        await self.ping_update()

    def _detect_maintenance_interval(self, timestamp: datetime) -> None:
        """Detect current maintenance interval."""
        if self._last_awake[NodeAwakeResponseType.MAINTENANCE] == timestamp:
            return

        new_interval_in_sec = (
            timestamp - self._last_awake[NodeAwakeResponseType.MAINTENANCE]
        ).seconds
        new_interval_in_min = round(new_interval_in_sec // 60)
        _LOGGER.warning(
            "Detect current maintenance interval for %s: %s (seconds), current %s (min)",
            self.name,
            new_interval_in_sec,
            self._battery_config.maintenance_interval,
        )
        # Validate new maintenance interval in seconds but store it in minutes
        if (new_interval_in_sec + SED_MAX_MAINTENANCE_INTERVAL_OFFSET) < (
            SED_DEFAULT_MAINTENANCE_INTERVAL * 60
        ):
            self._battery_config = replace(
                self._battery_config, maintenance_interval=new_interval_in_min
            )
            self._set_cache(CACHE_SED_MAINTENANCE_INTERVAL, new_interval_in_min)
        elif (new_interval_in_sec - SED_MAX_MAINTENANCE_INTERVAL_OFFSET) > (
            SED_DEFAULT_MAINTENANCE_INTERVAL * 60
        ):
            self._battery_config = replace(
                self._battery_config, maintenance_interval=new_interval_in_min
            )
            self._set_cache(CACHE_SED_MAINTENANCE_INTERVAL, new_interval_in_min)
        else:
            # Within off-set margin of default, so use the default
            self._battery_config = replace(
                self._battery_config,
                maintenance_interval=SED_DEFAULT_MAINTENANCE_INTERVAL,
            )
            self._set_cache(
                CACHE_SED_MAINTENANCE_INTERVAL, SED_DEFAULT_MAINTENANCE_INTERVAL
            )

        self._maintenance_interval_restored_from_cache = True

    async def _reset_awake(self, last_alive: datetime) -> None:
        """Reset node alive state."""
        if self._awake_future is not None and not self._awake_future.done():
            self._awake_future.set_result(True)
        # Setup new maintenance timer
        self._awake_future = self._loop.create_future()
        self._awake_timer_task = self._loop.create_task(
            self._awake_timer(), name=f"Node awake timer for {self._mac_in_str}"
        )

    async def _awake_timer(self) -> None:
        """Task to monitor to get next awake in time. If not it sets device to be unavailable."""
        # wait for next maintenance timer, but allow missing one
        if self._awake_future is None:
            return
        timeout_interval = self.maintenance_interval * 60 * 2.1
        try:
            await wait_for(
                self._awake_future,
                timeout=timeout_interval,
            )
        except TimeoutError:
            # No maintenance awake message within expected time frame
            # Mark node as unavailable
            if self._available:
                last_awake = self._last_awake.get(NodeAwakeResponseType.MAINTENANCE)
                _LOGGER.warning(
                    "No awake message received from %s | last_maintenance_awake=%s | interval=%s (%s) | Marking node as unavailable",
                    self.name,
                    last_awake,
                    self.maintenance_interval,
                    timeout_interval,
                )
                await self._available_update_state(False)
        except CancelledError:
            pass
        self._awake_future = None

    async def _run_awake_tasks(self) -> None:
        """Execute all awake tasks."""
        _LOGGER.debug("_run_awake_tasks | Device %s", self.name)
        if (
            self._sed_node_info_update_task_scheduled
            and await self.node_info_update(None) is not None
        ):
            self._sed_node_info_update_task_scheduled = False

        if self._battery_config.dirty:
            await self._configure_sed_task()

    async def sed_configure(self) -> bool:
        """Reconfigure the sleep/awake settings for a SED send at next awake of SED."""
        request = NodeSleepConfigRequest(
            self._send,
            self._mac_in_bytes,
            self._battery_config.awake_duration,
            self._battery_config.maintenance_interval,
            self._battery_config.sleep_duration,
            self._battery_config.clock_sync,
            self._battery_config.clock_interval,
        )
        _LOGGER.debug(
            "sed_configure | Device %s | awake_duration=%s | clock_interval=%s | clock_sync=%s | maintenance_interval=%s | sleep_duration=%s",
            self.name,
            self._battery_config.awake_duration,
            self._battery_config.clock_interval,
            self._battery_config.clock_sync,
            self._battery_config.maintenance_interval,
            self._battery_config.sleep_duration,
        )
        if (response := await request.send()) is None:
            _LOGGER.warning(
                "No response from %s to configure sleep settings request", self.name
            )
            return False
        if response.response_type == NodeResponseType.SED_CONFIG_FAILED:
            _LOGGER.warning("Failed to configure sleep settings for %s", self.name)
            return False
        if response.response_type == NodeResponseType.SED_CONFIG_ACCEPTED:
            self._battery_config = replace(self._battery_config, dirty=False)
            await self._sed_configure_update()
            return True
        _LOGGER.warning(
            "Unexpected response type %s for %s",
            response.response_type,
            self.name,
        )
        return False

    async def _sed_configure_update(self) -> None:
        """Process result of SED configuration update."""
        self._set_cache(
            CACHE_SED_MAINTENANCE_INTERVAL,
            str(self._battery_config.maintenance_interval),
        )
        self._set_cache(
            CACHE_SED_AWAKE_DURATION, str(self._battery_config.awake_duration)
        )
        self._set_cache(
            CACHE_SED_CLOCK_INTERVAL, str(self._battery_config.clock_interval)
        )
        self._set_cache(
            CACHE_SED_SLEEP_DURATION, str(self._battery_config.sleep_duration)
        )
        self._set_cache(CACHE_SED_CLOCK_SYNC, str(self._battery_config.clock_sync))
        self._set_cache(CACHE_SED_DIRTY, str(self._battery_config.dirty))
        await gather(
            *[
                self.save_cache(),
                self.publish_feature_update_to_subscribers(
                    NodeFeature.BATTERY,
                    self._battery_config,
                ),
            ]
        )

    async def get_state(self, features: tuple[NodeFeature]) -> dict[NodeFeature, Any]:
        """Update latest state for given feature."""
        states: dict[NodeFeature, Any] = {}
        for feature in features:
            if feature not in self._features:
                raise NodeError(
                    f"Update of feature '{feature.name}' is "
                    + f"not supported for {self.mac}"
                )

            match feature:
                case NodeFeature.INFO:
                    states[NodeFeature.INFO] = await self.node_info_update()
                case NodeFeature.BATTERY:
                    states[NodeFeature.BATTERY] = self._battery_config
                case _:
                    state_result = await super().get_state((feature,))
                    states[feature] = state_result[feature]

        return states
