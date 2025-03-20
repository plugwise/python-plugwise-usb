"""Energy pulse helper."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
from typing import Final

from ...constants import LOGADDR_MAX, MINUTE_IN_SECONDS, WEEK_IN_HOURS
from ...exceptions import EnergyError

_LOGGER = logging.getLogger(__name__)
CONSUMED: Final = True
PRODUCED: Final = False
PRODUCERS: tuple[str] = ("000D6F00029C32C7", "dummy")

MAX_LOG_HOURS = WEEK_IN_HOURS


def calc_log_address(address: int, slot: int, offset: int) -> tuple[int, int]:
    """Calculate address and slot for log based for specified offset."""

    if offset < 0:
        while offset + slot < 1:
            address -= 1
            # Check for log address rollover
            if address <= -1:
                address = LOGADDR_MAX - 1
            offset += 4
    if offset > 0:
        while offset + slot > 4:
            address += 1
            # Check for log address rollover
            if address >= LOGADDR_MAX:
                address = 0
            offset -= 4
    return (address, slot + offset)


@dataclass
class PulseLogRecord:
    """Total pulses collected at specific timestamp."""

    timestamp: datetime
    pulses: int
    is_consumption: bool


class PulseCollection:
    """Store energy pulses of the current interval and past (history log) intervals."""

    def __init__(self, mac: str) -> None:
        """Initialize PulseCollection class."""
        self._mac = mac
        self._log_interval: int | None = None

        self._last_log_address: int | None = None
        self._last_log_slot: int | None = None
        self._last_log_timestamp: datetime | None = None
        self._first_log_address: int | None = None
        self._first_log_slot: int | None = None
        self._first_log_timestamp: datetime | None = None

        self._first_empty_log_address: int | None = None
        self._first_empty_log_slot: int | None = None
        self._last_empty_log_address: int | None = None
        self._last_empty_log_slot: int | None = None

        self._last_log_timestamp: datetime | None = None
        self._last_log_address: int | None = None
        self._last_log_slot: int | None = None
        self._first_log_timestamp: datetime | None = None
        self._first_log_address: int | None = None
        self._first_log_slot: int | None = None
        self._next_log_timestamp: datetime | None = None

        self._consumption_counter_reset = False
        self._production_counter_reset = False
        self._rollover_consumption = False
        self._rollover_production = False

        self._logs: dict[int, dict[int, PulseLogRecord]] | None = None
        self._log_addresses_missing: list[int] | None = None
        self._pulses_consumption: int | None = None
        self._pulses_production: int | None = None
        self._pulses_timestamp: datetime | None = None

        self._log_production = False
        if mac in PRODUCERS:
            self._log_production = True

    @property
    def collected_logs(self) -> int:
        """Total collected logs."""
        counter = 0
        if self._logs is None:
            return counter
        for address in self._logs:
            counter += len(self._logs[address])
        return counter

    @property
    def logs(self) -> dict[int, dict[int, PulseLogRecord]]:
        """Return currently collected pulse logs in reversed order."""
        if self._logs is None:
            return {}
        sorted_log: dict[int, dict[int, PulseLogRecord]] = {}
        skip_before = datetime.now(tz=UTC) - timedelta(hours=MAX_LOG_HOURS)
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
        """Return address and slot of last imported log."""
        if (
            self._last_log_address is None
            or self._last_log_slot is None
        ):
            return None
        return (self._last_log_address, self._last_log_slot)

    @property
    def production_logging(self) -> bool | None:
        """Indicate if production logging is active."""
        return self._log_production

    @property
    def log_interval(self) -> int | None:
        """Interval in minutes between the last two pulse logs."""
        return self._log_interval

    @property
    def log_rollover(self) -> bool:
        """Indicate if new log is required."""
        return self._rollover_consumption or self._rollover_production

    @property
    def last_update(self) -> datetime | None:
        """Return timestamp of last update."""
        return self._pulses_timestamp

    def collected_pulses(
        self, from_timestamp: datetime, is_consumption: bool
    ) -> tuple[int | None, datetime | None]:
        """Calculate total pulses from given timestamp."""
        _LOGGER.debug(
            "collected_pulses 1 | %s | is_cons=%s, from_timestamp=%s",
            self._mac,
            is_consumption,
            from_timestamp,
        )
        _LOGGER.debug("collected_pulses 1a | _log_production=%s", self._log_production)
        if not is_consumption and not self._log_production:
            return (None, None)

        # !! consumption-pulses reset every hour - pulses just before rollover are lost?

        # !! production-pulses do not reset every hour but at the max counter value - double-check
        # if this is correct the pulses lost at rollover can be calculated:
        # max-counter - prev-value + counter after reset

        # Is the below code (6 lines) correct?
        if is_consumption and self._rollover_consumption:
            _LOGGER.debug("collected_pulses 2 | %s | _rollover_consumption", self._mac)
            return (None, None)
        if not is_consumption and self._rollover_production:
            _LOGGER.debug("collected_pulses 2 | %s | _rollover_production", self._mac)
            return (None, None)

        if (
            log_pulses := self._collect_pulses_from_logs(from_timestamp, is_consumption)
        ) is None:
            _LOGGER.debug("collected_pulses 3 | %s | log_pulses:None", self._mac)
            return (None, None)

        pulses: int | None = None
        timestamp: datetime | None = None
        if is_consumption and self._pulses_consumption is not None:
            pulses = self._pulses_consumption
            timestamp = self._pulses_timestamp
        if not is_consumption and self._pulses_production is not None:
            pulses = self._pulses_production
            timestamp = self._pulses_timestamp
        # _LOGGER.debug("collected_pulses | %s | pulses=%s", self._mac, pulses)
        if pulses is None:
            _LOGGER.debug(
                "collected_pulses 4 | %s | is_consumption=%s, pulses=None",
                self._mac,
                is_consumption,
            )
            return (None, None)
    
        _LOGGER.debug(
            "collected_pulses 5 | pulses=%s | log_pulses=%s | consumption=%s at timestamp=%s",
            pulses,
            log_pulses,
            is_consumption,
            timestamp,
        )
        return (pulses + log_pulses, timestamp)

    def _collect_pulses_from_logs(
        self, from_timestamp: datetime, is_consumption: bool
    ) -> int | None:
        """Collect all pulses from logs."""
        if self._logs is None:
            _LOGGER.debug("_collect_pulses_from_logs | %s | self._logs=None", self._mac)
            return None

        if self._last_log_timestamp is None:
            _LOGGER.debug(
                "_collect_pulses_from_logs | %s | self._last_log_timestamp=None",
                self._mac,
            )
            return None

        if from_timestamp > self._last_log_timestamp:
            return 0

        missing_logs = self._logs_missing(from_timestamp)
        if missing_logs is None or missing_logs:
            _LOGGER.debug(
                "_collect_pulses_from_logs | %s | missing_logs=%s",
                self._mac,
                missing_logs,
            )
            return None

        log_pulses = 0
        for log_item in self._logs.values():
            for slot_item in log_item.values():
                if (
                    slot_item.is_consumption == is_consumption
                    and slot_item.timestamp > from_timestamp
                ):
                    log_pulses += slot_item.pulses

        return log_pulses

    def update_pulse_counter(
        self, pulses_consumed: int, pulses_produced: int, timestamp: datetime
    ) -> None:
        """Update pulse counter, checking for rollover based on counter reset."""
        self._pulses_timestamp = timestamp
        self._update_rollover(True)
        if self._log_production:
            self._update_rollover(False)

        if (self._rollover_consumption or self._rollover_production):
            return

        # No rollover based on time, check rollover based on counter reset
        # Required for special cases like nodes which have been power off for several days
        if (
            self._pulses_consumption is not None
            and self._pulses_consumption > pulses_consumed
        ):
            self._consumption_counter_reset = True
            _LOGGER.debug(
                "_consumption_counter_reset | self._pulses_consumption=%s > pulses_consumed=%s",
                self._pulses_consumption,
                pulses_consumed,
            )

        if (
            self._pulses_production is not None
            and self._pulses_production < pulses_produced
        ):
            self._production_counter_reset = True
            _LOGGER.debug(
                "_production_counter_reset | self._pulses_production=%s < pulses_produced=%s",
                self._pulses_production,
                pulses_produced,
            )

        self._pulses_consumption = pulses_consumed
        self._pulses_production = pulses_produced

    def _update_rollover(self, consumption: bool) -> None:
        """Update rollover states.
        
        When the last found timestamp is outside the interval `_last_log_timestamp`
        to `_next_log_timestamp` the pulses should not be counted as part of the 
        ongoing collection-interval.
        """
        if self._log_addresses_missing is not None and self._log_addresses_missing:
            return

        if (
            self._pulses_timestamp is None
            or self._last_log_timestamp is None
        ):
            # Unable to determine rollover
            return

        if self._pulses_timestamp > self._next_log_timestamp:
            if consumption:
                self._rollover_consumption = True
                _LOGGER.debug(
                    "_update_rollover | %s | set consumption rollover => pulses newer",
                    self._mac,
                )
            else:
                self._rollover_production = True
                _LOGGER.debug(
                    "_update_rollover | %s | set production rollover => pulses newer",
                    self._mac,
                )
            return

        if self._pulses_timestamp < self._last_log_timestamp:
            if consumption:
                self._rollover_consumption = True
                _LOGGER.debug(
                    "_update_rollover | %s | set consumption rollover => log newer",
                    self._mac,
                )
            else:
                self._rollover_production = True
                _LOGGER.debug(
                    "_update_rollover | %s | set production rollover => log newer",
                    self._mac,
                )
            return

        # _last_log_timestamp <= _pulses_timestamp <= _next_log_timestamp
        if consumption:
            if self._rollover_consumption:
                _LOGGER.debug("_update_rollover | %s | reset consumption rollover", self._mac)
            self._rollover_consumption = False
        else:
            if self._rollover_production:
                _LOGGER.debug("_update_rollover | %s | reset production rollover", self._mac)
            self._rollover_production = False

    def add_empty_log(self, address: int, slot: int) -> None:
        """Add empty energy log record to mark any start of beginning of energy log collection."""
        recalculate = False
        if self._first_log_address is None or address <= self._first_log_address:
            if (
                self._first_empty_log_address is None
                or self._first_empty_log_address < address
            ):
                self._first_empty_log_address = address
                self._first_empty_log_slot = slot
                recalculate = True
            elif self._first_empty_log_address == address and (
                self._first_empty_log_slot is None or self._first_empty_log_slot < slot
            ):
                self._first_empty_log_slot = slot
                recalculate = True

        if self._last_log_address is None or address >= self._last_log_address:
            if (
                self._last_empty_log_address is None
                or self._last_empty_log_address > address
            ):
                self._last_empty_log_address = address
                self._last_empty_log_slot = slot
                recalculate = True
            elif self._last_empty_log_address == address and (
                self._last_empty_log_slot is None or self._last_empty_log_slot > slot
            ):
                self._last_empty_log_slot = slot
                recalculate = True
        if recalculate:
            self.recalculate_missing_log_addresses()

    # pylint: disable=too-many-arguments
    def add_log(
        self,
        address: int,
        slot: int,
        timestamp: datetime,
        pulses: int,
        import_only: bool = False,
    ) -> bool:
        """Store pulse log."""
        _LOGGER.debug(
            "add_log | address=%s | slot=%s | timestamp=%s | pulses=%s | import_only=%s",
            address,
            slot,
            timestamp,
            pulses,
            import_only,
        )
        direction = CONSUMED
        if self._log_production and pulses < 0:
            direction = PRODUCED

        log_record = PulseLogRecord(timestamp, pulses, direction)
        if not self._add_log_record(address, slot, log_record):
            if not self._log_exists(address, slot):
                return False
            if address != self._last_log_address and slot != self._last_log_slot:
                return False
        self._update_log_references(address, slot)
        self._update_log_interval()
        self._update_rollover(direction)
        if not import_only:
            self.recalculate_missing_log_addresses()
        return True

    def recalculate_missing_log_addresses(self) -> None:
        """Recalculate missing log addresses."""
        self._log_addresses_missing = self._logs_missing(
            datetime.now(tz=UTC) - timedelta(hours=MAX_LOG_HOURS)
        )

    def _add_log_record(
        self, address: int, slot: int, log_record: PulseLogRecord
    ) -> bool:
        """Add log record.

        Return False if log record already exists, or is not required because its timestamp is expired.
        """
        if self._logs is None:
            self._logs = {address: {slot: log_record}}
            return True
        if self._log_exists(address, slot):
            return False
        # Drop useless log records when we have at least 4 logs
        if self.collected_logs > 4 and log_record.timestamp < (
            datetime.now(tz=UTC) - timedelta(hours=MAX_LOG_HOURS)
        ):
            return False
        if self._logs.get(address) is None:
            self._logs[address] = {slot: log_record}
        self._logs[address][slot] = log_record
        if (
            address == self._first_empty_log_address
            and slot == self._first_empty_log_slot
        ):
            self._first_empty_log_address = None
            self._first_empty_log_slot = None
        if (
            address == self._last_empty_log_address
            and slot == self._last_empty_log_slot
        ):
            self._last_empty_log_address = None
            self._last_empty_log_slot = None
        return True

    def _update_log_interval(self) -> None:
        """Update the detected log interval based on the most recent two logs."""
        if self._logs is None:
            _LOGGER.debug(
                "_update_log_interval fail | %s | _logs=%s",
                self._mac,
                self._logs,
            )
            return

        last_address, last_slot = self._last_log_reference()
        if last_address is None or last_slot is None:
            return

        last_timestamp = self._logs[last_address][last_slot].timestamp
        address, slot = calc_log_address(last_address, last_slot, -1)
        if self._log_exists(address, slot):
            delta: timedelta = (
                last_timestamp - self._logs[address][slot].timestamp
            )
            self._log_interval = int(
                delta.total_seconds() / MINUTE_IN_SECONDS
            )

        if (
            self._log_interval is not None
            and self._last_log_timestamp is not None
        ):
            self._next_log_timestamp = (
                self._last_log_timestamp
                + timedelta(minutes=self._log_interval)
            )

    def _log_exists(self, address: int, slot: int) -> bool:
        if self._logs is None:
            return False
        if self._logs.get(address) is None:
            return False
        if self._logs[address].get(slot) is None:
            return False
        return True

    def _update_last_log_reference(
        self, address: int, slot: int, timestamp: datetime
    ) -> None:
        """Update references to last (most recent) log record."""
        if self._last_log_timestamp is None or self._last_log_timestamp < timestamp:
            self._last_log_address = address
            self._last_log_slot = slot
            self._last_log_timestamp = timestamp

    def _reset_log_references(self) -> None:
        """Reset log references."""
        self._last_log_address = None
        self._last_log_slot = None
        self._last_log_timestamp = None
        self._first_log_address = None
        self._first_log_slot = None
        self._first_log_timestamp = None

        if self._logs is None:
            return

        for address in self._logs:
            for slot, log_record in self._logs[address].items():
                if self._last_log_timestamp is None:
                    self._last_log_timestamp = log_record.timestamp
                if self._last_log_timestamp <= log_record.timestamp:
                    self._last_log_timestamp = log_record.timestamp
                    self._last_log_address = address
                    self._last_log_slot = slot

                if self._first_log_timestamp is None:
                    self._first_log_timestamp = log_record.timestamp
                if self._first_log_timestamp >= log_record.timestamp:
                    self._first_log_timestamp = log_record.timestamp
                    self._first_log_address = address
                    self._first_log_slot = slot

    def _update_first_log_reference(
        self, address: int, slot: int, timestamp: datetime
    ) -> None:
        """Update references to first (oldest) log record."""
        if self._first_log_timestamp is None or self._first_log_timestamp > timestamp:
            self._first_log_address = address
            self._first_log_slot = slot
            self._first_log_timestamp = timestamp

    def _update_log_references(self, address: int, slot: int) -> None:
        """Update next expected log timestamps."""
        if self._logs is None:
            return

        log_time_stamp = self._logs[address][slot].timestamp

        # Update log references
        self._update_first_log_reference(address, slot, log_time_stamp)
        self._update_last_log_reference(address, slot, log_time_stamp)

    @property
    def log_addresses_missing(self) -> list[int] | None:
        """Return the addresses of missing logs."""
        return self._log_addresses_missing

    def _last_log_reference(self) -> tuple[int | None, int | None]:
        """Address and slot of last log."""
        return (self._last_log_address, self._last_log_slot)

    def _first_log_reference(self) -> tuple[int | None, int | None]:
        """Address and slot of first log."""
        return (self._first_log_address, self._first_log_slot)

    def _logs_missing(self, from_timestamp: datetime) -> list[int] | None:
        """Calculate list of missing log addresses."""
        if self._logs is None:
            self._log_addresses_missing = None
            return None

        if self.collected_logs < 2:
            return None

        last_address, last_slot = self._last_log_reference()
        if last_address is None or last_slot is None:
            _LOGGER.debug(
                "_logs_missing | %s | last_address=%s, last_slot=%s",
                self._mac,
                last_address,
                last_slot,
            )
            return None

        first_address, first_slot = self._first_log_reference()
        if first_address is None or first_slot is None:
            _LOGGER.debug(
                "_logs_missing | %s | first_address=%s, first_slot=%s",
                self._mac,
                first_address,
                first_slot,
            )
            return None

        missing = []
        _LOGGER.debug(
            "_logs_missing | %s | first_address=%s, last_address=%s",
            self._mac,
            first_address,
            last_address,
        )

        if (
            last_address == first_address
            and last_slot == first_slot
            and self._logs[first_address][first_slot].timestamp
            == self._logs[last_address][last_slot].timestamp
        ):
            # Power consumption logging, so we need at least 4 logs.
            return None

        # Collect any missing address in current range
        address = last_address
        slot = last_slot
        while not (address == first_address and slot == first_slot):
            address, slot = calc_log_address(address, slot, -1)
            if address in missing:
                continue
            if not self._log_exists(address, slot):
                missing.append(address)
                continue
            if self._logs[address][slot].timestamp <= from_timestamp:
                break

        # return missing logs in range first
        if len(missing) > 0:
            _LOGGER.debug(
                "_logs_missing | %s | missing in range=%s", self._mac, missing
            )
            return missing

        if first_address not in self._logs:
            return missing

        if first_slot not in self._logs[first_address]:
            return missing

        if self._logs[first_address][first_slot].timestamp < from_timestamp:
            return missing

        # Check if we are able to calculate log interval
        address, slot = calc_log_address(first_address, first_slot, -1)
        log_interval: int | None = None
        if self._log_interval is not None:
            log_interval = self._log_interval

        if log_interval is None:
            return None

        # We have an suspected interval, so try to calculate missing log addresses prior to first collected log
        calculated_timestamp = self._logs[first_address][
            first_slot
        ].timestamp - timedelta(minutes=log_interval)
        while from_timestamp < calculated_timestamp:
            if (
                address == self._first_empty_log_address
                and slot == self._first_empty_log_slot
            ):
                break
            if address not in missing:
                missing.append(address)
            calculated_timestamp -= timedelta(minutes=log_interval)
            address, slot = calc_log_address(address, slot, -1)

        missing.sort(reverse=True)
        _LOGGER.debug("_logs_missing | %s | calculated missing=%s", self._mac, missing)
        return missing

    def _last_known_duration(self) -> timedelta:
        """Duration for last known logs."""
        if self._logs is None:
            raise EnergyError("Unable to return last known duration without any logs")
        if len(self._logs) < 2:
            return timedelta(hours=1)
        address, slot = self._last_log_reference()
        if address is None or slot is None:
            raise EnergyError("Unable to return last known duration without any logs")
        last_known_timestamp = self._logs[address][slot].timestamp
        address, slot = calc_log_address(address, slot, -1)
        while (
            self._log_exists(address, slot)
            or self._logs[address][slot].timestamp == last_known_timestamp
        ):
            address, slot = calc_log_address(address, slot, -1)
        return self._logs[address][slot].timestamp - last_known_timestamp

    def _missing_addresses_before(
        self, address: int, slot: int, target: datetime
    ) -> list[int]:
        """Return list of missing address(es) prior to given log timestamp."""
        addresses: list[int] = []
        if self._logs is None or target >= self._logs[address][slot].timestamp:
            return addresses

        # default interval
        calc_interval = timedelta(hours=1)
        if (
            self._log_interval is not None
            and self._log_interval > 0
        ):
            calc_interval = timedelta(minutes=self._log_interval)
            if self._log_interval == 0:
                pass

        expected_timestamp = (
            self._logs[address][slot].timestamp - calc_interval
        )
        address, slot = calc_log_address(address, slot, -1)
        while expected_timestamp > target and address > 0:
            if address not in addresses:
                addresses.append(address)
            expected_timestamp -= calc_interval
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
        calc_interval = timedelta(hours=1)
        if (
            self._log_interval is not None
            and self._log_interval > 0
        ):
            calc_interval = timedelta(minutes=self._log_interval)

        expected_timestamp = (
            self._logs[address][slot].timestamp + calc_interval
        )
        address, slot = calc_log_address(address, slot, 1)
        while expected_timestamp < target:
            address, slot = calc_log_address(address, slot, 1)
            expected_timestamp += timedelta(hours=1)
            if address not in addresses:
                addresses.append(address)

        return addresses
