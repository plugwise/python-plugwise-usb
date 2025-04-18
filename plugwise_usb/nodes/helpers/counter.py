from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum, auto
import logging
from typing import Final

from .pulses import PulseCollection, PulseLogRecord
from ..helpers import EnergyCalibration
from ...api import EnergyStatistics
from ...constants import HOUR_IN_SECONDS, LOCAL_TIMEZONE, PULSES_PER_KW_SECOND
from ...exceptions import EnergyError


class EnergyType(Enum):
    """Energy collection types"""
    CONSUMPTION_HOUR = auto()
    PRODUCTION_HOUR = auto()
    CONSUMPTION_DAY = auto()
    PRODUCTION_DAY = auto()
    CONSUMPTION_WEEK = auto()
    PRODUCTION_WEEK = auto()


ENERGY_COUNTERS: Final = (
    EnergyType.CONSUMPTION_HOUR,
    EnergyType.PRODUCTION_HOUR,
    EnergyType.CONSUMPTION_DAY,
    EnergyType.PRODUCTION_DAY,
    EnergyType.CONSUMPTION_WEEK,
    EnergyType.PRODUCTION_WEEK,
)
ENERGY_HOUR_COUNTERS: Final = (
    EnergyType.CONSUMPTION_HOUR,
    EnergyType.PRODUCTION_HOUR,
)
ENERGY_DAY_COUNTERS: Final = (
    EnergyType.CONSUMPTION_DAY,
    EnergyType.PRODUCTION_DAY,
)
ENERGY_WEEK_COUNTERS: Final = (
    EnergyType.CONSUMPTION_WEEK,
    EnergyType.PRODUCTION_WEEK,
)

ENERGY_CONSUMPTION_COUNTERS: Final = (
    EnergyType.CONSUMPTION_HOUR,
    EnergyType.CONSUMPTION_DAY,
    EnergyType.CONSUMPTION_WEEK,
)
ENERGY_PRODUCTION_COUNTERS: Final = (
    EnergyType.PRODUCTION_HOUR,
    EnergyType.PRODUCTION_DAY,
    EnergyType.PRODUCTION_WEEK,
)

_LOGGER = logging.getLogger(__name__)


class EnergyCounters:
    """
    Class to hold all energy counters.
    """

    def __init__(self, mac: str) -> None:
        """Initialize EnergyCounter class."""
        self._mac = mac
        self._calibration: EnergyCalibration | None = None
        self._counters: dict[EnergyType, EnergyCounter] = {}
        for energy_type in ENERGY_COUNTERS:
            self._counters[energy_type] = EnergyCounter(energy_type)
        self._pulse_collection = PulseCollection(mac)
        self._energy_statistics = EnergyStatistics()

    @property
    def collected_logs(self) -> int:
        """Total collected logs"""
        return self._pulse_collection.collected_logs

    def add_pulse_log(
        self,
        address: int,
        slot: int,
        timestamp: datetime,
        pulses: int,
        import_only: bool = False
    ) -> None:
        """Add pulse log"""
        if self._pulse_collection.add_log(
            address,
            slot,
            timestamp,
            pulses,
            import_only
        ):
            if not import_only:
                self.update()

    def get_pulse_logs(self) -> dict[int, dict[int, PulseLogRecord]]:
        """Return currently collected pulse logs"""
        return self._pulse_collection.logs

    def add_pulse_stats(
        self, pulses_consumed: int, pulses_produced: int, timestamp: datetime
    ) -> None:
        """Add pulse statistics"""
        _LOGGER.debug(
            "add_pulse_stats | consumed=%s, for %s",
            str(pulses_consumed),
            self._mac,
        )
        self._pulse_collection.update_pulse_counter(
            pulses_consumed, pulses_produced, timestamp
        )
        self.update()

    @property
    def energy_statistics(self) -> EnergyStatistics:
        """Return collection with energy statistics."""
        return self._energy_statistics

    @property
    def consumption_interval(self) -> int | None:
        """Measurement interval for energy consumption."""
        return self._pulse_collection.log_interval_consumption

    @property
    def production_interval(self) -> int | None:
        """Measurement interval for energy production."""
        return self._pulse_collection.log_interval_production

    @property
    def log_addresses_missing(self) -> list[int] | None:
        """Return list of addresses of energy logs"""
        return self._pulse_collection.log_addresses_missing

    @property
    def log_rollover(self) -> bool:
        """Indicate if new log is required due to rollover."""
        return self._pulse_collection.log_rollover

    @property
    def calibration(self) -> EnergyCalibration | None:
        """Energy calibration configuration."""
        return self._calibration

    @calibration.setter
    def calibration(self, calibration: EnergyCalibration) -> None:
        """Energy calibration configuration."""
        for node_event in ENERGY_COUNTERS:
            self._counters[node_event].calibration = calibration
        self._calibration = calibration

    def update(self) -> None:
        """Update counter collection"""
        if self._calibration is None:
            return
        (
            self._energy_statistics.hour_consumption,
            self._energy_statistics.hour_consumption_reset,
        ) = self._counters[EnergyType.CONSUMPTION_HOUR].update(
            self._pulse_collection
        )
        (
            self._energy_statistics.day_consumption,
            self._energy_statistics.day_consumption_reset,
        ) = self._counters[EnergyType.CONSUMPTION_DAY].update(
            self._pulse_collection
        )
        (
            self._energy_statistics.week_consumption,
            self._energy_statistics.week_consumption_reset,
        ) = self._counters[EnergyType.CONSUMPTION_WEEK].update(
            self._pulse_collection
        )

        (
            self._energy_statistics.hour_production,
            self._energy_statistics.hour_production_reset,
        ) = self._counters[EnergyType.PRODUCTION_HOUR].update(
            self._pulse_collection
        )
        (
            self._energy_statistics.day_production,
            self._energy_statistics.day_production_reset,
        ) = self._counters[EnergyType.PRODUCTION_DAY].update(
            self._pulse_collection
        )
        (
            self._energy_statistics.week_production,
            self._energy_statistics.week_production_reset,
        ) = self._counters[EnergyType.PRODUCTION_WEEK].update(
            self._pulse_collection
        )
        self._pulse_collection.recalculate_missing_log_addresses()

    @property
    def timestamp(self) -> datetime | None:
        """Return the last valid timestamp or None"""
        if self._calibration is None:
            return None
        if self._pulse_collection.log_addresses_missing is None:
            return None
        if len(self._pulse_collection.log_addresses_missing) > 0:
            return None
        return self._pulse_collection.last_update


class EnergyCounter:
    """
    Energy counter to convert pulses into energy
    """

    def __init__(
        self,
        energy_id: EnergyType,
    ) -> None:
        """Initialize energy counter based on energy id."""
        if energy_id not in ENERGY_COUNTERS:
            raise EnergyError(
                f"Invalid energy id '{energy_id}' for Energy counter"
            )
        self._calibration: EnergyCalibration | None = None
        self._duration = "hour"
        if energy_id in ENERGY_DAY_COUNTERS:
            self._duration = "day"
        elif energy_id in ENERGY_WEEK_COUNTERS:
            self._duration = "week"
        self._energy_id: EnergyType = energy_id
        self._is_consumption = True
        self._direction = "consumption"
        if self._energy_id in ENERGY_PRODUCTION_COUNTERS:
            self._direction = "production"
            self._is_consumption = False
        self._last_reset: datetime | None = None
        self._last_update: datetime | None = None
        self._pulses: int | None = None

    @property
    def direction(self) -> str:
        """Energy direction (consumption or production)"""
        return self._direction

    @property
    def duration(self) -> str:
        """Energy timespan"""
        return self._duration

    @property
    def calibration(self) -> EnergyCalibration | None:
        """Energy calibration configuration."""
        return self._calibration

    @calibration.setter
    def calibration(self, calibration: EnergyCalibration) -> None:
        """Energy calibration configuration."""
        self._calibration = calibration

    @property
    def is_consumption(self) -> bool:
        """Indicate the energy direction."""
        return self._is_consumption

    @property
    def energy(self) -> float | None:
        """Total energy (in kWh) since last reset."""
        if self._pulses is None or self._calibration is None:
            return None
        if self._pulses == 0:
            return 0.0
        pulses_per_s = self._pulses / float(HOUR_IN_SECONDS)
        corrected_pulses = HOUR_IN_SECONDS * (
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
        calc_value = corrected_pulses / PULSES_PER_KW_SECOND / HOUR_IN_SECONDS
        # Fix minor miscalculations?
        if -0.001 < calc_value < 0.001:
            calc_value = 0.0
        return calc_value

    @property
    def last_reset(self) -> datetime | None:
        """Last reset of energy counter."""
        return self._last_reset

    @property
    def last_update(self) -> datetime | None:
        """Last update of energy counter."""
        return self._last_update

    def update(
        self, pulse_collection: PulseCollection
    ) -> tuple[float | None, datetime | None]:
        """Get pulse update"""
        last_reset = datetime.now(tz=LOCAL_TIMEZONE)
        if self._energy_id in ENERGY_HOUR_COUNTERS:
            last_reset = last_reset.replace(minute=0, second=0, microsecond=0)
        elif self._energy_id in ENERGY_DAY_COUNTERS:
            last_reset = last_reset.replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        elif self._energy_id in ENERGY_WEEK_COUNTERS:
            last_reset = last_reset - timedelta(days=last_reset.weekday())
            last_reset = last_reset.replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )

        pulses, last_update = pulse_collection.collected_pulses(
            last_reset, self._is_consumption
        )
        _LOGGER.debug(
            "collected_pulses : pulses=%s | last_update=%s",
            pulses,
            last_update,
        )
        if pulses is None or last_update is None:
            return (None, None)
        self._last_update = last_update
        self._last_reset = last_reset
        self._pulses = pulses

        energy = self.energy
        _LOGGER.debug("energy=%s or last_update=%s", energy, last_update)
        return (energy, last_reset)
