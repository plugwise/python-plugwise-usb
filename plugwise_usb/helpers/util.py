"""Plugwise utility helpers."""
from __future__ import annotations

import re

import crcmod

from ..constants import HW_MODELS

crc_fun = crcmod.mkCrcFun(0x11021, rev=False, initCrc=0x0000, xorOut=0x0000)


def validate_mac(mac: str) -> bool:
    """Validate the supplied string is in a MAC address format."""
    if not re.match("^[A-F0-9]+$", mac):
        return False
    try:
        _ = int(mac, 16)
    except ValueError:
        return False
    return True


def version_to_model(version: str | None) -> str:
    """Translate hardware_version to device type."""
    if version is None:
        return "Unknown"
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
    """Compute the 2's compliment of int value val for negative values."""
    bits = octals << 2
    if (val & (1 << (bits - 1))) != 0:
        val = val - (1 << bits)
    return val


# octals (and hex) type as int according to
# https://docs.python.org/3/library/stdtypes.html
def int_to_uint(val: int, octals: int) -> int:
    """Compute the 2's compliment of int value val for negative values."""
    bits = octals << 2
    if val < 0:
        val = val + (1 << bits)
    return val
