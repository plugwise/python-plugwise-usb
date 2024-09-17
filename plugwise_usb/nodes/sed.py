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
from datetime import datetime
import logging
from typing import Any, Final

from ..api import NodeEvent, NodeFeature, NodeInfo
from ..connection import StickController
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
from ..nodes import PlugwiseNode
from .helpers import raise_not_loaded

# Defaults for 'Sleeping End Devices'

# Time in seconds the SED keep itself awake to receive
# and respond to other messages
SED_STAY_ACTIVE: Final = 10

# Time in minutes the SED will sleep
SED_SLEEP_FOR: Final = 60

# 24 hours, Interval in minutes the SED will get awake and notify
# it's available for maintenance purposes
SED_MAINTENANCE_INTERVAL: Final = 1440

# Enable or disable synchronizing clock
SED_CLOCK_SYNC: Final = True

# 7 days, duration in minutes the node synchronize its clock
SED_CLOCK_INTERVAL: Final = 25200


CACHE_MAINTENANCE_INTERVAL = "maintenance_interval"

_LOGGER = logging.getLogger(__name__)


class NodeSED(PlugwiseNode):
    """provides base class for SED based nodes like Scan, Sense & Switch."""

    # SED configuration
    _sed_configure_at_awake = False
    _sed_config_stay_active: int | None = None
    _sed_config_sleep_for: int | None = None
    _sed_config_maintenance_interval: int | None = None
    _sed_config_clock_sync: bool | None = None
    _sed_config_clock_interval: int | None = None

    # Maintenance
    _maintenance_last_awake: datetime | None = None
    _awake_future: Future[bool] | None = None
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
        self._node_info.battery_powered = True
        self._maintenance_interval = 86400  # Assume standard interval of 24h
        self._send_task_queue: list[Coroutine[Any, Any, bool]] = []
        self._send_task_lock = Lock()

    async def unload(self) -> None:
        """Deactivate and unload node features."""
        if self._awake_future is not None:
            self._awake_future.set_result(True)
        if self._awake_timer_task is not None and not self._awake_timer_task.done():
            await self._awake_timer_task
        if self._awake_subscription is not None:
            self._awake_subscription()
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
        self._awake_subscription = self._message_subscribe(
            self._awake_response,
            self._mac_in_bytes,
            (NODE_AWAKE_RESPONSE_ID,),
        )
        return await super().initialize()

    async def _load_from_cache(self) -> bool:
        """Load states from previous cached information. Returns True if successful."""
        if not await super()._load_from_cache():
            return False
        self.maintenance_interval_from_cache()
        return True

    def maintenance_interval_from_cache(self) -> bool:
        """Load maintenance interval from cache."""
        if (
            cached_maintenance_interval := self._get_cache(CACHE_MAINTENANCE_INTERVAL)
        ) is not None:
            _LOGGER.debug(
                "Restore maintenance interval cache for node %s", self._mac_in_str
            )
            self._maintenance_interval = int(cached_maintenance_interval)
        return True

    @property
    def maintenance_interval(self) -> int | None:
        """Heartbeat maintenance interval (seconds)."""
        return self._maintenance_interval

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
                f"Invalid response message type ({response.__class__.__name__}) received, expected NodeSwitchGroupResponse"
            )
        self._node_last_online = response.timestamp
        await self._available_update_state(True)
        if response.awake_type == NodeAwakeResponseType.MAINTENANCE:
            if self._maintenance_last_awake is None:
                self._maintenance_last_awake = response.timestamp
            self._maintenance_interval = (
                response.timestamp - self._maintenance_last_awake
            ).seconds
            if self._ping_at_awake:
                await self.ping_update()
        elif response.awake_type == NodeAwakeResponseType.FIRST:
            _LOGGER.info("Device %s is turned on for first time", self.name)
        elif response.awake_type == NodeAwakeResponseType.STARTUP:
            _LOGGER.info("Device %s is restarted", self.name)
        elif response.awake_type == NodeAwakeResponseType.STATE:
            _LOGGER.info("Device %s is awake to send status update", self.name)
        elif response.awake_type == NodeAwakeResponseType.BUTTON:
            _LOGGER.info("Button is pressed at device %s", self.name)
        await self._reset_awake(response.timestamp)
        return True

    async def _reset_awake(self, last_alive: datetime) -> None:
        """Reset node alive state."""
        if self._awake_future is not None:
            self._awake_future.set_result(True)

        # Setup new maintenance timer
        self._awake_future = self._loop.create_future()
        self._awake_timer_task = self._loop.create_task(
            self._awake_timer(), name=f"Node awake timer for {self._mac_in_str}"
        )

    async def _awake_timer(self) -> None:
        """Task to monitor to get next awake in time. If not it sets device to be unavailable."""
        # wait for next maintenance timer
        if self._awake_future is None:
            return
        try:
            await wait_for(
                self._awake_future,
                timeout=(self._maintenance_interval * 1.05),
            )
        except TimeoutError:
            # No maintenance awake message within expected time frame
            # Mark node as unavailable
            if self._available:
                _LOGGER.info(
                    "No awake message received from %s within expected %s seconds.",
                    self.name,
                    str(self._maintenance_interval * 1.05),
                )
                await self._available_update_state(False)
        except CancelledError:
            pass
        self._awake_future = None

    async def _send_tasks(self) -> None:
        """Send all tasks in queue."""
        if len(self._send_task_queue) == 0:
            return

        await self._send_task_lock.acquire()
        task_result = await gather(*self._send_task_queue)

        if not all(task_result):
            _LOGGER.warning(
                "Executed %s tasks (result=%s) for %s",
                len(self._send_task_queue),
                task_result,
                self.name,
            )
        else:
            self._send_task_queue = []
        self._send_task_lock.release()

    async def schedule_task_when_awake(
        self, task_fn: Coroutine[Any, Any, bool]
    ) -> None:
        """Add task to queue to be executed when node is awake."""
        await self._send_task_lock.acquire()
        self._send_task_queue.append(task_fn)
        self._send_task_lock.release()

    async def sed_configure(
        self,
        stay_active: int = SED_STAY_ACTIVE,
        sleep_for: int = SED_SLEEP_FOR,
        maintenance_interval: int = SED_MAINTENANCE_INTERVAL,
        clock_sync: bool = SED_CLOCK_SYNC,
        clock_interval: int = SED_CLOCK_INTERVAL,
    ) -> bool:
        """Reconfigure the sleep/awake settings for a SED send at next awake of SED."""
        request = NodeSleepConfigRequest(
            self._send,
            self._mac_in_bytes,
            stay_active,
            maintenance_interval,
            sleep_for,
            clock_sync,
            clock_interval,
        )
        if (response := await request.send()) is not None:
            if response.ack_id == NodeResponseType.SLEEP_CONFIG_FAILED:
                raise NodeError("SED failed to configure sleep settings")
            if response.ack_id == NodeResponseType.SLEEP_CONFIG_ACCEPTED:
                self._maintenance_interval = maintenance_interval
                return True
        return False

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
            if feature == NodeFeature.INFO:
                states[NodeFeature.INFO] = await self.node_info_update()
            else:
                state_result = await super().get_state((feature,))
                states[feature] = state_result[feature]
        return states
