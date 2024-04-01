"""Message property types."""

import binascii
import datetime
import struct
from typing import Any

from ..constants import LOGADDR_OFFSET, PLUGWISE_EPOCH, UTF8
from ..helpers.util import int_to_uint


class BaseType:
    """Generic single instance property."""

    def __init__(self, value: Any, length: int) -> None:
        """Initialize single instance property."""
        self.value = value
        self.length = length

    def serialize(self) -> bytes:
        """Return current value into an iterable list of bytes."""
        return bytes(self.value, UTF8)

    def deserialize(self, val: bytes) -> None:
        """Convert current value into single data object."""
        self.value = val

    def __len__(self) -> int:
        """Return length of property object."""
        return self.length


class CompositeType:
    """Generic multi instance property."""

    def __init__(self) -> None:
        """Initialize multi instance property."""
        self.contents: list = []

    def serialize(self) -> bytes:
        """Return current value of all properties into an iterable list of bytes."""
        return b"".join(a.serialize() for a in self.contents)

    def deserialize(self, val: bytes) -> None:
        """Convert data into multiple data objects."""
        for content in self.contents:
            _val = val[: len(content)]
            content.deserialize(_val)
            val = val[len(_val):]

    def __len__(self) -> int:
        """Return length of property objects."""
        return sum(len(x) for x in self.contents)


class String(BaseType):
    """String based property."""


class Int(BaseType):
    """Integer based property."""

    def __init__(
        self, value: int, length: int = 2, negative: bool = True
    ) -> None:
        """Initialize integer based property."""
        super().__init__(value, length)
        self.negative = negative

    def serialize(self) -> bytes:
        """Return current string formatted value into an iterable list of bytes."""
        fmt = "%%0%dX" % self.length
        return bytes(fmt % self.value, UTF8)

    def deserialize(self, val: bytes) -> None:
        """Convert current value into single string formatted object."""
        self.value = int(val, 16)
        if self.negative:
            mask = 1 << (self.length * 4 - 1)
            self.value = -(self.value & mask) + (self.value & ~mask)


class SInt(BaseType):
    """String formatted data with integer value property."""

    def __init__(self, value: int, length: int = 2) -> None:
        """Initialize string formatted data with integer value property."""
        super().__init__(value, length)

    @staticmethod
    def negative(val: int, octals: int) -> int:
        """Compute the 2's compliment of int value val for negative values."""
        bits = octals << 2
        if (val & (1 << (bits - 1))) != 0:
            val = val - (1 << bits)
        return val

    def serialize(self) -> bytes:
        """Return current string formatted integer value into an iterable list of bytes."""
        fmt = "%%0%dX" % self.length
        return bytes(fmt % int_to_uint(self.value, self.length), UTF8)

    def deserialize(self, val: bytes) -> None:
        """Convert current string formatted value into integer value."""
        # TODO: negative is not initialized! 20220405
        self.value = self.negative(int(val, 16), self.length)


class UnixTimestamp(Int):
    """Unix formatted timestamp property."""

    def __init__(self, value: float, length: int = 8) -> None:
        """Initialize Unix formatted timestamp property."""
        Int.__init__(self, int(value), length, False)

    def deserialize(self, val: bytes) -> None:
        """Convert data into datetime based on Unix timestamp format."""
        self.value = datetime.datetime.fromtimestamp(
            int(val, 16), datetime.UTC
        )


class Year2k(Int):
    """Year formatted property.

    Based on offset from the year 2000.
    """

    def deserialize(self, val: bytes) -> None:
        """Convert data into year valued based value with offset to Y2k."""
        Int.deserialize(self, val)
        self.value += PLUGWISE_EPOCH


class DateTime(CompositeType):
    """Date time formatted property.

    format is: YYMMmmmm
    where year is offset value from the epoch which is Y2K
    and last four bytes are offset from the beginning of the month in minutes.
    """

    def __init__(
        self, year: int = 0, month: int = 1, minutes: int = 0
    ) -> None:
        """Initialize Date time formatted property."""
        CompositeType.__init__(self)
        self.year = Year2k(year - PLUGWISE_EPOCH, 2)
        self.month = Int(month, 2, False)
        self.minutes = Int(minutes, 4, False)
        self.contents += [self.year, self.month, self.minutes]
        self.value: datetime.datetime | None = None

    def deserialize(self, val: bytes) -> None:
        """Convert data into datetime based on timestamp with offset to Y2k."""
        if val == b"FFFFFFFF":
            self.value = None
        else:
            CompositeType.deserialize(self, val)
            self.value = datetime.datetime(
                year=self.year.value, month=self.month.value, day=1
            ) + datetime.timedelta(minutes=self.minutes.value)


class Time(CompositeType):
    """Time formatted property."""

    def __init__(
        self, hour: int = 0, minute: int = 0, second: int = 0
    ) -> None:
        """Initialize time formatted property."""
        CompositeType.__init__(self)
        self.hour = Int(hour, 2, False)
        self.minute = Int(minute, 2, False)
        self.second = Int(second, 2, False)
        self.contents += [self.hour, self.minute, self.second]
        self.value: datetime.time | None = None

    def deserialize(self, val: bytes) -> None:
        """Convert data into time value."""
        CompositeType.deserialize(self, val)
        self.value = datetime.time(
            self.hour.value, self.minute.value, self.second.value
        )


class IntDec(BaseType):
    """Integer as string formatted data with integer value property."""

    def __init__(self, value: int, length: int = 2) -> None:
        """Initialize integer based property."""
        super().__init__(value, length)

    def serialize(self) -> bytes:
        """Return current string formatted integer value into an iterable list of bytes."""
        fmt = "%%0%dd" % self.length
        return bytes(fmt % self.value, UTF8)

    def deserialize(self, val: bytes) -> None:
        """Convert data into integer value based on string formatted data format."""
        self.value = val.decode(UTF8)


class RealClockTime(CompositeType):
    """Time value property based on integer values."""

    def __init__(
        self, hour: int = 0, minute: int = 0, second: int = 0
    ) -> None:
        """Initialize time formatted property."""
        CompositeType.__init__(self)
        self.hour = IntDec(hour, 2)
        self.minute = IntDec(minute, 2)
        self.second = IntDec(second, 2)
        self.contents += [self.second, self.minute, self.hour]
        self.value: datetime.time | None = None

    def deserialize(self, val: bytes) -> None:
        """Convert data into time value based on integer formatted data."""
        CompositeType.deserialize(self, val)
        self.value = datetime.time(
            int(self.hour.value),
            int(self.minute.value),
            int(self.second.value),
        )


class RealClockDate(CompositeType):
    """Date value property based on integer values."""

    def __init__(self, day: int = 0, month: int = 0, year: int = 0) -> None:
        """Initialize date formatted property."""
        CompositeType.__init__(self)
        self.day = IntDec(day, 2)
        self.month = IntDec(month, 2)
        self.year = IntDec(year - PLUGWISE_EPOCH, 2)
        self.contents += [self.day, self.month, self.year]
        self.value: datetime.date | None = None

    def deserialize(self, val: bytes) -> None:
        """Convert data into date value based on integer formatted data."""
        CompositeType.deserialize(self, val)
        self.value = datetime.date(
            int(self.year.value) + PLUGWISE_EPOCH,
            int(self.month.value),
            int(self.day.value),
        )


class Float(BaseType):
    """Float value property."""

    def __init__(self, value: float, length: int = 4) -> None:
        """Initialize float value property."""
        super().__init__(value, length)

    def deserialize(self, val: bytes) -> None:
        """Convert data into float value."""
        hex_val = binascii.unhexlify(val)
        self.value = float(struct.unpack("!f", hex_val)[0])


class LogAddr(Int):
    """Log address value property."""

    def serialize(self) -> bytes:
        """Return current log address formatted value into an iterable list of bytes."""
        return bytes("%08X" % ((self.value * 32) + LOGADDR_OFFSET), UTF8)

    def deserialize(self, val: bytes) -> None:
        """Convert data into integer value based on log address formatted data."""
        Int.deserialize(self, val)
        self.value = (self.value - LOGADDR_OFFSET) // 32
