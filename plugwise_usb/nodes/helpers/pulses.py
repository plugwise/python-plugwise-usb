"""Energy pulse helper."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
from typing import Final

from ...constants import DAY_IN_HOURS, LOGADDR_MAX, MINUTE_IN_SECONDS
from ...exceptions import EnergyError

_LOGGER = logging.getLogger(__name__)
CONSUMED: Final = True
PRODUCED: Final = False

MAX_LOG_HOURS = DAY_IN_HOURS


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
    """Store consumed and produced energy pulses of the current interval and past (history log) intervals."""

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

        self._first_empty_log_address: int | None = None
        self._first_empty_log_slot: int | None = None
        self._last_empty_log_address: int | None = None
        self._last_empty_log_slot: int | None = None

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

        self._cons_pulsecounter_reset = False
        self._prod_pulsecounter_reset = False
        self._rollover_consumption = False
        self._rollover_production = False

        self._first_next_log_processed = False
        self._first_prev_log_processed = False
        self._logs: dict[int, dict[int, PulseLogRecord]] | None = None
        self._log_addresses_missing: list[int] | None = None
        self._log_production: bool | None = None
        self._pulses_consumption: int | None = None
        self._pulses_production: int | None = None
        self._pulses_timestamp: datetime | None = None

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
            self._last_log_consumption_address is None
            or self._last_log_consumption_slot is None
        ):
            return None
        return (self._last_log_consumption_address, self._last_log_consumption_slot)

    @property
    def production_logging(self) -> bool | None:
        """Indicate if production logging is active."""
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
        """Indicate if new log is required."""
        return self._rollover_consumption or self._rollover_production

    @property
    def last_update(self) -> datetime | None:
        """Return timestamp of last update."""
        return self._pulses_timestamp

    @property
    def pulse_counter_reset(self) -> bool:
        """Return a pulse_counter reset."""
        return self._cons_pulsecounter_reset or self._prod_pulsecounter_reset

    def reset(self) -> None:
        """Reset PulseCollection after an energy-logs reset."""
        # Keep mac, wipe every other attribute.
        fresh_state = PulseCollection(self._mac).__dict__
        self.__dict__.clear()  # remove *all* existing keys first
        self.__dict__.update(fresh_state)

    def collected_pulses(
        self, from_timestamp: datetime, is_consumption: bool
    ) -> tuple[int | None, datetime | None]:
        """Calculate total pulses from given timestamp."""
        _LOGGER.debug(
            "collected_pulses | %s | from_timestamp=%s | is_cons=%s | _log_production=%s",
            self._mac,
            from_timestamp,
            is_consumption,
            self._log_production,
        )
        if not is_consumption:
            if self._log_production is None or not self._log_production:
                return (None, None)

        pulses: int | None = None
        timestamp: datetime | None = None
        if is_consumption and self._pulses_consumption is not None:
            pulses = self._pulses_consumption
            timestamp = self._pulses_timestamp

        if not is_consumption and self._pulses_production is not None:
            pulses = self._pulses_production
            timestamp = self._pulses_timestamp

        if pulses is None:
            _LOGGER.debug(
                "collected_pulses | %s | is_consumption=%s, pulses=None",
                self._mac,
                is_consumption,
            )
            return (None, None)

        if (
            log_pulses := self._collect_pulses_from_logs(from_timestamp, is_consumption)
        ) is None:
            _LOGGER.debug("collected_pulses | %s | log_pulses:None", self._mac)
            return (None, None)

        _LOGGER.debug(
            "collected_pulses | pulses=%s | log_pulses=%s | consumption=%s at timestamp=%s",
            pulses,
            log_pulses,
            is_consumption,
            timestamp,
        )

        # Always return positive values for energy_statistics
        return (abs(pulses + log_pulses), timestamp)

    def _collect_pulses_from_logs(
        self, from_timestamp: datetime, is_consumption: bool
    ) -> int | None:
        """Collect all pulses from logs."""
        if self._logs is None:
            _LOGGER.debug("_collect_pulses_from_logs | %s | self._logs=None", self._mac)
            return None

        timestamp: datetime | None = None
        if is_consumption:
            if self._last_log_consumption_timestamp is None:
                _LOGGER.debug(
                    "_collect_pulses_from_logs | %s | self._last_log_consumption_timestamp=None",
                    self._mac,
                )
                return None

            timestamp = self._last_log_consumption_timestamp
        else:
            if self._last_log_production_timestamp is None:
                _LOGGER.debug(
                    "_collect_pulses_from_logs | %s | self._last_log_production_timestamp=None",
                    self._mac,
                )
                return None

            timestamp = self._last_log_production_timestamp

        missing_logs = self._logs_missing(from_timestamp)
        if missing_logs is None or missing_logs:
            _LOGGER.debug(
                "_collect_pulses_from_logs | %s | missing_logs=%s",
                self._mac,
                missing_logs,
            )
            return None

        log_pulses = 0
        for log_item in self.logs.values():
            for slot_item in log_item.values():
                if (
                    slot_item.is_consumption == is_consumption
                    and slot_item.timestamp > from_timestamp
                ):
                    log_pulses += slot_item.pulses

        _LOGGER.debug(
            "_collect_pulses_from_logs | log_pulses=%s | is_consumption=%s | from %s to %s",
            log_pulses,
            is_consumption,
            from_timestamp,
            timestamp,
        )
        return log_pulses

    def update_pulse_counter(
        self, pulses_consumed: int, pulses_produced: int, timestamp: datetime
    ) -> None:
        """Update pulse counter.

        Both device consumption and production counters reset after the beginning of a new hour.
        """
        self._cons_pulsecounter_reset = False
        self._prod_pulsecounter_reset = False
        self._pulses_timestamp = timestamp
        self._update_rollover()
        if (
            self._pulses_consumption is not None
            and self._pulses_consumption > pulses_consumed
        ):
            self._cons_pulsecounter_reset = True
            _LOGGER.debug("update_pulse_counter | consumption pulses reset")

        if (
            self._pulses_production is not None
            and self._pulses_production < pulses_produced
        ):
            self._prod_pulsecounter_reset = True
            _LOGGER.debug("update_pulse_counter | production pulses reset")

        # No rollover based on time, set rollover based on counter reset
        # Required for special cases like nodes which have been powered off for several days
        if not (self._rollover_consumption or self._rollover_production):
            if self._cons_pulsecounter_reset:
                _LOGGER.debug("update_pulse_counter | rollover consumption")
                self._rollover_consumption = True

            if self._prod_pulsecounter_reset:
                _LOGGER.debug("update_pulse_counter | rollover production")
                self._rollover_production = True

        self._pulses_consumption = pulses_consumed
        self._pulses_production = pulses_produced
        _LOGGER.debug(
            "update_pulse_counter | consumption pulses=%s | production pulses=%s",
            self._pulses_consumption,
            self._pulses_production,
        )

    def _update_rollover(self) -> None:
        """Update rollover states.

        When the last found timestamp is outside the interval `_last_log_timestamp`
        to `_next_log_timestamp` the pulses should not be counted as part of the
        ongoing collection-interval.
        """
        if self._log_addresses_missing is not None and self._log_addresses_missing:
            return

        self._rollover_consumption = self._detect_rollover(
            self._rollover_consumption,
            self._last_log_consumption_timestamp,
            self._next_log_consumption_timestamp,
        )
        if self._log_production:
            self._rollover_production = self._detect_rollover(
                self._rollover_production,
                self._last_log_production_timestamp,
                self._next_log_production_timestamp,
                False,
            )

    def _detect_rollover(
        self,
        rollover: bool,
        last_log_timestamp: datetime | None,
        next_log_timestamp: datetime | None,
        is_consumption=True,
    ) -> bool:
        """Detect rollover based on timestamp comparisons."""
        if (
            self._pulses_timestamp is not None
            and last_log_timestamp is not None
            and next_log_timestamp is not None
        ):
            direction = "consumption"
            if not is_consumption:
                direction = "production"

            if self._pulses_timestamp > next_log_timestamp:
                _LOGGER.debug(
                    "_update_rollover | %s | set %s rollover => pulses newer",
                    self._mac,
                    direction,
                )
                return True

            if self._pulses_timestamp < last_log_timestamp:
                _LOGGER.debug(
                    "_update_rollover | %s | set %s rollover => log newer",
                    self._mac,
                    direction,
                )
                return True

            if last_log_timestamp <= self._pulses_timestamp <= next_log_timestamp:
                if rollover:
                    _LOGGER.debug(
                        "_update_rollover | %s | reset %s rollover",
                        self._mac,
                        direction,
                    )
                return False

        return False

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
        direction = CONSUMED
        if self._log_production and pulses < 0:
            direction = PRODUCED

        log_record = PulseLogRecord(timestamp, pulses, direction)
        if not self._add_log_record(address, slot, log_record):
            if not self._log_exists(address, slot):
                return False
            if address != self._last_log_address and slot != self._last_log_slot:
                _LOGGER.debug("add_log | address-slot already exists")
                return False
        self._update_log_direction(address, slot, timestamp)
        self._update_log_references(address, slot)
        self._update_log_interval()
        self._update_rollover()
        if not import_only:
            self.recalculate_missing_log_addresses()

        _LOGGER.debug(
            "add_log | pulses=%s | address=%s | slot=%s | time=%s, direction=%s",
            pulses,
            address,
            slot,
            timestamp,
            direction,
        )
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
        if self.collected_logs > 4 and log_record.timestamp <= (
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

    def _update_log_direction(
        self, address: int, slot: int, timestamp: datetime
    ) -> None:
        """Update Energy direction of log record.

        Two subsequential logging slots with the same timestamp indicate
        both consumption and production logging is active.
        The first slot (e.g. 1) contains consumption data,
        the second slot (e.g. 2) production data.

        This code expects either single slots containing consumption data
        or double slots containing consumption and production data.
        Single slots containing production data only is NOT supported/tested.
        """
        prev_timestamp = self._check_prev_production(address, slot, timestamp)
        next_timestamp = self._check_next_production(address, slot, timestamp)
        if self._first_prev_log_processed and self._first_next_log_processed:
            # _log_production is True when 2 out of 3 consecutive slots have
            # the same timestamp
            self._log_production = (prev_timestamp == timestamp) ^ (
                next_timestamp == timestamp
            )

    def _check_prev_production(
        self, address: int, slot: int, timestamp: datetime
    ) -> datetime | None:
        """Check the previous slot for production pulses."""
        if self._logs is None:
            return

        prev_address, prev_slot = calc_log_address(address, slot, -1)
        if self._log_exists(prev_address, prev_slot):
            prev_timestamp = self._logs[prev_address][prev_slot].timestamp
            if not self._first_prev_log_processed:
                self._first_prev_log_processed = True
                if prev_timestamp == timestamp:
                    # Given log is the second log with same timestamp,
                    # mark direction as production
                    self._logs[address][slot].is_consumption = False
                    self._logs[prev_address][prev_slot].is_consumption = True
                    self._log_production = True
                elif self._log_production:
                    self._logs[address][slot].is_consumption = True
                    if self._logs[prev_address][prev_slot].is_consumption:
                        self._logs[prev_address][prev_slot].is_consumption = False
                        self._reset_log_references()
                elif self._log_production is None:
                    self._log_production = False
            return prev_timestamp

        if self._first_prev_log_processed:
            self._first_prev_log_processed = False
            return None

    def _check_next_production(
        self, address: int, slot: int, timestamp: datetime
    ) -> datetime | None:
        """Check the next slot for production pulses."""
        if self._logs is None:
            return

        next_address, next_slot = calc_log_address(address, slot, 1)
        if self._log_exists(next_address, next_slot):
            next_timestamp = self._logs[next_address][next_slot].timestamp
            if not self._first_next_log_processed:
                self._first_next_log_processed = True
                if next_timestamp == timestamp:
                    # Given log is the first log with same timestamp,
                    # mark direction as production of next log
                    self._logs[address][slot].is_consumption = True
                    if self._logs[next_address][next_slot].is_consumption:
                        self._logs[next_address][next_slot].is_consumption = False
                        self._reset_log_references()
                    self._log_production = True
                elif self._log_production:
                    self._logs[address][slot].is_consumption = False
                    self._logs[next_address][next_slot].is_consumption = True
                elif self._log_production is None:
                    self._log_production = False
            return next_timestamp

        if self._first_next_log_processed:
            self._first_next_log_processed = False
            return None

    def _update_log_interval(self) -> None:
        """Update the detected log interval based on the most recent two logs."""
        if self._logs is None or self._log_production is None:
            _LOGGER.debug(
                "_update_log_interval | %s | _logs=%s, _log_production=%s",
                self._mac,
                self._logs,
                self._log_production,
            )
            return

        last_cons_address, last_cons_slot = self._last_log_reference(
            is_consumption=True
        )
        if last_cons_address is None or last_cons_slot is None:
            return

        # Update interval of consumption
        last_cons_timestamp = self._logs[last_cons_address][last_cons_slot].timestamp
        address, slot = calc_log_address(last_cons_address, last_cons_slot, -1)
        while self._log_exists(address, slot):
            if self._logs[address][slot].is_consumption:
                delta1: timedelta = (
                    last_cons_timestamp - self._logs[address][slot].timestamp
                )
                self._log_interval_consumption = int(
                    delta1.total_seconds() / MINUTE_IN_SECONDS
                )
                break

            address, slot = calc_log_address(address, slot, -1)
        if (
            self._log_interval_consumption is not None
            and self._last_log_consumption_timestamp is not None
        ):
            self._next_log_consumption_timestamp = (
                self._last_log_consumption_timestamp
                + timedelta(minutes=self._log_interval_consumption)
            )

        if not self._log_production:
            return
        # Update interval of production
        last_prod_address, last_prod_slot = self._last_log_reference(
            is_consumption=False
        )
        if last_prod_address is None or last_prod_slot is None:
            return
        last_prod_timestamp = self._logs[last_prod_address][last_prod_slot].timestamp
        address, slot = calc_log_address(last_prod_address, last_prod_slot, -1)
        while self._log_exists(address, slot):
            if not self._logs[address][slot].is_consumption:
                delta2: timedelta = (
                    last_prod_timestamp - self._logs[address][slot].timestamp
                )
                self._log_interval_production = int(
                    delta2.total_seconds() / MINUTE_IN_SECONDS
                )
                break
            address, slot = calc_log_address(address, slot, -1)
        if (
            self._log_interval_production is not None
            and self._last_log_production_timestamp is not None
        ):
            self._next_log_production_timestamp = (
                self._last_log_production_timestamp
                + timedelta(minutes=self._log_interval_production)
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
        self, address: int, slot: int, timestamp: datetime, is_consumption: bool
    ) -> None:
        """Update references to last (most recent) log record."""
        if self._last_log_timestamp is None or self._last_log_timestamp < timestamp:
            self._last_log_address = address
            self._last_log_slot = slot
            self._last_log_timestamp = timestamp
        elif self._last_log_timestamp == timestamp and not is_consumption:
            self._last_log_address = address
            self._last_log_slot = slot
            self._last_log_timestamp = timestamp

    def _update_last_consumption_log_reference(
        self, address: int, slot: int, timestamp: datetime
    ) -> None:
        """Update references to last (most recent) log consumption record."""
        if (
            self._last_log_consumption_timestamp is None
            or self._last_log_consumption_timestamp <= timestamp
        ):
            self._last_log_consumption_timestamp = timestamp
            self._last_log_consumption_address = address
            self._last_log_consumption_slot = slot

    def _reset_log_references(self) -> None:  # noqa: PLR0912
        """Reset log references."""
        self._last_log_consumption_address = None
        self._last_log_consumption_slot = None
        self._last_log_consumption_timestamp = None
        self._first_log_consumption_address = None
        self._first_log_consumption_slot = None
        self._first_log_consumption_timestamp = None
        self._last_log_production_address = None
        self._last_log_production_slot = None
        self._last_log_production_timestamp = None
        self._first_log_production_address = None
        self._first_log_production_slot = None
        self._first_log_production_timestamp = None
        if self._logs is None:
            return
        for address in self._logs:
            for slot, log_record in self._logs[address].items():
                if log_record.is_consumption:
                    if self._last_log_consumption_timestamp is None:
                        self._last_log_consumption_timestamp = log_record.timestamp
                    if self._last_log_consumption_timestamp <= log_record.timestamp:
                        self._last_log_consumption_timestamp = log_record.timestamp
                        self._last_log_consumption_address = address
                        self._last_log_consumption_slot = slot

                    if self._first_log_consumption_timestamp is None:
                        self._first_log_consumption_timestamp = log_record.timestamp
                    if self._first_log_consumption_timestamp >= log_record.timestamp:
                        self._first_log_consumption_timestamp = log_record.timestamp
                        self._first_log_consumption_address = address
                        self._first_log_consumption_slot = slot
                else:
                    if self._last_log_production_timestamp is None:
                        self._last_log_production_timestamp = log_record.timestamp
                    if self._last_log_production_timestamp <= log_record.timestamp:
                        self._last_log_production_timestamp = log_record.timestamp
                        self._last_log_production_address = address
                        self._last_log_production_slot = slot

                    if self._first_log_production_timestamp is None:
                        self._first_log_production_timestamp = log_record.timestamp
                    if self._first_log_production_timestamp > log_record.timestamp:
                        self._first_log_production_timestamp = log_record.timestamp
                        self._first_log_production_address = address
                        self._first_log_production_slot = slot

    def _update_last_production_log_reference(
        self, address: int, slot: int, timestamp: datetime
    ) -> None:
        """Update references to last (most recent) log production record."""
        if (
            self._last_log_production_timestamp is None
            or self._last_log_production_timestamp <= timestamp
        ):
            self._last_log_production_timestamp = timestamp
            self._last_log_production_address = address
            self._last_log_production_slot = slot

    def _update_first_log_reference(
        self, address: int, slot: int, timestamp: datetime, is_consumption: bool
    ) -> None:
        """Update references to first (oldest) log record."""
        if self._first_log_timestamp is None or self._first_log_timestamp > timestamp:
            self._first_log_address = address
            self._first_log_slot = slot
            self._first_log_timestamp = timestamp
        elif self._first_log_timestamp == timestamp and is_consumption:
            self._first_log_address = address
            self._first_log_slot = slot
            self._first_log_timestamp = timestamp

    def _update_first_consumption_log_reference(
        self, address: int, slot: int, timestamp: datetime
    ) -> None:
        """Update references to first (oldest) log consumption record."""
        if (
            self._first_log_consumption_timestamp is None
            or self._first_log_consumption_timestamp >= timestamp
        ):
            self._first_log_consumption_timestamp = timestamp
            self._first_log_consumption_address = address
            self._first_log_consumption_slot = slot

    def _update_first_production_log_reference(
        self, address: int, slot: int, timestamp: datetime
    ) -> None:
        """Update references to first (oldest) log production record."""
        if (
            self._first_log_production_timestamp is None
            or self._first_log_production_timestamp >= timestamp
        ):
            self._first_log_production_timestamp = timestamp
            self._first_log_production_address = address
            self._first_log_production_slot = slot

    def _update_log_references(self, address: int, slot: int) -> None:
        """Update next expected log timestamps."""
        if self._logs is None:
            return

        log_timestamp = self._logs[address][slot].timestamp
        is_consumption = self._logs[address][slot].is_consumption
        # Update log references
        self._update_first_log_reference(address, slot, log_timestamp, is_consumption)
        self._update_last_log_reference(address, slot, log_timestamp, is_consumption)

        if is_consumption:
            self._update_first_consumption_log_reference(address, slot, log_timestamp)
            self._update_last_consumption_log_reference(address, slot, log_timestamp)
        elif self._log_production:
            self._update_first_production_log_reference(address, slot, log_timestamp)
            self._update_last_production_log_reference(address, slot, log_timestamp)

    @property
    def log_addresses_missing(self) -> list[int] | None:
        """Return the addresses of missing logs."""
        return self._log_addresses_missing

    def _last_log_reference(
        self, is_consumption: bool | None = None
    ) -> tuple[int | None, int | None]:
        """Address and slot of last log."""
        if is_consumption is None:
            return (self._last_log_address, self._last_log_slot)
        if is_consumption:
            return (self._last_log_consumption_address, self._last_log_consumption_slot)
        return (self._last_log_production_address, self._last_log_production_slot)

    def _first_log_reference(
        self, is_consumption: bool | None = None
    ) -> tuple[int | None, int | None]:
        """Address and slot of first log."""
        if is_consumption is None:
            return (self._first_log_address, self._first_log_slot)
        if is_consumption:
            return (
                self._first_log_consumption_address,
                self._first_log_consumption_slot,
            )
        return (self._first_log_production_address, self._first_log_production_slot)

    def _logs_missing(self, from_timestamp: datetime) -> list[int] | None:  # noqa: PLR0911 PLR0912 PLR0915
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
            "_logs_missing | %s | checking in range: first_address=%s, last_address=%s, from_timestamp=%s",
            self._mac,
            first_address,
            last_address,
            from_timestamp,
        )

        # When higher addresses contain outdated data
        if last_address < first_address and (first_address - last_address < 1000):
            _LOGGER.warning(
                "The Circle %s does not overwrite old logged data, please reset the Circle's energy-logs via Source",
                self._mac,
            )
            return None

        if (
            last_address == first_address
            and last_slot == first_slot
            and self._logs[first_address][first_slot].timestamp
            == self._logs[last_address][last_slot].timestamp
        ):
            # Power consumption logging, so we need at least 4 logs.
            return None

        # Collect any missing address in current range, within MAX_LOG_HOURS timeframe
        # The max_count-guarding has been added for when an outdated logrecord is present in the cache,
        # this will result in the unwanted collection of missing logs outside the MAX_LOG_HOURS timeframe
        address = last_address
        slot = last_slot
        count = 0
        max_count = MAX_LOG_HOURS
        if self._log_production:
            max_count = (
                2 * max_count
            )  # this requires production_interval == consumption_interval

        while not (
            (address == first_address and slot == first_slot) or count > max_count
        ):
            address, slot = calc_log_address(address, slot, -1)
            if address in missing:
                continue

            if not self._log_exists(address, slot):
                missing.append(address)
                continue

            if self._logs[address][slot].timestamp <= from_timestamp:
                break

            count += 1

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
        if self._log_interval_consumption is not None:
            log_interval = self._log_interval_consumption
        elif self._log_interval_production is not None:
            log_interval = self._log_interval_production

        if (
            self._log_interval_production is not None
            and log_interval is not None
            and self._log_interval_production < log_interval
        ):
            log_interval = self._log_interval_production

        if log_interval is None:
            return None

        # We have an suspected interval, so try to calculate missing log addresses prior to first collected log
        _LOGGER.debug(
            "_logs_missing | %s | checking before range with log_interval=%s",
            self._mac,
            log_interval,
        )
        calculated_timestamp = self._logs[first_address][
            first_slot
        ].timestamp - timedelta(minutes=log_interval)
        _LOGGER.debug(
            "_logs_missing | %s | first_empty_log_address=%s",
            self._mac,
            self._first_empty_log_address,
        )
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

        if len(self._logs) < 2:  # noqa: PLR2004
            return timedelta(hours=1)

        address, slot = self._last_log_reference()
        if address is None or slot is None:
            raise EnergyError("Unable to return last known duration without any logs")

        last_known_timestamp = self._logs[address][slot].timestamp
        address, slot = calc_log_address(address, slot, -1)
        while (
            self._log_exists(address, slot)
            and self._logs[address][slot].timestamp == last_known_timestamp
        ):
            address, slot = calc_log_address(address, slot, -1)

        if self._logs[address][slot].timestamp == last_known_timestamp:
            return timedelta(hours=1)

        return self._logs[address][slot].timestamp - last_known_timestamp

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
            calc_interval_cons = timedelta(minutes=self._log_interval_consumption)

        if not self._log_production:
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
                calc_interval_prod = timedelta(minutes=self._log_interval_production)

            expected_timestamp_cons = (
                self._logs[address][slot].timestamp - calc_interval_cons
            )
            expected_timestamp_prod = (
                self._logs[address][slot].timestamp - calc_interval_prod
            )

            address, slot = calc_log_address(address, slot, -1)
            while (
                expected_timestamp_cons > target or expected_timestamp_prod > target
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
            calc_interval_cons = timedelta(minutes=self._log_interval_consumption)

        if not self._log_production:
            expected_timestamp = (
                self._logs[address][slot].timestamp + calc_interval_cons
            )
            address, slot = calc_log_address(address, slot, 1)
            while expected_timestamp < target:
                address, slot = calc_log_address(address, slot, 1)
                expected_timestamp += calc_interval_cons
                if address not in addresses:
                    addresses.append(address)

            return addresses

        # Production logging active
        calc_interval_prod = timedelta(hours=1)
        if (
            self._log_interval_production is not None
            and self._log_interval_production > 0
        ):
            calc_interval_prod = timedelta(minutes=self._log_interval_production)

        expected_timestamp_cons = (
            self._logs[address][slot].timestamp + calc_interval_cons
        )
        expected_timestamp_prod = (
            self._logs[address][slot].timestamp + calc_interval_prod
        )
        address, slot = calc_log_address(address, slot, 1)
        while expected_timestamp_cons < target or expected_timestamp_prod < target:
            if address not in addresses:
                addresses.append(address)
            if expected_timestamp_prod < expected_timestamp_cons:
                expected_timestamp_prod += calc_interval_prod
            else:
                expected_timestamp_cons += calc_interval_cons
            address, slot = calc_log_address(address, slot, 1)
        return addresses
