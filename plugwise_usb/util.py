"""
Use of this source code is governed by the MIT license found
in the LICENSE file.

Plugwise protocol helpers
"""
from __future__ import annotations

import binascii
import datetime
import os
import re
import struct
from typing import Any

import crcmod

from .constants import (
    CACHE_DIR,
    HW_MODELS,
    LOGADDR_OFFSET,
    PLUGWISE_EPOCH,
    UTF8,
)



crc_fun = crcmod.mkCrcFun(0x11021, rev=False, initCrc=0x0000, xorOut=0x0000)


def validate_mac(mac: str) -> bool:
    if not re.match("^[A-F0-9]+$", mac):
        return False
    try:
        _ = int(mac, 16)
    except ValueError:
        return False
    return True


def version_to_model(version: str | None) -> str | None:
    """Translate hardware_version to device type."""
    if version is None:
        return None

    model = HW_MODELS.get(version)
    if model is None:
        model = HW_MODELS.get(version[4:10])
    if model is None:
        # Try again with reversed order
        model = HW_MODELS.get(version[-2:] + version[-4:-2] + version[-6:-4])

    return model if model is not None else "Unknown"


# octals (and hex) type as int according to
# https://docs.python.org/3/library/stdtypes.html
def uint_to_int(val: int, octals: int) -> int:
    """Compute the 2's compliment of int value val for negative values"""
    bits = octals << 2
    if (val & (1 << (bits - 1))) != 0:
        val = val - (1 << bits)
    return val


# octals (and hex) type as int according to
# https://docs.python.org/3/library/stdtypes.html
def int_to_uint(val: int, octals: int) -> int:
    """Compute the 2's compliment of int value val for negative values"""
    bits = octals << 2
    if val < 0:
        val = val + (1 << bits)
    return val


class BaseType:
    def __init__(self, value: Any, length: int) -> None:
        self.value = value
        self.length = length

    def serialize(self) -> bytes:
        return bytes(self.value, UTF8)

    def deserialize(self, val: bytes) -> None:
        self.value = val

    def __len__(self) -> int:
        return self.length


class CompositeType:
    def __init__(self) -> None:
        self.contents: list = []

    def serialize(self) -> bytes:
        return b"".join(a.serialize() for a in self.contents)

    def deserialize(self, val: bytes) -> None:
        for content in self.contents:
            myval = val[: len(content)]
            content.deserialize(myval)
            val = val[len(myval):]

    def __len__(self) -> int:
        return sum(len(x) for x in self.contents)


class String(BaseType):
    pass


class Int(BaseType):
    def __init__(
        self, value: int, length: int = 2, negative: bool = True
    ) -> None:
        super().__init__(value, length)
        self.negative = negative

    def serialize(self) -> bytes:
        fmt = "%%0%dX" % self.length
        return bytes(fmt % self.value, UTF8)

    def deserialize(self, val: bytes) -> None:
        self.value = int(val, 16)
        if self.negative:
            mask = 1 << (self.length * 4 - 1)
            self.value = -(self.value & mask) + (self.value & ~mask)


class SInt(BaseType):
    def __init__(self, value: int, length: int = 2) -> None:
        super().__init__(value, length)

    @staticmethod
    def negative(val: int, octals: int) -> int:
        """compute the 2's compliment of int value val for negative values"""
        bits = octals << 2
        if (val & (1 << (bits - 1))) != 0:
            val = val - (1 << bits)
        return val

    def serialize(self) -> bytes:
        fmt = "%%0%dX" % self.length
        return bytes(fmt % int_to_uint(self.value, self.length), UTF8)

    def deserialize(self, val: bytes) -> None:
        # TODO: negative is not initialized! 20220405
        self.value = self.negative(int(val, 16), self.length)


class UnixTimestamp(Int):
    def __init__(self, value: float, length: int = 8) -> None:
        Int.__init__(self, int(value), length, False)

    def deserialize(self, val: bytes) -> None:
        self.value = datetime.datetime.fromtimestamp(
            int(val, 16), datetime.UTC
        )


class Year2k(Int):
    """year value that is offset from the year 2000"""

    def deserialize(self, val: bytes) -> None:
        Int.deserialize(self, val)
        self.value += PLUGWISE_EPOCH


class DateTime(CompositeType):
    """datetime value as used in the general info response
    format is: YYMMmmmm
    where year is offset value from the epoch which is Y2K
    and last four bytes are offset from the beginning of the month in minutes
    """

    def __init__(
        self, year: int = 0, month: int = 1, minutes: int = 0
    ) -> None:
        CompositeType.__init__(self)
        self.year = Year2k(year - PLUGWISE_EPOCH, 2)
        self.month = Int(month, 2, False)
        self.minutes = Int(minutes, 4, False)
        self.contents += [self.year, self.month, self.minutes]
        self.value: datetime.datetime | None = None

    def deserialize(self, val: bytes) -> None:
        if val == b"FFFFFFFF":
            self.value = None
        else:
            CompositeType.deserialize(self, val)
            self.value = datetime.datetime(
                year=self.year.value, month=self.month.value, day=1
            ) + datetime.timedelta(minutes=self.minutes.value)


class Time(CompositeType):
    """time value as used in the clock info response"""

    def __init__(
        self, hour: int = 0, minute: int = 0, second: int = 0
    ) -> None:
        CompositeType.__init__(self)
        self.hour = Int(hour, 2, False)
        self.minute = Int(minute, 2, False)
        self.second = Int(second, 2, False)
        self.contents += [self.hour, self.minute, self.second]
        self.value: datetime.time | None = None

    def deserialize(self, val: bytes) -> None:
        CompositeType.deserialize(self, val)
        self.value = datetime.time(
            self.hour.value, self.minute.value, self.second.value
        )


class IntDec(BaseType):
    def __init__(self, value: int, length: int = 2) -> None:
        super().__init__(value, length)

    def serialize(self) -> bytes:
        fmt = "%%0%dd" % self.length
        return bytes(fmt % self.value, UTF8)

    def deserialize(self, val: bytes) -> None:
        self.value = val.decode(UTF8)


class RealClockTime(CompositeType):
    """time value as used in the realtime clock info response"""

    def __init__(
        self, hour: int = 0, minute: int = 0, second: int = 0
    ) -> None:
        CompositeType.__init__(self)
        self.hour = IntDec(hour, 2)
        self.minute = IntDec(minute, 2)
        self.second = IntDec(second, 2)
        self.contents += [self.second, self.minute, self.hour]
        self.value: datetime.time | None = None

    def deserialize(self, val: bytes) -> None:
        CompositeType.deserialize(self, val)
        self.value = datetime.time(
            int(self.hour.value),
            int(self.minute.value),
            int(self.second.value),
        )


class RealClockDate(CompositeType):
    """date value as used in the realtime clock info response"""

    def __init__(self, day: int = 0, month: int = 0, year: int = 0) -> None:
        CompositeType.__init__(self)
        self.day = IntDec(day, 2)
        self.month = IntDec(month, 2)
        self.year = IntDec(year - PLUGWISE_EPOCH, 2)
        self.contents += [self.day, self.month, self.year]
        self.value: datetime.date | None = None

    def deserialize(self, val: bytes) -> None:
        CompositeType.deserialize(self, val)
        self.value = datetime.date(
            int(self.year.value) + PLUGWISE_EPOCH,
            int(self.month.value),
            int(self.day.value),
        )


class Float(BaseType):
    def __init__(self, value: float, length: int = 4) -> None:
        super().__init__(value, length)

    def deserialize(self, val: bytes) -> None:
        hexval = binascii.unhexlify(val)
        self.value = float(struct.unpack("!f", hexval)[0])


class LogAddr(Int):
    def serialize(self) -> bytes:
        return bytes("%08X" % ((self.value * 32) + LOGADDR_OFFSET), UTF8)

    def deserialize(self, val: bytes) -> None:
        Int.deserialize(self, val)
        self.value = (self.value - LOGADDR_OFFSET) // 32
