"""Message property types."""

import binascii
from datetime import UTC, date, datetime, time, timedelta
import struct
from typing import Any, Final

from ..constants import LOGADDR_OFFSET, PLUGWISE_EPOCH, UTF8
from ..exceptions import MessageError
from ..helpers.util import int_to_uint

DESERIALIZE_ERROR: Final[MessageError] = MessageError(
    "Unable to return value. Deserialize data first"
)


class BaseType:
    """Generic single instance property."""

    def __init__(self, raw_value: Any, length: int) -> None:
        """Initialize single instance property."""
        self._raw_value = raw_value
        self.length = length

    def serialize(self) -> bytes:
        """Return current value into an iterable list of bytes."""
        return bytes(self._raw_value, UTF8)

    def deserialize(self, val: bytes) -> None:
        """Convert current value into single data object."""
        raise NotImplementedError()

    def __len__(self) -> int:
        """Return length of property object."""
        return self.length


class CompositeType:
    """Generic multi instance property."""

    def __init__(self) -> None:
        """Initialize multi instance property."""
        self.contents: list[
            String | Int | SInt | UnixTimestamp | Year2k | IntDec | Float | LogAddr
        ] = []

    def serialize(self) -> bytes:
        """Return current value of all properties into an iterable list of bytes."""
        return b"".join(a.serialize() for a in self.contents)

    def deserialize(self, val: bytes) -> None:
        """Convert data into multiple data objects."""
        for content in self.contents:
            _val = val[: len(content)]
            content.deserialize(_val)
            val = val[len(_val) :]

    def __len__(self) -> int:
        """Return length of property objects."""
        return sum(len(x) for x in self.contents)


class Bytes(BaseType):
    """Bytes based property."""

    def __init__(self, value: bytes | None, length: int) -> None:
        """Initialize bytes based property."""
        super().__init__(value, length)
        self._value: bytes | None = None

    def deserialize(self, val: bytes) -> None:
        """Set current value."""
        self._value = val

    @property
    def value(self) -> bytes:
        """Return bytes value."""
        if self._value is None:
            raise DESERIALIZE_ERROR
        return self._value


class String(BaseType):
    """String based property."""

    def __init__(self, value: str | None, length: int) -> None:
        """Initialize string based property."""
        super().__init__(value, length)
        self._value: str | None = None

    def deserialize(self, val: bytes) -> None:
        """Convert current value into single string formatted object."""
        self._value = val.decode(UTF8)

    @property
    def value(self) -> str:
        """Return converted int value."""
        if self._value is None:
            raise DESERIALIZE_ERROR
        return self._value


class Int(BaseType):
    """Integer based property."""

    def __init__(self, value: int, length: int = 2, negative: bool = True) -> None:
        """Initialize integer based property."""
        super().__init__(value, length)
        self.negative = negative
        self._value: int | None = None

    def serialize(self) -> bytes:
        """Return current string formatted value into an iterable list of bytes."""
        fmt = "%%0%dX" % self.length  # noqa: UP031
        return bytes(fmt % self._raw_value, UTF8)

    def deserialize(self, val: bytes) -> None:
        """Convert current value into single string formatted object."""
        self._value = int(val, 16)
        if self.negative:
            mask = 1 << (self.length * 4 - 1)
            self._value = -(self._value & mask) + (self._value & ~mask)

    @property
    def value(self) -> int:
        """Return converted int value."""
        if self._value is None:
            raise DESERIALIZE_ERROR
        return self._value


class SInt(BaseType):
    """String formatted data with integer value property."""

    def __init__(self, value: int, length: int = 2) -> None:
        """Initialize string formatted data with integer value property."""
        super().__init__(value, length)
        self._value: int | None = None

    @staticmethod
    def negative(val: int, octals: int) -> int:
        """Compute the 2's compliment of int value val for negative values."""
        bits = octals << 2
        if (val & (1 << (bits - 1))) != 0:
            val = val - (1 << bits)
        return val

    def serialize(self) -> bytes:
        """Return current string formatted integer value into an iterable list of bytes."""
        fmt = "%%0%dX" % self.length  # noqa: UP031
        return bytes(fmt % int_to_uint(self._raw_value, self.length), UTF8)

    def deserialize(self, val: bytes) -> None:
        """Convert current string formatted value into integer value."""
        # TODO: negative is not initialized! 20220405
        self._value = self.negative(int(val, 16), self.length)

    @property
    def value(self) -> int:
        """Return converted datetime value."""
        if self._value is None:
            raise DESERIALIZE_ERROR
        return self._value


class UnixTimestamp(BaseType):
    """Unix formatted timestamp property."""

    def __init__(self, value: datetime | None, length: int = 8) -> None:
        """Initialize Unix formatted timestamp property."""
        super().__init__(value, length)
        self._value: datetime | None = None

    def serialize(self) -> bytes:
        """Return current string formatted value into an iterable list of bytes."""
        if not isinstance(self._raw_value, datetime):
            raise MessageError("Unable to serialize. Value is not a datetime object")
        fmt = "%%0%dX" % self.length  # noqa: UP031
        date_in_float = self._raw_value.timestamp()
        return bytes(fmt % int(date_in_float), UTF8)

    def deserialize(self, val: bytes) -> None:
        """Convert data into datetime based on Unix timestamp format."""
        self._value = datetime.fromtimestamp(int(val, 16), UTC)

    @property
    def value(self) -> datetime:
        """Return converted datetime value."""
        if self._value is None:
            raise DESERIALIZE_ERROR
        return self._value


class Year2k(Int):
    """Year formatted property.

    Based on offset from the year 2000.
    """

    def deserialize(self, val: bytes) -> None:
        """Convert data into year valued based value with offset to Y2k."""
        super().deserialize(val)
        if self._value is not None:
            self._value += PLUGWISE_EPOCH

    @property
    def value(self) -> int:
        """Return converted int value."""
        if self._value is None:
            raise DESERIALIZE_ERROR
        return self._value


class DateTime(CompositeType):
    """Date time formatted property.

    format is: YYMMmmmm
    where year is offset value from the epoch which is Y2K
    and last four bytes are offset from the beginning of the month in minutes.
    """

    def __init__(self, year: int = 0, month: int = 1, minutes: int = 0) -> None:
        """Initialize Date time formatted property."""
        CompositeType.__init__(self)
        self.year = Year2k(year - PLUGWISE_EPOCH, 2)
        self.month = Int(month, 2, False)
        self.minutes = Int(minutes, 4, False)
        self.contents += [self.year, self.month, self.minutes]
        self._value: datetime | None = None
        self._deserialized = False

    def deserialize(self, val: bytes) -> None:
        """Convert data into datetime based on timestamp with offset to Y2k."""
        if val in (b"FFFFFFFF", b"00000000"):
            self._value = None
        else:
            CompositeType.deserialize(self, val)
            self._value = datetime(
                year=self.year.value, month=self.month.value, day=1
            ) + timedelta(minutes=self.minutes.value)
        self._deserialized = True

    @property
    def value_set(self) -> bool:
        """True when datetime is converted."""
        if not self._deserialized:
            raise DESERIALIZE_ERROR
        return self._value is not None

    @property
    def value(self) -> datetime:
        """Return converted datetime value."""
        if self._value is None:
            raise DESERIALIZE_ERROR
        return self._value


class Time(CompositeType):
    """Time formatted property."""

    def __init__(self, hour: int = 0, minute: int = 0, second: int = 0) -> None:
        """Initialize time formatted property."""
        CompositeType.__init__(self)
        self.hour = Int(hour, 2, False)
        self.minute = Int(minute, 2, False)
        self.second = Int(second, 2, False)
        self.contents += [self.hour, self.minute, self.second]
        self._value: time | None = None

    def deserialize(self, val: bytes) -> None:
        """Convert data into time value."""
        CompositeType.deserialize(self, val)
        self._value = time(self.hour.value, self.minute.value, self.second.value)

    @property
    def value(self) -> time:
        """Return converted time value."""
        if self._value is None:
            raise DESERIALIZE_ERROR
        return self._value


class IntDec(BaseType):
    """Integer as string formatted data with integer value property."""

    def __init__(self, value: int, length: int = 2) -> None:
        """Initialize integer based property."""
        super().__init__(value, length)
        self._value: str | None = None

    def serialize(self) -> bytes:
        """Return current string formatted integer value into an iterable list of bytes."""
        fmt = "%%0%dd" % self.length  # noqa: UP031
        return bytes(fmt % self._raw_value, UTF8)

    def deserialize(self, val: bytes) -> None:
        """Convert data into integer value based on string formatted data format."""
        self._value = val.decode(UTF8)

    @property
    def value(self) -> str:
        """Return converted string value."""
        if self._value is None:
            raise DESERIALIZE_ERROR
        return self._value


class RealClockTime(CompositeType):
    """Time value property based on integer values."""

    def __init__(self, hour: int = 0, minute: int = 0, second: int = 0) -> None:
        """Initialize time formatted property."""
        super().__init__()
        self.hour = IntDec(hour, 2)
        self.minute = IntDec(minute, 2)
        self.second = IntDec(second, 2)
        self.contents += [self.second, self.minute, self.hour]
        self._value: time | None = None

    def deserialize(self, val: bytes) -> None:
        """Convert data into time value based on integer formatted data."""
        CompositeType.deserialize(self, val)
        self._value = time(
            int(self.hour.value),
            int(self.minute.value),
            int(self.second.value),
        )

    @property
    def value(self) -> time:
        """Return converted time value."""
        if self._value is None:
            raise DESERIALIZE_ERROR
        return self._value


class RealClockDate(CompositeType):
    """Date value property based on integer values."""

    def __init__(self, day: int = 0, month: int = 0, year: int = 0) -> None:
        """Initialize date formatted property."""
        super().__init__()
        self.day = IntDec(day, 2)
        self.month = IntDec(month, 2)
        self.year = IntDec(year - PLUGWISE_EPOCH, 2)
        self.contents += [self.day, self.month, self.year]
        self._value: date | None = None

    def deserialize(self, val: bytes) -> None:
        """Convert data into date value based on integer formatted data."""
        CompositeType.deserialize(self, val)
        self._value = date(
            int(self.year.value) + PLUGWISE_EPOCH,
            int(self.month.value),
            int(self.day.value),
        )

    @property
    def value(self) -> date:
        """Return converted date value."""
        if self._value is None:
            raise DESERIALIZE_ERROR
        return self._value


class Float(BaseType):
    """Float value property."""

    def __init__(self, value: float, length: int = 4) -> None:
        """Initialize float value property."""
        super().__init__(value, length)
        self._value: float | None = None

    def deserialize(self, val: bytes) -> None:
        """Convert data into float value."""
        hex_val = binascii.unhexlify(val)
        self._value = float(struct.unpack("!f", hex_val)[0])

    @property
    def value(self) -> float:
        """Return converted float value."""
        if self._value is None:
            raise DESERIALIZE_ERROR
        return self._value


class LogAddr(Int):
    """Log address value property."""

    def serialize(self) -> bytes:
        """Return current log address formatted value into an iterable list of bytes."""
        return bytes("%08X" % ((self._raw_value * 32) + LOGADDR_OFFSET), UTF8)

    def deserialize(self, val: bytes) -> None:
        """Convert data into integer value based on log address formatted data."""
        if val == b"00000000":
            self._value = 0
            return
        Int.deserialize(self, val)
        self._value = (self.value - LOGADDR_OFFSET) // 32

    @property
    def value(self) -> int:
        """Return converted time value."""
        if self._value is None:
            raise DESERIALIZE_ERROR
        return self._value
