"""Plugwise Circle node class."""

from __future__ import annotations

from asyncio import CancelledError, Task, create_task, gather, sleep
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import wraps
import logging
from math import ceil
import random
from typing import Any, Final, TypeVar, cast

from ..api import (
    EnergyStatistics,
    NodeEvent,
    NodeFeature,
    NodeInfo,
    NodeInfoMessage,
    NodeType,
    PowerStatistics,
    RelayConfig,
    RelayLock,
    RelayState,
)
from ..connection import StickController
from ..constants import (
    DAY_IN_HOURS,
    DEFAULT_CONS_INTERVAL,
    MAX_TIME_DRIFT,
    MINIMAL_POWER_UPDATE,
    NO_PRODUCTION_INTERVAL,
    PULSES_PER_KW_SECOND,
    SECOND_IN_NANOSECONDS,
)
from ..exceptions import FeatureError, MessageError, NodeError
from ..messages.requests import (
    CircleClockGetRequest,
    CircleClockSetRequest,
    CircleEnergyLogsRequest,
    CircleMeasureIntervalRequest,
    CirclePowerUsageRequest,
    CircleRelayInitStateRequest,
    CircleRelaySwitchRequest,
    EnergyCalibrationRequest,
    NodeInfoRequest,
)
from ..messages.responses import NodeInfoResponse, NodeResponseType
from .helpers import EnergyCalibration, raise_not_loaded
from .helpers.counter import EnergyCounters
from .helpers.firmware import CIRCLE_FIRMWARE_SUPPORT
from .helpers.pulses import PulseLogRecord, calc_log_address
from .node import PlugwiseBaseNode

CACHE_CALIBRATION_GAIN_A = "calibration_gain_a"
CACHE_CALIBRATION_GAIN_B = "calibration_gain_b"
CACHE_CALIBRATION_NOISE = "calibration_noise"
CACHE_CALIBRATION_TOT = "calibration_tot"
CACHE_ENERGY_COLLECTION = "energy_collection"
CACHE_RELAY = "relay"
CACHE_RELAY_INIT = "relay_init"
CACHE_RELAY_LOCK = "relay_lock"

CIRCLE_FEATURES: Final = (
    NodeFeature.CIRCLE,
    NodeFeature.RELAY,
    NodeFeature.RELAY_INIT,
    NodeFeature.RELAY_LOCK,
    NodeFeature.ENERGY,
    NodeFeature.POWER,
)


# Default firmware if not known
DEFAULT_FIRMWARE: Final = datetime(2008, 8, 26, 15, 46, tzinfo=UTC)

MAX_LOG_HOURS: Final = DAY_IN_HOURS

CLOCK_SYNC_PERIOD: Final = 3600

FuncT = TypeVar("FuncT", bound=Callable[..., Any])
_LOGGER = logging.getLogger(__name__)


def _collect_records(data: str) -> dict[int, dict[int, tuple[datetime, int]]]:
    """Collect logs from a cache data string."""
    logs: dict[int, dict[int, tuple[datetime, int]]] = {}
    log_data = data.split("|")
    for log_record in log_data:
        log_fields = log_record.split(":")
        if len(log_fields) == 4:
            address = int(log_fields[0])
            slot = int(log_fields[1])
            pulses = int(log_fields[3])
            # Parse zero-padded timestamp, fallback to manual split
            try:
                timestamp = datetime.strptime(
                    log_fields[2], "%Y-%m-%d-%H-%M-%S"
                ).replace(tzinfo=UTC)
            except ValueError:
                parts = log_fields[2].split("-")
                if len(parts) != 6:
                    continue
                timestamp = datetime(
                    year=int(parts[0]),
                    month=int(parts[1]),
                    day=int(parts[2]),
                    hour=int(parts[3]),
                    minute=int(parts[4]),
                    second=int(parts[5]),
                    tzinfo=UTC,
                )
            bucket = logs.setdefault(address, {})
            # Keep the first occurrence (cache is newest-first), skip older duplicates
            if slot not in bucket:
                bucket[slot] = (timestamp, pulses)

    return logs


def raise_calibration_missing(func: FuncT) -> FuncT:
    """Validate energy calibration settings are available."""

    @wraps(func)
    def decorated(*args: Any, **kwargs: Any) -> Any:
        if args[0].calibrated is None:
            raise NodeError("Energy calibration settings are missing")
        return func(*args, **kwargs)

    return cast(FuncT, decorated)


class PlugwiseCircle(PlugwiseBaseNode):
    """Plugwise Circle node."""

    def __init__(
        self,
        mac: str,
        node_type: NodeType,
        controller: StickController,
        loaded_callback: Callable[[NodeEvent, str], Awaitable[None]],
    ):
        """Initialize base class for Sleeping End Device."""
        super().__init__(mac, node_type, controller, loaded_callback)

        # Clock
        self._clock_synchronize_task: Task[None] | None = None
        # Relay
        self._relay_lock: RelayLock = RelayLock()
        self._relay_state: RelayState = RelayState()
        self._relay_config: RelayConfig = RelayConfig()

        # Power
        self._power: PowerStatistics = PowerStatistics()
        self._calibration: EnergyCalibration | None = None

        # Energy
        self._energy_counters = EnergyCounters(mac)
        self._retrieve_energy_logs_task: None | Task[None] = None
        self._last_energy_log_requested: bool = False

        self._group_member: list[int] = []

    # region Properties

    @property
    def calibrated(self) -> bool:
        """State of calibration."""
        if self._calibration is not None:
            return True
        return False

    @property
    def energy(self) -> EnergyStatistics:
        """Energy statistics."""
        return self._energy_counters.energy_statistics

    @property
    @raise_not_loaded
    def energy_consumption_interval(self) -> int | None:
        """Interval (minutes) energy consumption counters are locally logged at Circle devices."""
        if NodeFeature.ENERGY not in self._features:
            raise NodeError(f"Energy log interval is not supported for node {self.mac}")
        return self._energy_counters.consumption_interval

    @property
    def energy_production_interval(self) -> int | None:
        """Interval (minutes) energy production counters are locally logged at Circle devices."""
        if NodeFeature.ENERGY not in self._features:
            raise NodeError(f"Energy log interval is not supported for node {self.mac}")
        return self._energy_counters.production_interval

    @property
    @raise_not_loaded
    def power(self) -> PowerStatistics:
        """Power statistics."""
        return self._power

    @property
    @raise_not_loaded
    def relay(self) -> bool:
        """Current value of relay."""
        return bool(self._relay_state.state)

    @property
    @raise_not_loaded
    def relay_config(self) -> RelayConfig:
        """Configuration state of relay."""
        if NodeFeature.RELAY_INIT not in self._features:
            raise FeatureError(
                f"Configuration of relay is not supported for device {self.name}"
            )
        return self._relay_config

    @property
    @raise_not_loaded
    def relay_state(self) -> RelayState:
        """State of relay."""
        return self._relay_state

    @raise_not_loaded
    async def relay_off(self) -> None:
        """Switch relay off."""
        await self.set_relay(False)

    @raise_not_loaded
    async def relay_on(self) -> None:
        """Switch relay on."""
        await self.set_relay(True)

    @raise_not_loaded
    async def relay_init_off(self) -> None:
        """Switch relay off."""
        await self._relay_init_set(False)

    @raise_not_loaded
    async def relay_init_on(self) -> None:
        """Switch relay on."""
        await self._relay_init_set(True)

    @property
    def relay_lock(self) -> RelayLock:
        """State of the relay lock."""
        return self._relay_lock

    # endregion

    async def calibration_update(self) -> bool:
        """Retrieve and update calibration settings. Returns True if successful."""
        _LOGGER.debug(
            "Start updating energy calibration for %s",
            self._mac_in_str,
        )
        request = EnergyCalibrationRequest(self._send, self._mac_in_bytes)
        if (calibration_response := await request.send()) is None:
            _LOGGER.warning(
                "Retrieving energy calibration information for %s failed",
                self.name,
            )
            await self._available_update_state(False)
            return False
        await self._available_update_state(True, calibration_response.timestamp)
        await self._calibration_update_state(
            calibration_response.gain_a,
            calibration_response.gain_b,
            calibration_response.off_noise,
            calibration_response.off_tot,
        )
        _LOGGER.debug(
            "Updating energy calibration for %s succeeded",
            self._mac_in_str,
        )
        return True

    async def _calibration_load_from_cache(self) -> bool:
        """Load calibration settings from cache."""
        cal_gain_a: float | None = None
        cal_gain_b: float | None = None
        cal_noise: float | None = None
        cal_tot: float | None = None
        if (gain_a := self._get_cache(CACHE_CALIBRATION_GAIN_A)) is not None:
            cal_gain_a = float(gain_a)
        if (gain_b := self._get_cache(CACHE_CALIBRATION_GAIN_B)) is not None:
            cal_gain_b = float(gain_b)
        if (noise := self._get_cache(CACHE_CALIBRATION_NOISE)) is not None:
            cal_noise = float(noise)
        if (tot := self._get_cache(CACHE_CALIBRATION_TOT)) is not None:
            cal_tot = float(tot)

        # Restore calibration
        result = await self._calibration_update_state(
            cal_gain_a,
            cal_gain_b,
            cal_noise,
            cal_tot,
            load_from_cache=True,
        )
        if result:
            _LOGGER.debug(
                "Restore calibration settings from cache for %s was successful",
                self._mac_in_str,
            )
            return True
        _LOGGER.info(
            "Failed to restore calibration settings from cache for %s", self.name
        )
        return False

    async def _calibration_update_state(
        self,
        gain_a: float | None,
        gain_b: float | None,
        off_noise: float | None,
        off_tot: float | None,
        load_from_cache: bool = False,
    ) -> bool:
        """Process new energy calibration settings. Returns True if successful."""
        if gain_a is None or gain_b is None or off_noise is None or off_tot is None:
            return False
        self._calibration = EnergyCalibration(
            gain_a=gain_a, gain_b=gain_b, off_noise=off_noise, off_tot=off_tot
        )
        # Forward calibration config to energy collection
        self._energy_counters.calibration = self._calibration
        if self._cache_enabled and not load_from_cache:
            self._set_cache(CACHE_CALIBRATION_GAIN_A, gain_a)
            self._set_cache(CACHE_CALIBRATION_GAIN_B, gain_b)
            self._set_cache(CACHE_CALIBRATION_NOISE, off_noise)
            self._set_cache(CACHE_CALIBRATION_TOT, off_tot)
            await self.save_cache()
        return True

    @raise_calibration_missing
    async def power_update(self) -> PowerStatistics | None:
        """Update the current power usage statistics.

        Return power usage or None if retrieval failed
        """
        # Debounce power
        if self.skip_update(self._power, MINIMAL_POWER_UPDATE):
            return self._power

        request = CirclePowerUsageRequest(self._send, self._mac_in_bytes)
        response = await request.send()
        if response is None or response.timestamp is None:
            _LOGGER.debug(
                "No response for async_power_update() for %s", self._mac_in_str
            )
            await self._available_update_state(False)
            return None
        await self._available_update_state(True, response.timestamp)

        # Update power stats
        self._power.last_second = self._calc_watts(
            response.pulse_1s, 1, response.offset
        )
        self._power.last_8_seconds = self._calc_watts(
            response.pulse_8s, 8, response.offset
        )
        self._power.timestamp = response.timestamp
        await self.publish_feature_update_to_subscribers(NodeFeature.POWER, self._power)

        # Forward pulse interval counters to pulse Collection
        self._energy_counters.add_pulse_stats(
            response.consumed_counter,
            response.produced_counter,
            response.timestamp,
        )
        await self.publish_feature_update_to_subscribers(
            NodeFeature.ENERGY, self._energy_counters.energy_statistics
        )
        return self._power

    def _log_no_energy_stats_update(self) -> None:
        """Return log-message based on conditions."""
        if (
            self._initialization_delay_expired is not None
            and datetime.now(tz=UTC) < self._initialization_delay_expired
        ):
            _LOGGER.info(
                "Unable to return energy statistics for %s during initialization, because it is not responding",
                self.name,
            )
        else:
            _LOGGER.warning(
                "Unable to return energy statistics for %s, because it is not responding",
                self.name,
            )

    @raise_not_loaded
    @raise_calibration_missing
    async def energy_update(self) -> EnergyStatistics | None:  # noqa: PLR0911 PLR0912
        """Return updated energy usage statistics."""
        if self._current_log_address is None:
            _LOGGER.debug(
                "Unable to update energy logs for node %s because the current log address is unknown.",
                self._mac_in_str,
            )
            if await self.node_info_update() is None:
                self._log_no_energy_stats_update()
                return None
        # Request node info update every 30 minutes.
        elif not self.skip_update(self._node_info, 1800):
            if await self.node_info_update() is None:
                self._log_no_energy_stats_update()
                return None

        # Always request the most recent energy log records at initial startup, check if the current
        # address is actually reported by the node even when all slots at that address are empty.
        if not self._last_energy_log_requested:
            self._last_energy_log_requested, _ = await self.energy_log_update(
                self._current_log_address, save_cache=False
            )

        if self._energy_counters.log_rollover:
            # Try updating node_info to collect the updated energy log address
            if await self.node_info_update() is None:
                _LOGGER.debug(
                    "async_energy_update | %s | Log rollover | node_info_update failed",
                    self._mac_in_str,
                )
                return None

            # Try collecting energy-stats from _current_log_address
            result, slots_empty_cur = await self.energy_log_update(
                self._current_log_address, save_cache=False
            )
            if not result:
                _LOGGER.debug(
                    "async_energy_update | %s | Log rollover | energy_log_update from address %s failed",
                    self._mac_in_str,
                    self._current_log_address,
                )
                return None

            # Retry with previous log address as Circle node pointer to self._current_log_address
            # could be rolled over while the last log is at previous address
            prev_log_address, _ = calc_log_address(self._current_log_address, 1, -4)
            result, slots_empty_prev = await self.energy_log_update(
                prev_log_address, save_cache=False
            )
            if not result:
                _LOGGER.debug(
                    "async_energy_update | %s | Log rollover | energy_log_update from address %s failed",
                    self._mac_in_str,
                    prev_log_address,
                )
                return None

            if self._cache_enabled and (not slots_empty_cur or not slots_empty_prev):
                await self.save_cache()

        if (
            missing_addresses := self._energy_counters.log_addresses_missing
        ) is not None:
            if len(missing_addresses) == 0:
                await self.power_update()
                _LOGGER.debug(
                    "async_energy_update for %s | no missing log records",
                    self._mac_in_str,
                )
                return self._energy_counters.energy_statistics

            if len(missing_addresses) == 1:
                result, _ = await self.energy_log_update(
                    missing_addresses[0], save_cache=True
                )
                if result:
                    await self.power_update()
                    _LOGGER.debug(
                        "async_energy_update for %s | single energy log is missing | %s",
                        self._mac_in_str,
                        missing_addresses,
                    )
                    return self._energy_counters.energy_statistics

        # Create task to request remaining missing logs
        if (
            self._retrieve_energy_logs_task is None
            or self._retrieve_energy_logs_task.done()
        ):
            _LOGGER.debug(
                "Create task to update energy logs for node %s",
                self._mac_in_str,
            )
            self._retrieve_energy_logs_task = create_task(
                self.get_missing_energy_logs()
            )

        if (
            self._initialization_delay_expired is not None
            and datetime.now(tz=UTC) < self._initialization_delay_expired
        ):
            _LOGGER.info(
                "Unable to return energy statistics for %s during initialization, collecting required data...",
                self.name,
            )
        else:
            _LOGGER.warning(
                "Unable to return energy statistics for %s, collecting required data...",
                self.name,
            )
        return None

    async def _get_initial_energy_logs(self) -> None:
        """Collect initial energy logs for recent hours up to MAX_LOG_HOURS."""
        if self._current_log_address is None:
            return

        if self.energy_consumption_interval is None:
            return

        _LOGGER.debug(
            "Start collecting today's energy logs for node %s.",
            self._mac_in_str,
        )

        # When only consumption is measured, 1 address contains data from 4 hours
        # When both consumption and production are measured, 1 address contains data from 2 hours
        cons_only = self.energy_production_interval is None
        factor = 4 if cons_only else 2
        max_addresses_to_collect = MAX_LOG_HOURS // factor
        total_addresses = min(
            max_addresses_to_collect, ceil(datetime.now(tz=UTC).hour / factor) + 1
        )
        log_address = self._current_log_address
        any_updates = False
        while total_addresses > 0:
            result, slots_empty = await self.energy_log_update(
                log_address, save_cache=False
            )
            if result and slots_empty:
                # Stop initial log collection when an address contains no (None) or outdated data
                # Outdated data can indicate a EnergyLog address rollover: from address 6014 to 0
                _LOGGER.debug(
                    "All slots at log address %s are empty or outdated – stopping initial collection",
                    log_address,
                )
                break

            any_updates |= not slots_empty
            log_address, _ = calc_log_address(log_address, 1, -4)
            total_addresses -= 1

        if self._cache_enabled and any_updates:
            await self.save_cache()

    async def get_missing_energy_logs(self) -> None:
        """Task to retrieve missing energy logs."""
        self._energy_counters.update()
        if self._current_log_address is None:
            return

        if (missing_addresses := self._energy_counters.log_addresses_missing) is None:
            await self._get_initial_energy_logs()
            return

        _LOGGER.debug("Task created to get missing logs of %s", self._mac_in_str)
        _LOGGER.debug(
            "Task Request %s missing energy logs for node %s | %s",
            str(len(missing_addresses)),
            self._mac_in_str,
            str(missing_addresses),
        )
        missing_addresses = sorted(missing_addresses, reverse=True)
        tasks = [
            create_task(self.energy_log_update(address, save_cache=False))
            for address in missing_addresses
        ]
        any_updates = False
        for idx, task in enumerate(tasks):
            result, slots_empty = await task
            # When an energy log collection task returns False, stop and cancel the remaining tasks
            if not result:
                to_cancel = tasks[idx + 1 :]
                for t in to_cancel:
                    t.cancel()
                # Drain cancellations to avoid "Task exception was never retrieved"
                await gather(*to_cancel, return_exceptions=True)
                break

            any_updates |= not slots_empty

        if self._cache_enabled and any_updates:
            await self.save_cache()

    async def energy_log_update(
        self, address: int | None, save_cache: bool = True
    ) -> tuple[bool, bool]:
        """Request energy logs from node and store them.

        First bool: True when processing succeeded (records stored in memory, possibly all-empty);
        False only on transport or address errors.
        Second bool: slots_empty — True when all four slots at the address are empty or outdated;
        False when at least one recent, non-empty record was stored.
        """
        result = False
        slots_empty = True
        if address is None:
            return result, slots_empty

        _LOGGER.debug(
            "Requesting EnergyLogs from node %s address %s",
            self._mac_in_str,
            address,
        )
        request = CircleEnergyLogsRequest(self._send, self._mac_in_bytes, address)
        if (response := await request.send()) is None:
            _LOGGER.debug(
                "Retrieving EnergyLogs data from node %s failed",
                self._mac_in_str,
            )
            return result, slots_empty

        result = True
        _LOGGER.debug("EnergyLogs from node %s, address=%s:", self._mac_in_str, address)
        await self._available_update_state(True, response.timestamp)

        # Forward historical energy log information to energy counters
        # Each response message contains 4 log counters (slots) of the
        # energy pulses collected during the previous hour of given timestamp
        cache_updated = False
        for _slot in range(4, 0, -1):
            log_timestamp, log_pulses = response.log_data[_slot]
            _LOGGER.debug(
                "In slot=%s: pulses=%s, timestamp=%s", _slot, log_pulses, log_timestamp
            )
            address = response.log_address
            if log_timestamp is None or log_pulses is None:
                self._energy_counters.add_empty_log(address, _slot)
            else:
                log_timestamp = log_timestamp.replace(tzinfo=UTC)
                if self._check_timestamp_is_recent(address, _slot, log_timestamp):
                    self._energy_counters.add_pulse_log(
                        address,
                        _slot,
                        log_timestamp,
                        log_pulses,
                        import_only=True,
                    )
                cache_updated = True

        self._energy_counters.update()
        if cache_updated:
            slots_empty = False
            await self._energy_log_records_save_to_cache()
            if save_cache:
                _LOGGER.debug("Saving energy cache for %s", self._mac_in_str)
                await self.save_cache()
            return result, slots_empty

        return result, slots_empty

    def _check_timestamp_is_recent(
        self, address: int, slot: int, timestamp: datetime
    ) -> bool:
        """Check if a log record timestamp is within the last MAX_LOG_HOURS hours."""
        age_seconds = max(
            0.0, (datetime.now(tz=UTC) - timestamp.replace(tzinfo=UTC)).total_seconds()
        )
        if age_seconds > MAX_LOG_HOURS * 3600:
            _LOGGER.info(
                "EnergyLog from Node %s | address %s | slot %s | timestamp %s is outdated, ignoring...",
                self._mac_in_str,
                address,
                slot,
                timestamp,
            )
            return False
        return True

    async def _energy_log_records_load_from_cache(self) -> bool:
        """Load energy_log_record from cache."""
        if (cache_data := self._get_cache(CACHE_ENERGY_COLLECTION)) is None:
            _LOGGER.warning(
                "Failed to restore energy log records from cache for node %s", self.name
            )
            return False
        if cache_data == "":
            _LOGGER.debug("Cache-record is empty")
            return False

        collected_logs = _collect_records(cache_data)

        # Cutoff timestamp for filtering
        skip_before = datetime.now(tz=UTC) - timedelta(hours=MAX_LOG_HOURS)

        # Iterate in reverse sorted order directly
        for address in sorted(collected_logs, reverse=True):
            for slot in sorted(collected_logs[address].keys(), reverse=True):
                (timestamp, pulses) = collected_logs[address][slot]
                # Keep only recent entries; prune older-or-equal than cutoff
                if timestamp <= skip_before:
                    continue
                self._energy_counters.add_pulse_log(
                    address=address,
                    slot=slot,
                    pulses=pulses,
                    timestamp=timestamp,
                    import_only=True,
                )

        self._energy_counters.update()

        # Create task to retrieve remaining (missing) logs
        if self._energy_counters.log_addresses_missing is None:
            _LOGGER.debug("Cache | missing log addresses is None")
            return False

        if len(self._energy_counters.log_addresses_missing) > 0:
            if self._retrieve_energy_logs_task is not None:
                if not self._retrieve_energy_logs_task.done():
                    await self._retrieve_energy_logs_task
            self._retrieve_energy_logs_task = create_task(
                self.get_missing_energy_logs()
            )
            _LOGGER.debug("Cache | creating tasks to obtain missing energy logs")
            return False

        return True

    async def _energy_log_records_save_to_cache(self) -> None:
        """Update the in-memory energy log cache string (no file I/O)."""
        if not self._cache_enabled:
            return

        logs: dict[int, dict[int, PulseLogRecord]] = (
            self._energy_counters.get_pulse_logs()
        )
        # Efficiently serialize newest-first (logs is already sorted)
        records: list[str] = []
        for address, record in logs.items():
            for slot, log in record.items():
                ts = log.timestamp
                records.append(
                    f"{address}:{slot}:{ts.strftime('%Y-%m-%d-%H-%M-%S')}:{log.pulses}"
                )
        cached_logs = "|".join(records)
        _LOGGER.debug("Updating in-memory energy log records for %s", self._mac_in_str)
        self._set_cache(CACHE_ENERGY_COLLECTION, cached_logs)

    @raise_not_loaded
    async def set_relay(self, state: bool) -> bool:
        """Change the state of the relay."""
        if NodeFeature.RELAY not in self._features:
            raise FeatureError(
                f"Changing state of relay is not supported for node {self.mac}"
            )

        if self._relay_lock.state:
            _LOGGER.debug("Relay switch blocked, relay is locked")
            return self._relay_state.state

        _LOGGER.debug("Switching relay to %s", state)
        request = CircleRelaySwitchRequest(self._send, self._mac_in_bytes, state)
        response = await request.send()

        if response is None or response.ack_id == NodeResponseType.RELAY_SWITCH_FAILED:
            raise NodeError(f"Request to switch relay for {self.name} failed")

        if response.ack_id == NodeResponseType.RELAY_SWITCHED_OFF:
            await self._relay_update_state(state=False, timestamp=response.timestamp)
            return False
        if response.ack_id == NodeResponseType.RELAY_SWITCHED_ON:
            await self._relay_update_state(state=True, timestamp=response.timestamp)
            return True

        raise NodeError(
            f"Unexpected NodeResponseType {response.ack_id!r} received "
            + "in response to CircleRelaySwitchRequest for node {self.mac}"
        )

    @raise_not_loaded
    async def set_relay_lock(self, state: bool) -> bool:
        """Set the state of the relay-lock."""
        await self._relay_update_lock(state)
        return state

    async def _relay_load_from_cache(self) -> bool:
        """Load relay state from cache."""
        result = True
        if (cached_relay_data := self._get_cache(CACHE_RELAY)) is not None:
            _LOGGER.debug(
                "Restore relay state from cache for node %s: relay: %s",
                self._mac_in_str,
                cached_relay_data,
            )
            relay_state = cached_relay_data == "True"
            await self._relay_update_state(relay_state)
        else:
            _LOGGER.debug(
                "Failed to restore relay state from cache for node %s, try to request node info...",
                self._mac_in_str,
            )
            if await self.node_info_update() is None:
                result = False

        if (cached_relay_lock := self._get_cache(CACHE_RELAY_LOCK)) is not None:
            _LOGGER.debug(
                "Restore relay_lock state from cache for node %s: relay_lock: %s",
                self._mac_in_str,
                cached_relay_lock,
            )
            relay_lock = cached_relay_lock == "True"
            await self._relay_update_lock(relay_lock, load_from_cache=True)
        else:
            # Set to initial state False when not present in cache
            await self._relay_update_lock(False)

        return result

    async def _relay_update_state(
        self, state: bool, timestamp: datetime | None = None
    ) -> None:
        """Process relay state update."""
        state_update = False
        if state:
            self._set_cache(CACHE_RELAY, "True")
            if self._relay_state.state is None or not self._relay_state.state:
                state_update = True
        else:
            self._set_cache(CACHE_RELAY, "False")
            if self._relay_state.state is None or self._relay_state.state:
                state_update = True

        self._relay_state = replace(self._relay_state, state=state, timestamp=timestamp)
        if state_update:
            await self.publish_feature_update_to_subscribers(
                NodeFeature.RELAY, self._relay_state
            )
            _LOGGER.debug("Saving relay state update to cache for %s", self._mac_in_str)
            await self.save_cache()

    async def _relay_update_lock(
        self, state: bool, load_from_cache: bool = False
    ) -> None:
        """Process relay lock update."""
        state_update = False
        if state:
            self._set_cache(CACHE_RELAY_LOCK, "True")
            if self._relay_lock.state is None or not self._relay_lock.state:
                state_update = True
        else:
            self._set_cache(CACHE_RELAY_LOCK, "False")
            if self._relay_lock.state is None or self._relay_lock.state:
                state_update = True

        if state_update:
            self._relay_lock = replace(self._relay_lock, state=state)
            await self.publish_feature_update_to_subscribers(
                NodeFeature.RELAY_LOCK, self._relay_lock
            )
            if not load_from_cache:
                _LOGGER.debug(
                    "Saving relay lock state update to cache for %s", self._mac_in_str
                )
                await self.save_cache()

    async def _clock_synchronize_scheduler(self) -> None:
        """Background task: periodically synchronize the clock until cancelled."""
        try:
            while True:
                await sleep(CLOCK_SYNC_PERIOD + (random.uniform(-5, 5)))
                try:
                    await self.clock_synchronize()
                except Exception:
                    _LOGGER.exception(
                        "Clock synchronization failed for %s", self._mac_in_str
                    )
        except CancelledError:
            _LOGGER.debug("Clock sync scheduler cancelled for %s", self._mac_in_str)
            raise

    async def clock_synchronize(self) -> bool:
        """Synchronize clock. Returns true if successful."""
        request = CircleClockGetRequest(self._send, self._mac_in_bytes)
        response = await request.send()
        if response is None or response.timestamp is None:
            return False

        dt_now = datetime.now(tz=UTC)
        days_diff = (response.day_of_week.value - dt_now.weekday()) % 7
        circle_timestamp: datetime = dt_now.replace(
            day=dt_now.day + days_diff,
            hour=response.time.value.hour,
            minute=response.time.value.minute,
            second=response.time.value.second,
            microsecond=0,
            tzinfo=UTC,
        )
        clock_offset = response.timestamp.replace(microsecond=0) - circle_timestamp
        if abs(clock_offset.total_seconds()) < MAX_TIME_DRIFT:
            return True

        _LOGGER.info(
            "Sync clock of node %s because time drifted %s seconds",
            self._mac_in_str,
            int(abs(clock_offset.total_seconds())),
        )
        if self._node_protocols is None:
            raise NodeError(
                "Unable to synchronize clock when protocol version is unknown"
            )

        set_request = CircleClockSetRequest(
            self._send,
            self._mac_in_bytes,
            datetime.now(tz=UTC),
            self._node_protocols.max,
        )
        if (node_response := await set_request.send()) is not None:
            return node_response.ack_id == NodeResponseType.CLOCK_ACCEPTED
        _LOGGER.warning("Failed to sync the clock of %s", self.name)
        return False

    async def load(self) -> None:
        """Load and activate Circle node features."""
        if self._loaded:
            return

        if self._cache_enabled:
            _LOGGER.debug("Loading Circle node %s from cache", self._mac_in_str)
            if await self._load_from_cache():
                self._loaded = True
        if not self._loaded:
            _LOGGER.debug("Retrieving Info For Circle node %s", self._mac_in_str)

            # Check if node is online
            if (
                not self._available
                and not await self.is_online()
                or await self.node_info_update() is None
            ):
                _LOGGER.debug(
                    "Failed to retrieve NodeInfo for %s, loading defaults",
                    self._mac_in_str,
                )
                await self._load_defaults()

        self._loaded = True

        self._setup_protocol(CIRCLE_FIRMWARE_SUPPORT, CIRCLE_FEATURES)
        await self._loaded_callback(NodeEvent.LOADED, self.mac)
        await self.initialize()

    async def _load_from_cache(self) -> bool:
        """Load states from previous cached information. Returns True if successful."""
        result = True
        if not await super()._load_from_cache():
            _LOGGER.debug("_load_from_cache | super-load failed")
            result = False

        # Calibration settings
        if not await self._calibration_load_from_cache():
            _LOGGER.debug(
                "Node %s failed to load calibration from cache", self._mac_in_str
            )
            if result:
                result = False

        # Energy collection
        if not await self._energy_log_records_load_from_cache():
            _LOGGER.debug(
                "Node %s failed to load energy_log_records from cache",
                self._mac_in_str,
            )
            if result:
                result = False

        # Relay
        if not await self._relay_load_from_cache():
            _LOGGER.debug(
                "Node %s failed to load relay state from cache",
                self._mac_in_str,
            )
            if result:
                result = False

        # Relay init config if feature is enabled
        if NodeFeature.RELAY_INIT in self._features:
            if not await self._relay_init_load_from_cache():
                _LOGGER.debug(
                    "Node %s failed to load relay_init state from cache",
                    self._mac_in_str,
                )
            if result:
                result = False

        return result

    async def _load_defaults(self) -> None:
        """Load default configuration settings."""
        if self._node_info.model is None:
            self._node_info.model = "Circle"
        if self._node_info.name is None:
            self._node_info.name = f"Circle {self._node_info.mac[-5:]}"
        if self._node_info.firmware is None:
            self._node_info.firmware = DEFAULT_FIRMWARE

    @raise_not_loaded
    async def initialize(self) -> bool:
        """Initialize node."""
        if self._initialized:
            _LOGGER.debug("Already initialized node %s", self._mac_in_str)
            return True

        if not await self.clock_synchronize():
            _LOGGER.debug(
                "Failed to initialized node %s, failed clock sync", self._mac_in_str
            )
            self._initialized = False
            return False

        if not self._calibration and not await self.calibration_update():
            _LOGGER.debug(
                "Failed to initialized node %s, no calibration", self._mac_in_str
            )
            self._initialized = False
            return False

        if (
            self.skip_update(self._node_info, 30)
            and await self.node_info_update() is None
        ):
            _LOGGER.debug("Failed to retrieve node info for %s", self._mac_in_str)
        if NodeFeature.RELAY_INIT in self._features:
            if (state := await self._relay_init_get()) is not None:
                self._relay_config = replace(self._relay_config, init_state=state)
            else:
                _LOGGER.debug(
                    "Failed to initialized node %s, relay init", self._mac_in_str
                )
                self._initialized = False
                return False

        await super().initialize()
        if self._clock_synchronize_task is None or self._clock_synchronize_task.done():
            self._clock_synchronize_task = create_task(
                self._clock_synchronize_scheduler()
            )
        return True

    async def node_info_update(
        self, node_info: NodeInfoResponse | NodeInfoMessage | None = None
    ) -> NodeInfo | None:
        """Update Node (hardware) information."""
        if node_info is None:
            if self.skip_update(self._node_info, 30):
                return self._node_info

            node_request = NodeInfoRequest(self._send, self._mac_in_bytes)
            node_info = await node_request.send()

        if node_info is None:
            return None

        await super().node_info_update(node_info)

        return self._node_info

    # pylint: disable=too-many-arguments
    async def update_node_details(
        self, node_info: NodeInfoResponse | NodeInfoMessage | None = None
    ) -> bool:
        """Process new node info and return true if all fields are updated."""
        if node_info is None:
            return False

        if node_info.relay_state is not None:
            await self._relay_update_state(
                node_info.relay_state, timestamp=node_info.timestamp
            )

        if (
            node_info.current_logaddress_pointer is not None
            and self._current_log_address is not None
            and self._current_log_address > node_info.current_logaddress_pointer
        ):
            # Rollover of log address
            _LOGGER.debug(
                "Rollover log address from %s to %s for node %s",
                self._current_log_address,
                node_info.current_logaddress_pointer,
                self._mac_in_str,
            )

        if (
            node_info.current_logaddress_pointer is not None
            and node_info.current_logaddress_pointer != self._current_log_address
        ):
            self._current_log_address = node_info.current_logaddress_pointer

        return await super().update_node_details(node_info)

    async def unload(self) -> None:
        """Deactivate and unload node features."""
        self._loaded = False
        if (
            self._retrieve_energy_logs_task is not None
            and not self._retrieve_energy_logs_task.done()
        ):
            self._retrieve_energy_logs_task.cancel()

        if self._cache_enabled:
            await self._energy_log_records_save_to_cache()

        if self._clock_synchronize_task:
            self._clock_synchronize_task.cancel()
            await gather(self._clock_synchronize_task, return_exceptions=True)
            self._clock_synchronize_task = None

        await super().unload()

    @raise_not_loaded
    async def set_relay_init(self, state: bool) -> bool:
        """Change the initial power-on state of the relay."""
        if NodeFeature.RELAY_INIT not in self._features:
            raise FeatureError(
                f"Configuration of initial power-up relay state is not supported for node {self.mac}"
            )
        await self._relay_init_set(state)
        if self._relay_config.init_state is None:
            raise NodeError("Failed to configure relay init setting")
        return self._relay_config.init_state

    async def _relay_init_get(self) -> bool | None:
        """Get current configuration of the power-up state of the relay. Returns None if retrieval failed."""
        if NodeFeature.RELAY_INIT not in self._features:
            raise NodeError(
                "Retrieval of initial state of relay is not "
                + f"supported for device {self.name}"
            )
        request = CircleRelayInitStateRequest(
            self._send, self._mac_in_bytes, False, False
        )
        if (response := await request.send()) is not None:
            await self._relay_init_update_state(response.relay.value == 1)
            return self._relay_config.init_state
        return None

    async def _relay_init_set(self, state: bool) -> bool | None:
        """Configure relay init state."""
        if NodeFeature.RELAY_INIT not in self._features:
            raise NodeError(
                "Configuring of initial state of relay is not"
                + f"supported for device {self.name}"
            )
        request = CircleRelayInitStateRequest(
            self._send, self._mac_in_bytes, True, state
        )
        if (response := await request.send()) is not None:
            await self._relay_init_update_state(response.relay.value == 1)
            return self._relay_config.init_state
        return None

    async def _relay_init_load_from_cache(self) -> bool:
        """Load relay init state from cache. Returns True if retrieval was successful."""
        if (cached_relay_data := self._get_cache(CACHE_RELAY_INIT)) is not None:
            relay_init_state = False
            if cached_relay_data == "True":
                relay_init_state = True
            await self._relay_init_update_state(relay_init_state)
            return True
        return False

    async def _relay_init_update_state(self, state: bool) -> None:
        """Process relay init state update."""
        state_update = False
        if state:
            self._set_cache(CACHE_RELAY_INIT, "True")
            if (
                self._relay_config.init_state is None
                or not self._relay_config.init_state
            ):
                state_update = True
        if not state:
            self._set_cache(CACHE_RELAY_INIT, "False")
            if self._relay_config.init_state is None or self._relay_config.init_state:
                state_update = True
        if state_update:
            self._relay_config = replace(self._relay_config, init_state=state)
            await self.publish_feature_update_to_subscribers(
                NodeFeature.RELAY_INIT, self._relay_config
            )
            _LOGGER.debug(
                "Saving relay_init state update to cache for %s", self._mac_in_str
            )
            await self.save_cache()

    @raise_calibration_missing
    def _calc_watts(self, pulses: int, seconds: int, nano_offset: int) -> float | None:
        """Calculate watts based on energy usages."""
        if self._calibration is None:
            return None

        pulses_per_s = self._correct_power_pulses(pulses, nano_offset) / float(seconds)
        negative = False
        if pulses_per_s < 0:
            negative = True
            pulses_per_s = abs(pulses_per_s)

        corrected_pulses = seconds * (
            (
                (
                    ((pulses_per_s + self._calibration.off_noise) ** 2)
                    * self._calibration.gain_b
                )
                + (
                    (pulses_per_s + self._calibration.off_noise)
                    * self._calibration.gain_a
                )
            )
            + self._calibration.off_tot
        )
        if negative:
            corrected_pulses = -corrected_pulses

        return corrected_pulses / PULSES_PER_KW_SECOND / seconds * (1000)

    def _correct_power_pulses(self, pulses: int, offset: int) -> float:
        """Correct pulses based on given measurement time offset (ns)."""
        # Sometimes the circle returns -1 for some of the pulse counters
        # likely this means the circle measures very little power and is
        # suffering from rounding errors. Zero these out. However, negative
        # pulse values are valid for power producing appliances, like
        # solar panels, so don't complain too loudly.
        if pulses == -1:
            _LOGGER.debug(
                "Power pulse counter for node %s of value of -1, corrected to 0",
                self._mac_in_str,
            )
            return 0.0
        if pulses != 0:
            if offset != 0:
                return (
                    pulses * (SECOND_IN_NANOSECONDS + offset)
                ) / SECOND_IN_NANOSECONDS
            return pulses
        return 0.0

    @raise_not_loaded
    async def get_state(self, features: tuple[NodeFeature]) -> dict[NodeFeature, Any]:  # noqa: PLR0912
        """Update latest state for given feature."""
        states: dict[NodeFeature, Any] = {}
        if not self._available and not await self.is_online():
            _LOGGER.debug(
                "Node %s did not respond, unable to update state", self._mac_in_str
            )
            for feature in features:
                states[feature] = None
            states[NodeFeature.AVAILABLE] = self.available_state
            return states

        for feature in features:
            if feature not in self._features:
                raise NodeError(
                    f"Update of feature '{feature}' is not supported for {self.name}"
                )

        features, states = await self._check_for_energy_and_power_features(
            features, states
        )

        for feature in features:
            match feature:
                case NodeFeature.ENERGY:
                    states[feature] = await self.energy_update()
                    _LOGGER.debug(
                        "async_get_state %s - energy: %s",
                        self._mac_in_str,
                        states[feature],
                    )
                case NodeFeature.RELAY:
                    states[feature] = self._relay_state
                    _LOGGER.debug(
                        "async_get_state %s - relay: %s",
                        self._mac_in_str,
                        states[feature],
                    )
                case NodeFeature.RELAY_LOCK:
                    states[feature] = self._relay_lock
                case NodeFeature.RELAY_INIT:
                    states[feature] = self._relay_config
                case NodeFeature.POWER:
                    states[feature] = await self.power_update()
                    _LOGGER.debug(
                        "async_get_state %s - power: %s",
                        self._mac_in_str,
                        states[feature],
                    )
                case _:
                    state_result = await super().get_state((feature,))
                    if feature in state_result:
                        states[feature] = state_result[feature]

        if NodeFeature.AVAILABLE not in states:
            states[NodeFeature.AVAILABLE] = self.available_state

        return states

    async def _check_for_energy_and_power_features(
        self, features: tuple[NodeFeature], states: dict[NodeFeature, Any]
    ) -> tuple[tuple[NodeFeature], dict[NodeFeature, Any]]:
        """Check for presence of both NodeFeature.ENERGY and NodeFeature.POWER.

        If both are present, execute the related functions in a specific order
        to assure a proper response to the hourly pulses-rollovers.
        """
        if {NodeFeature.ENERGY, NodeFeature.POWER} <= set(features):
            states[NodeFeature.POWER] = await self.power_update()
            _LOGGER.debug(
                "async_get_state %s - power: %s",
                self._mac_in_str,
                states[NodeFeature.POWER],
            )
            states[NodeFeature.ENERGY] = await self.energy_update()
            _LOGGER.debug(
                "async_get_state %s - energy: %s",
                self._mac_in_str,
                states[NodeFeature.ENERGY],
            )
            return tuple(
                set(features).difference({NodeFeature.ENERGY, NodeFeature.POWER})
            ), states

        return features, states

    async def energy_reset_request(self) -> None:
        """Send an energy-reset to a Node."""
        if self._node_protocols is None:
            raise NodeError("Unable to energy-reset when protocol version is unknown")

        request = CircleClockSetRequest(
            self._send,
            self._mac_in_bytes,
            datetime.now(tz=UTC),
            self._node_protocols.max,
            True,
        )
        if (response := await request.send()) is None:
            raise NodeError(f"Energy-reset for {self._mac_in_str} failed")

        if response.ack_id != NodeResponseType.CLOCK_ACCEPTED:
            raise MessageError(
                f"Unexpected NodeResponseType {response.ack_id!r} received as response to CircleClockSetRequest"
            )

        _LOGGER.info("Energy reset for Node %s successful", self._mac_in_str)

        # Follow up by an energy-intervals (re)set
        interval_request = CircleMeasureIntervalRequest(
            self._send,
            self._mac_in_bytes,
            DEFAULT_CONS_INTERVAL,
            NO_PRODUCTION_INTERVAL,
        )
        if (interval_response := await interval_request.send()) is None:
            raise NodeError("No response for CircleMeasureIntervalRequest")

        if (
            interval_response.response_type
            != NodeResponseType.POWER_LOG_INTERVAL_ACCEPTED
        ):
            raise MessageError(
                f"Unknown NodeResponseType '{interval_response.response_type.name}' received"
            )
        _LOGGER.info("Resetting energy intervals to default (= consumption only)")

        # Clear the cached energy_collection
        if self._cache_enabled:
            self._set_cache(CACHE_ENERGY_COLLECTION, "")
            _LOGGER.info(
                "Energy-collection cache cleared successfully, updating cache for %s",
                self._mac_in_str,
            )
            await self.save_cache()

        # Clear PulseCollection._logs
        self._energy_counters.reset_pulse_collection()
        _LOGGER.info("Resetting pulse-collection")

        # Request a NodeInfo update
        if await self.node_info_update() is None:
            _LOGGER.warning(
                "Node info update failed after energy-reset for %s",
                self._mac_in_str,
            )
        else:
            _LOGGER.info(
                "Node info update after energy-reset successful for %s",
                self._mac_in_str,
            )
