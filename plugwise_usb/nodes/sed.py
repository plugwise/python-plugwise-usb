"""Plugwise SED (Sleeping Endpoint Device) base object."""

from __future__ import annotations

from asyncio import (
    CancelledError,
    Future,
    Lock,
    Task,
    gather,
    get_running_loop,
    wait_for,
)
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import replace
from datetime import datetime, timedelta
import logging
from typing import Any, Final

from ..api import BatteryConfig, NodeEvent, NodeFeature, NodeInfo
from ..connection import StickController
from ..constants import MAX_UINT_2, MAX_UINT_4
from ..exceptions import MessageError, NodeError
from ..messages.requests import NodeSleepConfigRequest
from ..messages.responses import (
    NODE_AWAKE_RESPONSE_ID,
    NodeAwakeResponse,
    NodeAwakeResponseType,
    NodeInfoResponse,
    NodeResponseType,
    PlugwiseResponse,
)
from .helpers import raise_not_loaded
from .node import PlugwiseBaseNode

CACHE_AWAKE_DURATION = "awake_duration"
CACHE_CLOCK_INTERVAL = "clock_interval"
CACHE_SLEEP_DURATION = "sleep_duration"
CACHE_CLOCK_SYNC = "clock_sync"
CACHE_MAINTENANCE_INTERVAL = "maintenance_interval"
CACHE_AWAKE_TIMESTAMP = "awake_timestamp"
CACHE_AWAKE_REASON = "awake_reason"

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
        address: int,
        controller: StickController,
        loaded_callback: Callable[[NodeEvent, str], Awaitable[None]],
    ):
        """Initialize base class for Sleeping End Device."""
        super().__init__(mac, address, controller, loaded_callback)
        self._loop = get_running_loop()
        self._node_info.is_battery_powered = True

        # Configure SED
        self._battery_config = BatteryConfig()
        self._new_battery_config = BatteryConfig()
        self._sed_config_task_scheduled = False
        self._send_task_queue: list[Coroutine[Any, Any, bool]] = []
        self._send_task_lock = Lock()
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
        if self._cache_enabled:
            _LOGGER.debug("Load SED node %s from cache", self._node_info.mac)
            await self._load_from_cache()
        else:
            self._load_defaults()
        self._loaded = True
        self._features += (NodeFeature.BATTERY,)
        if await self.initialize():
            await self._loaded_callback(NodeEvent.LOADED, self.mac)
            return True
        _LOGGER.debug("Load of SED node %s failed", self._node_info.mac)
        return False

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
        if len(self._send_task_queue) > 0:
            _LOGGER.warning(
                "Unable to execute %s open tasks for %s",
                len(self._send_task_queue),
                self.name,
            )
        await super().unload()

    @raise_not_loaded
    async def initialize(self) -> bool:
        """Initialize SED node."""
        if self._initialized:
            return True

        self._awake_subscription = await self._message_subscribe(
            self._awake_response,
            self._mac_in_bytes,
            (NODE_AWAKE_RESPONSE_ID,),
        )
        await super().initialize()
        return True

    def _load_defaults(self) -> None:
        """Load default configuration settings."""
        self._battery_config = BatteryConfig(
            awake_duration=SED_DEFAULT_AWAKE_DURATION,
            clock_interval=SED_DEFAULT_CLOCK_INTERVAL,
            clock_sync=SED_DEFAULT_CLOCK_SYNC,
            maintenance_interval=SED_DEFAULT_MAINTENANCE_INTERVAL,
            sleep_duration=SED_DEFAULT_SLEEP_DURATION,
        )

    async def _load_from_cache(self) -> bool:
        """Load states from previous cached information. Returns True if successful."""
        if not await super()._load_from_cache():
            self._load_defaults()
            return False
        self._battery_config = BatteryConfig(
            awake_duration=self._awake_duration_from_cache(),
            clock_interval=self._clock_interval_from_cache(),
            clock_sync=self._clock_sync_from_cache(),
            maintenance_interval=self._maintenance_interval_from_cache(),
            sleep_duration=self._sleep_duration_from_cache(),
        )
        self._awake_timestamp_from_cache()
        self._awake_reason_from_cache()
        return True

    def _awake_duration_from_cache(self) -> int:
        """Load awake duration from cache."""
        if (awake_duration := self._get_cache(CACHE_AWAKE_DURATION)) is not None:
            return int(awake_duration)
        return SED_DEFAULT_AWAKE_DURATION

    def _clock_interval_from_cache(self) -> int:
        """Load clock interval from cache."""
        if (clock_interval := self._get_cache(CACHE_CLOCK_INTERVAL)) is not None:
            return int(clock_interval)
        return SED_DEFAULT_CLOCK_INTERVAL

    def _clock_sync_from_cache(self) -> bool:
        """Load clock sync state from cache."""
        if (clock_sync := self._get_cache(CACHE_CLOCK_SYNC)) is not None:
            if clock_sync == "True":
                return True
            return False
        return SED_DEFAULT_CLOCK_SYNC

    def _maintenance_interval_from_cache(self) -> int:
        """Load maintenance interval from cache."""
        if (
            maintenance_interval := self._get_cache(CACHE_MAINTENANCE_INTERVAL)
        ) is not None:
            self._maintenance_interval_restored_from_cache = True
            return int(maintenance_interval)
        return SED_DEFAULT_MAINTENANCE_INTERVAL

    def _sleep_duration_from_cache(self) -> int:
        """Load sleep duration from cache."""
        if (sleep_duration := self._get_cache(CACHE_SLEEP_DURATION)) is not None:
            return int(sleep_duration)
        return SED_DEFAULT_SLEEP_DURATION

    def _awake_timestamp_from_cache(self) -> datetime | None:
        """Load last awake timestamp from cache."""
        return self._get_cache_as_datetime(CACHE_AWAKE_TIMESTAMP)

    def _awake_reason_from_cache(self) -> str | None:
        """Load last awake state from cache."""
        return self._get_cache(CACHE_AWAKE_REASON)

    # region Configuration actions
    @raise_not_loaded
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

        if self._battery_config.awake_duration == seconds:
            return False

        self._new_battery_config = replace(
            self._new_battery_config, awake_duration=seconds
        )
        if not self._sed_config_task_scheduled:
            await self.schedule_task_when_awake(self._configure_sed_task())
            self._sed_config_task_scheduled = True
            _LOGGER.debug(
                "set_awake_duration | Device %s | config scheduled",
                self.name,
            )

        return True

    @raise_not_loaded
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

        self._new_battery_config = replace(
            self._new_battery_config, clock_interval=minutes
        )
        if not self._sed_config_task_scheduled:
            await self.schedule_task_when_awake(self._configure_sed_task())
            self._sed_config_task_scheduled = True
            _LOGGER.debug(
                "set_clock_interval | Device %s | config scheduled",
                self.name,
            )

        return True

    @raise_not_loaded
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

        self._new_battery_config = replace(self._new_battery_config, clock_sync=sync)
        if not self._sed_config_task_scheduled:
            await self.schedule_task_when_awake(self._configure_sed_task())
            self._sed_config_task_scheduled = True
            _LOGGER.debug(
                "set_clock_sync | Device %s | config scheduled",
                self.name,
            )

        return True

    @raise_not_loaded
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

        self._new_battery_config = replace(
            self._new_battery_config, maintenance_interval=minutes
        )
        if not self._sed_config_task_scheduled:
            await self.schedule_task_when_awake(self._configure_sed_task())
            self._sed_config_task_scheduled = True
            _LOGGER.debug(
                "set_maintenance_interval | Device %s | config scheduled",
                self.name,
            )

        return True

    @raise_not_loaded
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

        self._new_battery_config = replace(
            self._new_battery_config, sleep_duration=minutes
        )
        if not self._sed_config_task_scheduled:
            await self.schedule_task_when_awake(self._configure_sed_task())
            self._sed_config_task_scheduled = True
            _LOGGER.debug(
                "set_sleep_duration | Device %s | config scheduled",
                self.name,
            )

        return True

    # endregion
    # region Properties
    @property
    @raise_not_loaded
    def awake_duration(self) -> int:
        """Duration in seconds a battery powered devices is awake."""
        if self._new_battery_config.awake_duration is not None:
            return self._new_battery_config.awake_duration
        if self._battery_config.awake_duration is not None:
            return self._battery_config.awake_duration
        return SED_DEFAULT_AWAKE_DURATION

    @property
    @raise_not_loaded
    def battery_config(self) -> BatteryConfig:
        """Battery related configuration settings."""
        return BatteryConfig(
            awake_duration=self.awake_duration,
            clock_interval=self.clock_interval,
            clock_sync=self.clock_sync,
            maintenance_interval=self.maintenance_interval,
            sleep_duration=self.sleep_duration,
        )

    @property
    @raise_not_loaded
    def clock_interval(self) -> int:
        """Return the clock interval value."""
        if self._new_battery_config.clock_interval is not None:
            return self._new_battery_config.clock_interval
        if self._battery_config.clock_interval is not None:
            return self._battery_config.clock_interval
        return SED_DEFAULT_CLOCK_INTERVAL

    @property
    @raise_not_loaded
    def clock_sync(self) -> bool:
        """Indicate if the internal clock must be synced."""
        if self._new_battery_config.clock_sync is not None:
            return self._new_battery_config.clock_sync
        if self._battery_config.clock_sync is not None:
            return self._battery_config.clock_sync
        return SED_DEFAULT_CLOCK_SYNC

    @property
    @raise_not_loaded
    def maintenance_interval(self) -> int:
        """Return the maintenance interval value.

        When value is scheduled to be changed the return value is the optimistic value.
        """
        if self._new_battery_config.maintenance_interval is not None:
            return self._new_battery_config.maintenance_interval
        if self._battery_config.maintenance_interval is not None:
            return self._battery_config.maintenance_interval
        return SED_DEFAULT_MAINTENANCE_INTERVAL

    @property
    def sed_config_task_scheduled(self) -> bool:
        """Check if a configuration task is scheduled."""
        return self._sed_config_task_scheduled

    @property
    @raise_not_loaded
    def sleep_duration(self) -> int:
        """Return the sleep duration value in minutes.

        When value is scheduled to be changed the return value is the optimistic value.
        """
        if self._new_battery_config.sleep_duration is not None:
            return self._new_battery_config.sleep_duration
        if self._battery_config.sleep_duration is not None:
            return self._battery_config.sleep_duration
        return SED_DEFAULT_SLEEP_DURATION

    # endregion
    async def _configure_sed_task(self) -> bool:
        """Configure SED settings. Returns True if successful."""
        _LOGGER.debug(
            "_configure_sed_task | Device %s | start",
            self.name,
        )
        self._sed_config_task_scheduled = False
        change_required = False
        if (
            self._new_battery_config.awake_duration is not None
            or self._new_battery_config.clock_interval is not None
            or self._new_battery_config.clock_sync is not None
            or self._new_battery_config.maintenance_interval is not None
            or self._new_battery_config.sleep_duration is not None
        ):
            change_required = True

        if not change_required:
            _LOGGER.debug(
                "_configure_sed_task | Device %s | no change",
                self.name,
            )
            return True

        _LOGGER.debug(
            "_configure_sed_task | Device %s | request change",
            self.name,
        )
        if not await self.sed_configure(
            awake_duration=self.awake_duration,
            clock_interval=self.clock_interval,
            clock_sync=self.clock_sync,
            maintenance_interval=self.maintenance_interval,
            sleep_duration=self.sleep_duration,
        ):
            return False

        return True

    async def node_info_update(
        self, node_info: NodeInfoResponse | None = None
    ) -> NodeInfo | None:
        """Update Node (hardware) information."""
        if node_info is None and self.skip_update(self._node_info, 86400):
            return self._node_info
        return await super().node_info_update(node_info)

    async def _awake_response(self, response: PlugwiseResponse) -> bool:
        """Process awake message."""
        if not isinstance(response, NodeAwakeResponse):
            raise MessageError(
                f"Invalid response message type ({response.__class__.__name__}) received, expected NodeAwakeResponse"
            )

        _LOGGER.debug("Device %s is awake for %s", self.name, response.awake_type)
        self._set_cache(CACHE_AWAKE_TIMESTAMP, response.timestamp)
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
            self._send_tasks(), name=f"Delayed update for {self._mac_in_str}"
        )
        if response.awake_type == NodeAwakeResponseType.MAINTENANCE:
            self._last_awake_reason = "Maintenance"
            self._set_cache(CACHE_AWAKE_REASON, "Maintenance")

            if not self._maintenance_interval_restored_from_cache:
                self._detect_maintenance_interval(response.timestamp)
            if self._ping_at_awake:
                tasks.append(self.update_ping_at_awake())
        elif response.awake_type == NodeAwakeResponseType.FIRST:
            self._last_awake_reason = "First"
            self._set_cache(CACHE_AWAKE_REASON, "First")
        elif response.awake_type == NodeAwakeResponseType.STARTUP:
            self._last_awake_reason = "Startup"
            self._set_cache(CACHE_AWAKE_REASON, "Startup")
        elif response.awake_type == NodeAwakeResponseType.STATE:
            self._last_awake_reason = "State update"
            self._set_cache(CACHE_AWAKE_REASON, "State update")
        elif response.awake_type == NodeAwakeResponseType.BUTTON:
            self._last_awake_reason = "Button press"
            self._set_cache(CACHE_AWAKE_REASON, "Button press")
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
            self._set_cache(CACHE_MAINTENANCE_INTERVAL, new_interval_in_min)
        elif (new_interval_in_sec - SED_MAX_MAINTENANCE_INTERVAL_OFFSET) > (
            SED_DEFAULT_MAINTENANCE_INTERVAL * 60
        ):
            self._battery_config = replace(
                self._battery_config, maintenance_interval=new_interval_in_min
            )
            self._set_cache(CACHE_MAINTENANCE_INTERVAL, new_interval_in_min)
        else:
            # Within off-set margin of default, so use the default
            self._battery_config = replace(
                self._battery_config,
                maintenance_interval=SED_DEFAULT_MAINTENANCE_INTERVAL,
            )
            self._set_cache(
                CACHE_MAINTENANCE_INTERVAL, SED_DEFAULT_MAINTENANCE_INTERVAL
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

    async def _send_tasks(self) -> None:
        """Send all tasks in queue."""
        if len(self._send_task_queue) == 0:
            return

        async with self._send_task_lock:
            task_result = await gather(*self._send_task_queue)
            if not all(task_result):
                _LOGGER.warning(
                    "Executed %s tasks (result=%s) for %s",
                    len(self._send_task_queue),
                    task_result,
                    self.name,
                )
            self._send_task_queue = []

    async def schedule_task_when_awake(
        self, task_fn: Coroutine[Any, Any, bool]
    ) -> None:
        """Add task to queue to be executed when node is awake."""
        async with self._send_task_lock:
            self._send_task_queue.append(task_fn)

    async def sed_configure(  # pylint: disable=too-many-arguments
        self,
        awake_duration: int,
        sleep_duration: int,
        maintenance_interval: int,
        clock_sync: bool,
        clock_interval: int,
    ) -> bool:
        """Reconfigure the sleep/awake settings for a SED send at next awake of SED."""
        request = NodeSleepConfigRequest(
            self._send,
            self._mac_in_bytes,
            awake_duration,
            maintenance_interval,
            sleep_duration,
            clock_sync,
            clock_interval,
        )
        _LOGGER.debug(
            "sed_configure | Device %s | awake_duration=%s | clock_interval=%s | clock_sync=%s | maintenance_interval=%s | sleep_duration=%s",
            self.name,
            awake_duration,
            clock_interval,
            clock_sync,
            maintenance_interval,
            sleep_duration,
        )
        if (response := await request.send()) is None:
            self._new_battery_config = BatteryConfig()
            _LOGGER.warning(
                "No response from %s to configure sleep settings request", self.name
            )
            return False
        if response.response_type == NodeResponseType.SED_CONFIG_FAILED:
            self._new_battery_config = BatteryConfig()
            _LOGGER.warning("Failed to configure sleep settings for %s", self.name)
            return False
        if response.response_type == NodeResponseType.SED_CONFIG_ACCEPTED:
            await self._sed_configure_update(
                awake_duration,
                clock_interval,
                clock_sync,
                maintenance_interval,
                sleep_duration,
            )
            self._new_battery_config = BatteryConfig()
            return True
        _LOGGER.warning(
            "Unexpected response type %s for %s",
            response.response_type,
            self.name,
        )
        return False

    # pylint: disable=too-many-arguments
    async def _sed_configure_update(
        self,
        awake_duration: int = SED_DEFAULT_AWAKE_DURATION,
        clock_interval: int = SED_DEFAULT_CLOCK_INTERVAL,
        clock_sync: bool = SED_DEFAULT_CLOCK_SYNC,
        maintenance_interval: int = SED_DEFAULT_MAINTENANCE_INTERVAL,
        sleep_duration: int = SED_DEFAULT_SLEEP_DURATION,
    ) -> None:
        """Process result of SED configuration update."""
        self._battery_config = BatteryConfig(
            awake_duration=awake_duration,
            clock_interval=clock_interval,
            clock_sync=clock_sync,
            maintenance_interval=maintenance_interval,
            sleep_duration=sleep_duration,
        )
        self._set_cache(CACHE_MAINTENANCE_INTERVAL, str(maintenance_interval))
        self._set_cache(CACHE_AWAKE_DURATION, str(awake_duration))
        self._set_cache(CACHE_CLOCK_INTERVAL, str(clock_interval))
        self._set_cache(CACHE_SLEEP_DURATION, str(sleep_duration))
        if clock_sync:
            self._set_cache(CACHE_CLOCK_SYNC, "True")
        else:
            self._set_cache(CACHE_CLOCK_SYNC, "False")
        await gather(
            *[
                self.save_cache(),
                self.publish_feature_update_to_subscribers(
                    NodeFeature.BATTERY,
                    self._battery_config,
                ),
            ]
        )

    @raise_not_loaded
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
