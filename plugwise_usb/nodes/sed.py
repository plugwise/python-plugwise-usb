"""Plugwise SED (Sleeping Endpoint Device) base object."""

from __future__ import annotations

from asyncio import CancelledError, Future, get_event_loop, wait_for
from collections.abc import Callable
from datetime import datetime
import logging
from typing import Final

from ..connection import StickController
from ..exceptions import NodeError, NodeTimeout
from ..messages.requests import NodeSleepConfigRequest
from ..messages.responses import (
    NODE_AWAKE_RESPONSE_ID,
    NodeAwakeResponse,
    NodeAwakeResponseType,
    NodeInfoResponse,
    NodePingResponse,
    NodeResponse,
    NodeResponseType,
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
    _maintenance_interval: int | None = None
    _maintenance_last_awake: datetime | None = None
    _maintenance_future: Future | None = None

    _ping_at_awake: bool = False

    _awake_subscription: Callable[[], None] | None = None

    def __init__(
        self,
        mac: str,
        address: int,
        controller: StickController,
    ):
        """Initialize SED"""
        super().__init__(mac, address, controller)
        self._message_subscribe = controller.subscribe_to_node_responses

    async def unload(self) -> None:
        """Deactivate and unload node features."""
        if self._maintenance_future is not None:
            self._maintenance_future.cancel()
        if self._awake_subscription is not None:
            self._awake_subscription()
        await self.save_cache()
        self._loaded = False

    @raise_not_loaded
    async def initialize(self) -> bool:
        """Initialize SED node."""
        if self._initialized:
            return True
        self._awake_subscription = self._message_subscribe(
            self._awake_response,
            self._mac_in_bytes,
            NODE_AWAKE_RESPONSE_ID,
        )
        return True

    @property
    def maintenance_interval(self) -> int | None:
        """Heartbeat maintenance interval (seconds)."""
        return self._maintenance_interval

    async def node_info_update(
        self, node_info: NodeInfoResponse | None = None
    ) -> bool:
        """Update Node (hardware) information."""
        if node_info is None and self.skip_update(self._node_info, 86400):
            return True
        return await super().node_info_update(node_info)


    async def _awake_response(self, message: NodeAwakeResponse) -> None:
        """Process awake message."""
        self._node_last_online = message.timestamp
        await self._available_update_state(True)
        if message.timestamp is None:
            return
        if (
            NodeAwakeResponseType(message.awake_type.value)
            == NodeAwakeResponseType.MAINTENANCE
        ):
            if self._ping_at_awake:
                ping_response: NodePingResponse | None = (
                    await self.ping_update()  # type: ignore [assignment]
                )
                if ping_response is not None:
                    self._ping_at_awake = False
            await self.reset_maintenance_awake(message.timestamp)

    async def reset_maintenance_awake(self, last_alive: datetime) -> None:
        """Reset node alive state."""
        if self._maintenance_last_awake is None:
            self._maintenance_last_awake = last_alive
            return
        self._maintenance_interval = (
            last_alive - self._maintenance_last_awake
        ).seconds

        # Finish previous maintenance timer
        if self._maintenance_future is not None:
            self._maintenance_future.set_result(True)

        # Setup new maintenance timer
        self._maintenance_future = get_event_loop().create_future()

        # wait for next maintenance timer
        try:
            await wait_for(
                self._maintenance_future,
                timeout=(self._maintenance_interval * 1.05),
            )
        except AsyncTimeOutError:
            # No maintenance awake message within expected time frame
            # Mark node as unavailable
            if self._available:
                _LOGGER.info(
                    "No maintenance awake message received for %s within expected %s seconds.",
                    self.mac,
                    str(self._maintenance_interval * 1.05),
                )
                await self._available_update_state(False)
        except CancelledError:
            pass

        self._maintenance_future = None

    async def sed_configure(
        self,
        stay_active: int = SED_STAY_ACTIVE,
        sleep_for: int = SED_SLEEP_FOR,
        maintenance_interval: int = SED_MAINTENANCE_INTERVAL,
        clock_sync: bool = SED_CLOCK_SYNC,
        clock_interval: int = SED_CLOCK_INTERVAL,
        awake: bool = False,
    ) -> None:
        """Reconfigure the sleep/awake settings for a SED send at next awake of SED."""
        if not awake:
            self._sed_configure_at_awake = True
            self._sed_config_stay_active = stay_active
            self._sed_config_sleep_for = sleep_for
            self._sed_config_maintenance_interval = maintenance_interval
            self._sed_config_clock_sync = clock_sync
            self._sed_config_clock_interval = clock_interval
            return
        response: NodeResponse | None = await self._send(
            NodeSleepConfigRequest(
                self._mac_in_bytes,
                stay_active,
                maintenance_interval,
                sleep_for,
                clock_sync,
                clock_interval,
            )
        )
        if response is None:
            raise NodeTimeout(
                "No response to 'NodeSleepConfigRequest' from node " + self.mac
            )
        if response.ack_id == NodeResponseType.SLEEP_CONFIG_FAILED:
            raise NodeError("SED failed to configure sleep settings")
        if response.ack_id == NodeResponseType.SLEEP_CONFIG_ACCEPTED:
            self._maintenance_interval = maintenance_interval
