from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, UTC
import logging
from typing import Final

from ...constants import MINUTE_IN_SECONDS, WEEK_IN_HOURS

_LOGGER = logging.getLogger(__name__)
CONSUMED: Final = True
PRODUCED: Final = False

MAX_LOG_HOURS = WEEK_IN_HOURS


def calc_log_address(address: int, slot: int, offset: int) -> tuple[int, int]:
    """Calculate address and slot for log based for specified offset"""

    # FIXME: Handle max address (max is currently unknown) to guard
    # against address rollovers
    if offset < 0:
        while offset + slot < 1:
            address -= 1
            offset += 4
    if offset > 0:
        while offset + slot > 4:
            address += 1
            offset -= 4
    return (address, slot + offset)


@dataclass
class PulseLogRecord:
    """Total pulses collected at specific timestamp."""

    timestamp: datetime
    pulses: int
    is_consumption: bool


class PulseCollection:
    """
    Class to store consumed and produced energy pulses of
    the current interval and past (history log) intervals.
    """

    def __init__(self, mac: str) -> None:
        """Initialize PulseCollection class."""
        self._mac = mac
        self._log_interval_consumption: int | None = None
        self._log_interval_production: int | None = None

        self._last_log_address: int | None = None
        self._last_log_slot: int | None = None
        self._last_log_timestamp: datetime | None = None
        self._first_log_address: int | None = None
        self._first_log_slot: int | None = None
        self._first_log_timestamp: datetime | None = None

        self._last_log_consumption_timestamp: datetime | None = None
        self._last_log_consumption_address: int | None = None
        self._last_log_consumption_slot: int | None = None
        self._first_log_consumption_timestamp: datetime | None = None
        self._first_log_consumption_address: int | None = None
        self._first_log_consumption_slot: int | None = None
        self._next_log_consumption_timestamp: datetime | None = None

        self._last_log_production_timestamp: datetime | None = None
        self._last_log_production_address: int | None = None
        self._last_log_production_slot: int | None = None
        self._first_log_production_timestamp: datetime | None = None
        self._first_log_production_address: int | None = None
        self._first_log_production_slot: int | None = None
        self._next_log_production_timestamp: datetime | None = None

        self._rollover_log_consumption = False
        self._rollover_log_production = False
        self._rollover_pulses_consumption = False
        self._rollover_pulses_production = False

        self._logs: dict[int, dict[int, PulseLogRecord]] | None = None
        self._log_addresses_missing: list[int] | None = None
        self._log_production: bool | None = None
        self._pulses_consumption: int | None = None
        self._pulses_production: int | None = None
        self._last_update: datetime | None = None

    @property
    def collected_logs(self) -> int:
        """Total collected logs"""
        counter = 0
        if self._logs is None:
            return counter
        for address in self._logs:
            counter += len(self._logs[address])
        return counter

    @property
    def logs(self) -> dict[int, dict[int, PulseLogRecord]]:
        """Return currently collected pulse logs in reversed order"""
        if self._logs is None:
            return {}
        sorted_log: dict[int, dict[int, PulseLogRecord]] = {}
        skip_before = datetime.now(UTC) - timedelta(hours=MAX_LOG_HOURS)
        sorted_addresses = sorted(self._logs.keys(), reverse=True)
        for address in sorted_addresses:
            sorted_slots = sorted(self._logs[address].keys(), reverse=True)
            for slot in sorted_slots:
                if self._logs[address][slot].timestamp > skip_before:
                    if sorted_log.get(address) is None:
                        sorted_log[address] = {}
                    sorted_log[address][slot] = self._logs[address][slot]
        return sorted_log

    @property
    def last_log(self) -> tuple[int, int] | None:
        """Return address and slot of last imported log"""
        return (self._last_log_consumption_address, self._last_log_consumption_slot)

    @property
    def production_logging(self) -> bool | None:
        """Indicate if production logging is active"""
        return self._log_production

    @property
    def log_interval_consumption(self) -> int | None:
        """Interval in minutes between last consumption pulse logs."""
        return self._log_interval_consumption

    @property
    def log_interval_production(self) -> int | None:
        """Interval in minutes between last production pulse logs."""
        return self._log_interval_production

    @property
    def log_rollover(self) -> bool:
        """Indicate if new log is required"""
        return (
            self._rollover_log_consumption or self._rollover_log_production
        )

    @property
    def last_update(self) -> datetime | None:
        """Return timestamp of last update."""
        return self._last_update

    def collected_pulses(
        self, from_timestamp: datetime, is_consumption: bool
    ) -> tuple[int | None, datetime | None]:
        """Calculate total pulses from given timestamp"""

        # _LOGGER.debug("collected_pulses | %s | is_cons=%s, from=%s", self._mac, is_consumption, from_timestamp)

        if not is_consumption:
            if self._log_production is None or not self._log_production:
                return (None, None)

        if is_consumption and (
            self._rollover_log_consumption or self._rollover_pulses_consumption
        ):
            _LOGGER.debug("collected_pulses | %s | is consumption: self._rollover_log_consumption=%s, self._rollover_pulses_consumption=%s", self._mac, self._rollover_log_consumption, self._rollover_pulses_consumption)
            return (None, None)
        if not is_consumption and (
            self._rollover_log_production or self._rollover_pulses_production
        ):
            _LOGGER.debug("collected_pulses | %s | NOT is consumption: self._rollover_log_consumption=%s, self._rollover_pulses_consumption=%s", self._mac, self._rollover_log_consumption, self._rollover_pulses_consumption)
            return (None, None)

        log_pulses = self._collect_pulses_from_logs(
            from_timestamp, is_consumption
        )
        if log_pulses is None:
            _LOGGER.debug("collected_pulses | %s | log_pulses:None", self._mac)
            return (None, None)
        # _LOGGER.debug("collected_pulses | %s | log_pulses=%s", self._mac, log_pulses)

        pulses: int | None = None
        timestamp: datetime | None = None
        if is_consumption and self._pulses_consumption is not None:
            pulses = self._pulses_consumption
            timestamp = self._last_update
        if not is_consumption and self._pulses_production is not None:
            pulses = self._pulses_production
            timestamp = self._last_update
        # _LOGGER.debug("collected_pulses | %s | pulses=%s", self._mac, pulses)

        if pulses is None:
            _LOGGER.debug("collected_pulses | %s | is_consumption=%s, pulses=None", self._mac, is_consumption)
            return (None, None)
        return (pulses + log_pulses, timestamp)

    def _collect_pulses_from_logs(
        self, from_timestamp: datetime, is_consumption: bool
    ) -> int | None:
        """Collect all pulses from logs"""
        if self._logs is None:
            _LOGGER.debug("_collect_pulses_from_logs | %s | self._logs=None", self._mac)
            return None
        if is_consumption:
            if self._last_log_consumption_timestamp is None:
                _LOGGER.debug("_collect_pulses_from_logs | %s | self._last_log_consumption_timestamp=None", self._mac)
                return None
            if from_timestamp > self._last_log_consumption_timestamp:
                return 0
        else:
            if self._last_log_production_timestamp is None:
                _LOGGER.debug("_collect_pulses_from_logs | %s | self._last_log_production_timestamp=None", self._mac)
                return None
            if from_timestamp > self._last_log_production_timestamp:
                return 0
        missing_logs = self._logs_missing(from_timestamp)
        if missing_logs is None or missing_logs:
            _LOGGER.debug("_collect_pulses_from_logs | %s | missing_logs=%s", self._mac, missing_logs)
            return None

        log_pulses = 0

        for log_item in self._logs.values():
            for slot_item in log_item.values():
                if (
                    slot_item.is_consumption == is_consumption
                    and slot_item.timestamp >= from_timestamp
                ):
                    log_pulses += slot_item.pulses
        return log_pulses

    def update_pulse_counter(
        self, pulses_consumed: int, pulses_produced: int, timestamp: datetime
    ) -> None:
        """Update pulse counter"""
        if self._pulses_consumption is None:
            self._pulses_consumption = pulses_consumed
        if self._pulses_production is None:
            self._pulses_production = pulses_produced
        self._last_update = timestamp

        if self._next_log_consumption_timestamp is None:
            self._pulses_consumption = pulses_consumed
            self._pulses_production = pulses_produced
            return
        if (
            self._log_production
            and self._next_log_production_timestamp is None
        ):
            return

        if (
            self._log_addresses_missing is None or
            len(self._log_addresses_missing) > 0
        ):
            return

        # Rollover of logs first
        if (
            self._rollover_log_consumption
            and pulses_consumed <= self._pulses_consumption
        ):
            self._rollover_log_consumption = False
        if (
            self._log_production
            and self._rollover_log_production
            and self._pulses_production >= pulses_produced
        ):
            self._rollover_log_production = False

        # Rollover of pulses first
        if pulses_consumed < self._pulses_consumption:
            _LOGGER.debug("update_pulse_counter | %s | pulses_consumed=%s, _pulses_consumption=%s", self._mac, pulses_consumed, self._pulses_consumption)
            self._rollover_pulses_consumption = True
        else:
            if self._log_interval_consumption is not None and timestamp > (
                self._next_log_consumption_timestamp
                + timedelta(minutes=self._log_interval_consumption)
            ):
                _LOGGER.debug("update_pulse_counter | %s | _log_interval_consumption=%s, timestamp=%s, _next_log_consumption_timestamp=%s", self._mac, self._log_interval_consumption, timestamp, self._next_log_consumption_timestamp) 
                self._rollover_pulses_consumption = True

        if self._log_production:
            if self._pulses_production < pulses_produced:
                self._rollover_pulses_production = True
            else:
                if (
                    self._next_log_production_timestamp is not None
                    and self._log_interval_production is not None
                    and timestamp
                    > (
                        self._next_log_production_timestamp
                        + timedelta(minutes=self._log_interval_production)
                    )
                ):
                    self._rollover_pulses_production = True

        self._pulses_consumption = pulses_consumed
        self._pulses_production = pulses_produced

    def add_log(
        self,
        address: int,
        slot: int,
        timestamp: datetime,
        pulses: int,
        import_only: bool = False
    ) -> bool:
        """Store pulse log."""
        log_record = PulseLogRecord(timestamp, pulses, CONSUMED)
        if not self._add_log_record(address, slot, log_record):
            return False
        self._update_log_direction(address, slot, timestamp)
        self._update_log_interval()
        self._update_log_references(address, slot)
        self._update_log_rollover(address, slot)
        if not import_only:
            self.recalculate_missing_log_addresses()
        return True

    def recalculate_missing_log_addresses(self) -> None:
        """Recalculate missing log addresses"""
        self._log_addresses_missing = self._logs_missing(
            datetime.now(UTC) - timedelta(
                hours=MAX_LOG_HOURS
            )
        )

    def _add_log_record(
        self, address: int, slot: int, log_record: PulseLogRecord
    ) -> bool:
        """Add log record and return True if log did not exists."""
        if self._logs is None:
            self._logs = {address: {slot: log_record}}
            return True
        if self._log_exists(address, slot):
            return False
        # Drop unused log records
        if log_record.timestamp < (
            datetime.now(UTC) - timedelta(hours=MAX_LOG_HOURS)
        ):
            return False
        if self._logs.get(address) is None:
            self._logs[address] = {slot: log_record}
        else:
            self._logs[address][slot] = log_record
        return True

    def _update_log_direction(
        self, address: int, slot: int, timestamp: datetime
    ) -> None:
        """
        Update Energy direction of log record.
        Two subsequential logs with the same timestamp indicates the first
        is consumption and second production.
        """
        if self._logs is None:
            return

        prev_address, prev_slot = calc_log_address(address, slot, -1)
        if self._log_exists(prev_address, prev_slot):
            if self._logs[prev_address][prev_slot].timestamp == timestamp:
                # Given log is the second log with same timestamp,
                # mark direction as production
                self._logs[address][slot].is_consumption = False
                self._log_production = True

        next_address, next_slot = calc_log_address(address, slot, 1)
        if self._log_exists(next_address, next_slot):
            if self._logs[next_address][next_slot].timestamp == timestamp:
                # Given log the first log with same timestamp,
                # mark direction as production of next log
                self._logs[next_address][next_slot].is_consumption = False
                self._log_production = True
            else:
                if self._log_production is None:
                    self._log_production = False

    def _update_log_rollover(self, address: int, slot: int) -> None:
        if self._last_update is None:
            return
        if self._logs is None:
            return
        if (
            self._next_log_consumption_timestamp is not None
            and self._rollover_pulses_consumption
            and self._next_log_consumption_timestamp > self._last_update
        ):
            self._rollover_pulses_consumption = False

        if (
            self._next_log_production_timestamp is not None
            and self._rollover_pulses_production
            and self._next_log_production_timestamp > self._last_update
        ):
            self._rollover_pulses_production = False

        if self._logs[address][slot].timestamp > self._last_update:
            if self._logs[address][slot].is_consumption:
                self._rollover_log_consumption = True
            else:
                self._rollover_log_production = True

    def _update_log_interval(self) -> None:
        """
        Update the detected log interval based on
        the most recent two logs.
        """
        if self._logs is None or self._log_production is None:
            _LOGGER.debug("_update_log_interval | %s | _logs=%s, _log_production=%s", self._mac, self._logs, self._log_production)
            return
        last_address, last_slot = self._last_log_reference()
        if last_address is None or last_slot is None:
            return

        last_timestamp = self._logs[last_address][last_slot].timestamp
        last_direction = self._logs[last_address][last_slot].is_consumption
        address1, slot1 = calc_log_address(last_address, last_slot, -1)
        while self._log_exists(address1, slot1):
            if last_direction == self._logs[address1][slot1].is_consumption:
                delta1: timedelta = (
                    last_timestamp - self._logs[address1][slot1].timestamp
                )
                if last_direction:
                    self._log_interval_consumption = int(
                        delta1.total_seconds() / MINUTE_IN_SECONDS
                    )
                else:
                    self._log_interval_production = int(
                        delta1.total_seconds() / MINUTE_IN_SECONDS
                    )
                break
            if not self._log_production:
                return
            address1, slot1 = calc_log_address(address1, slot1, -1)

        # update interval of other direction too
        address2, slot2 = self._last_log_reference(not last_direction)
        if address2 is None or slot2 is None:
            return
        timestamp = self._logs[address2][slot2].timestamp
        address3, slot3 = calc_log_address(address2, slot2, -1)
        while self._log_exists(address3, slot3):
            if last_direction != self._logs[address3][slot3].is_consumption:
                delta2: timedelta = (
                    timestamp - self._logs[address3][slot3].timestamp
                )
                if last_direction:
                    self._log_interval_production = int(
                        delta2.total_seconds() / MINUTE_IN_SECONDS
                    )
                else:
                    self._log_interval_consumption = int(
                        delta2.total_seconds() / MINUTE_IN_SECONDS
                    )
                break
            address3, slot3 = calc_log_address(address3, slot3, -1)

    def _log_exists(self, address: int, slot: int) -> bool:
        if self._logs is None:
            return False
        if self._logs.get(address) is None:
            return False
        if self._logs[address].get(slot) is not None:
            return True
        return False

    def _update_last_log_reference(
        self, address: int, slot: int, timestamp
    ) -> None:
        """Update references to last (most recent) log record"""
        if (
            self._last_log_timestamp is None or
            self._last_log_timestamp < timestamp
        ):
            self._last_log_address = address
            self._last_log_slot = slot
            self._last_log_timestamp = timestamp

    def _update_last_consumption_log_reference(
            self, address: int, slot: int, timestamp
    ) -> None:
        """Update references to last (most recent) log consumption record."""
        if (
            self._last_log_consumption_timestamp is None or
            self._last_log_consumption_timestamp < timestamp
        ):
            self._last_log_consumption_timestamp = timestamp
            self._last_log_consumption_address = address
            self._last_log_consumption_slot = slot
            if self._log_interval_consumption is not None:
                self._next_log_consumption_timestamp = (
                    timestamp + timedelta(
                        minutes=self.log_interval_consumption
                    )
                )

    def _update_last_production_log_reference(
        self, address: int, slot: int, timestamp
    ) -> None:
        """Update references to last (most recent) log production record"""
        if (
            self._last_log_production_timestamp is None or
            self._last_log_production_timestamp < timestamp
        ):
            self._last_log_production_timestamp = timestamp
            self._last_log_production_address = address
            self._last_log_production_slot = slot
            if self._log_interval_production is not None:
                self._next_log_production_timestamp = (
                    timestamp + timedelta(minutes=self.log_interval_production)
                )

    def _update_first_log_reference(
        self, address: int, slot: int, timestamp
    ) -> None:
        """Update references to first (oldest) log record"""
        if (
            self._first_log_timestamp is None or
            self._first_log_timestamp > timestamp
        ):
            self._first_log_address = address
            self._first_log_slot = slot
            self._first_log_timestamp = timestamp

    def _update_first_consumption_log_reference(
        self, address: int, slot: int, timestamp
    ) -> None:
        """Update references to first (oldest) log consumption record."""
        if (
            self._first_log_consumption_timestamp is None or
            self._first_log_consumption_timestamp > timestamp
        ):
            self._first_log_consumption_timestamp = timestamp
            self._first_log_consumption_address = address
            self._first_log_consumption_slot = slot

    def _update_first_production_log_reference(
        self, address: int, slot: int, timestamp
    ) -> None:
        """Update references to first (oldest) log production record."""
        if (
            self._first_log_production_timestamp is None or
            self._first_log_production_timestamp > timestamp
        ):
            self._first_log_production_timestamp = timestamp
            self._first_log_production_address = address
            self._first_log_production_slot = slot

    def _update_log_references(self, address: int, slot: int) -> None:
        """Update next expected log timestamps."""
        if self._logs is None:
            return
        if not self._log_exists(address, slot):
            return
        log_record = self.logs[address][slot]

        # Update log references
        self._update_first_log_reference(address, slot, log_record.timestamp)
        self._update_last_log_reference(address, slot, log_record.timestamp)

        if log_record.is_consumption:
            # Consumption
            self._update_first_consumption_log_reference(
                address, slot, log_record.timestamp
            )
            self._update_last_consumption_log_reference(
                address, slot, log_record.timestamp
            )
        else:
            # production
            self._update_first_production_log_reference(
                address, slot, log_record.timestamp
            )
            self._update_last_production_log_reference(
                address, slot, log_record.timestamp
            )

    @property
    def log_addresses_missing(self) -> list[int] | None:
        """Return the addresses of missing logs"""
        return self._log_addresses_missing

    def _last_log_reference(
        self, is_consumption: bool | None = None
    ) -> tuple[int | None, int | None]:
        """Address and slot of last log"""
        if is_consumption is None:
            return (
                self._last_log_address,
                self._last_log_slot
            )
        if is_consumption:
            return (
                self._last_log_consumption_address,
                self._last_log_consumption_slot
            )
        return (
            self._last_log_production_address,
            self._last_log_production_slot
        )

    def _first_log_reference(
        self, is_consumption: bool | None = None
    ) -> tuple[int | None, int | None]:
        """Address and slot of first log"""
        if is_consumption is None:
            return (
                self._first_log_address,
                self._first_log_slot
            )
        if is_consumption:
            return (
                self._first_log_consumption_address,
                self._first_log_consumption_slot
            )
        return (
            self._first_log_production_address,
            self._first_log_production_slot
        )

    def _logs_missing(self, from_timestamp: datetime) -> list[int] | None:
        """
        Calculate list of missing log addresses
        """
        if self._logs is None:
            self._log_addresses_missing = None
            return None
        last_address, last_slot = self._last_log_reference()
        if last_address is None or last_slot is None:
            _LOGGER.debug("_logs_missing | %s | last_address=%s, last_slot=%s", self._mac, last_address, last_slot)
            return None
        if self._logs[last_address][last_slot].timestamp <= from_timestamp:
            return []

        first_address, first_slot = self._first_log_reference()
        if first_address is None or first_slot is None:
            _LOGGER.debug("_logs_missing | %s | first_address=%s, first_slot=%s", self._mac, first_address, first_slot)
            return None

        missing = []
        _LOGGER.debug("_logs_missing | %s | first_address=%s, last_address=%s", self._mac, first_address, last_address)

        if last_address <= first_address:
            return []

        finished = False
        # Collect any missing address in current range
        for address in range(last_address - 1, first_address, -1):
            for slot in range(4, 0, -1):
                if address in missing:
                    break
                if not self._log_exists(address, slot):
                    missing.append(address)
                    break
                if self.logs[address][slot].timestamp <= from_timestamp:
                    finished = True
                    break
            if finished:
                break
        if finished:
            return missing

        # return missing logs in range first
        if len(missing) > 0:
            _LOGGER.debug("_logs_missing | %s | missing in range=%s", self._mac, missing)
            return missing

        if self.logs[first_address][first_slot].timestamp < from_timestamp:
            return missing

        # calculate missing log addresses prior to first collected log
        address, slot = calc_log_address(first_address, first_slot, -1)
        calculated_timestamp = self.logs[first_address][first_slot].timestamp - timedelta(hours=1)
        while from_timestamp < calculated_timestamp:
            if address not in missing:
                missing.append(address)
            address, slot = calc_log_address(address, slot, -1)
            calculated_timestamp -= timedelta(hours=1)

        missing.sort(reverse=True)
        _LOGGER.debug("_logs_missing | %s | calculated missing=%s", self._mac, missing)
        return missing

    def _last_known_duration(self) -> timedelta:
        """Duration for last known logs"""
        if len(self.logs) < 2:
            return timedelta(hours=1)
        address, slot = self._last_log_reference()
        last_known_timestamp = self.logs[address][slot].timestamp
        address, slot = calc_log_address(address, slot, -1)
        while (
            self._log_exists(address, slot) or 
            self.logs[address][slot].timestamp == last_known_timestamp
        ):
            address, slot = calc_log_address(address, slot, -1)
        return self.logs[address][slot].timestamp - last_known_timestamp

    def _missing_addresses_before(
        self, address: int, slot: int, target: datetime
    ) -> list[int]:
        """Return list of missing address(es) prior to given log timestamp."""
        addresses: list[int] = []
        if self._logs is None or target >= self._logs[address][slot].timestamp:
            return addresses

        # default interval
        calc_interval_cons = timedelta(hours=1)
        if (
            self._log_interval_consumption is not None
            and self._log_interval_consumption > 0
        ):
            # Use consumption interval
            calc_interval_cons = timedelta(
                minutes=self._log_interval_consumption
            )
            if self._log_interval_consumption == 0:
                pass

        if self._log_production is not True:
            expected_timestamp = (
                self._logs[address][slot].timestamp - calc_interval_cons
            )
            address, slot = calc_log_address(address, slot, -1)
            while expected_timestamp > target and address > 0:
                if address not in addresses:
                    addresses.append(address)
                expected_timestamp -= calc_interval_cons
                address, slot = calc_log_address(address, slot, -1)
        else:
            # Production logging active
            calc_interval_prod = timedelta(hours=1)
            if (
                self._log_interval_production is not None
                and self._log_interval_production > 0
            ):
                calc_interval_prod = timedelta(
                    minutes=self._log_interval_production
                )

            expected_timestamp_cons = (
                self._logs[address][slot].timestamp - calc_interval_cons
            )
            expected_timestamp_prod = (
                self._logs[address][slot].timestamp - calc_interval_prod
            )

            address, slot = calc_log_address(address, slot, -1)
            while (
                expected_timestamp_cons > target
                or expected_timestamp_prod > target
            ) and address > 0:
                if address not in addresses:
                    addresses.append(address)
                if expected_timestamp_prod > expected_timestamp_cons:
                    expected_timestamp_prod -= calc_interval_prod
                else:
                    expected_timestamp_cons -= calc_interval_cons
                address, slot = calc_log_address(address, slot, -1)

        return addresses

    def _missing_addresses_after(
        self, address: int, slot: int, target: datetime
    ) -> list[int]:
        """Return list of any missing address(es) after given log timestamp."""
        addresses: list[int] = []

        if self._logs is None:
            return addresses

        # default interval
        calc_interval_cons = timedelta(hours=1)
        if (
            self._log_interval_consumption is not None
            and self._log_interval_consumption > 0
        ):
            # Use consumption interval
            calc_interval_cons = timedelta(
                minutes=self._log_interval_consumption
            )

        if self._log_production is not True:
            expected_timestamp = (
                self._logs[address][slot].timestamp + calc_interval_cons
            )
            address, slot = calc_log_address(address, slot, 1)
            while expected_timestamp < target:
                address, slot = calc_log_address(address, slot, 1)
                expected_timestamp += timedelta(hours=1)
                if address not in addresses:
                    addresses.append(address)
            return addresses

        # Production logging active
        calc_interval_prod = timedelta(hours=1)
        if (
            self._log_interval_production is not None
            and self._log_interval_production > 0
        ):
            calc_interval_prod = timedelta(
                minutes=self._log_interval_production
            )

        expected_timestamp_cons = (
            self._logs[address][slot].timestamp + calc_interval_cons
        )
        expected_timestamp_prod = (
            self._logs[address][slot].timestamp + calc_interval_prod
        )
        address, slot = calc_log_address(address, slot, 1)
        while (
            expected_timestamp_cons < target
            or expected_timestamp_prod < target
        ):
            if address not in addresses:
                addresses.append(address)
            if expected_timestamp_prod < expected_timestamp_cons:
                expected_timestamp_prod += calc_interval_prod
            else:
                expected_timestamp_cons += calc_interval_cons
            address, slot = calc_log_address(address, slot, 1)
        return addresses
