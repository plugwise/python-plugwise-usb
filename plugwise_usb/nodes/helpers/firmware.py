"""Firmware protocol support definitions.

The minimum and maximum supported (custom) zigbee protocol versions
are based on the utc timestamp of firmware.

The data is extracted from analyzing the "Plugwise.IO.dll" file of
the Plugwise source installation.

"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final, NamedTuple

from ...api import NodeFeature


class SupportedVersions(NamedTuple):
    """Range of supported version."""

    min: float
    max: float


# region - node firmware versions
CIRCLE_FIRMWARE_SUPPORT: Final = {
    datetime(2008, 8, 26, 15, 46, tzinfo=UTC): SupportedVersions(min=1.0, max=1.1),
    datetime(2009, 9, 8, 13, 50, 31, tzinfo=UTC): SupportedVersions(min=2.0, max=2.4),
    datetime(2010, 4, 27, 11, 56, 23, tzinfo=UTC): SupportedVersions(min=2.0, max=2.4),
    datetime(2010, 8, 4, 14, 9, 6, tzinfo=UTC): SupportedVersions(min=2.0, max=2.4),
    datetime(2010, 8, 17, 7, 40, 37, tzinfo=UTC): SupportedVersions(min=2.0, max=2.4),
    datetime(2010, 8, 31, 5, 55, 19, tzinfo=UTC): SupportedVersions(min=2.0, max=2.5),
    datetime(2010, 8, 31, 10, 21, 2, tzinfo=UTC): SupportedVersions(min=2.0, max=2.4),
    datetime(2010, 10, 7, 14, 46, 38, tzinfo=UTC): SupportedVersions(min=2.0, max=2.4),
    datetime(2010, 11, 1, 13, 29, 38, tzinfo=UTC): SupportedVersions(min=2.0, max=2.4),
    datetime(2011, 3, 25, 17, 40, 20, tzinfo=UTC): SupportedVersions(min=2.0, max=2.5),
    datetime(2011, 5, 13, 7, 19, 23, tzinfo=UTC): SupportedVersions(min=2.0, max=2.5),
    datetime(2011, 6, 27, 8, 52, 18, tzinfo=UTC): SupportedVersions(min=2.0, max=2.5),
    # Legrand
    datetime(2011, 11, 3, 12, 57, 57, tzinfo=UTC): SupportedVersions(min=2.0, max=2.6),
    # Radio Test
    datetime(2012, 4, 19, 14, 0, 42, tzinfo=UTC): SupportedVersions(min=2.0, max=2.5),
    # Beta release
    datetime(2015, 6, 18, 14, 42, 54, tzinfo=UTC): SupportedVersions(min=2.0, max=2.6),
    # Proto release
    datetime(2015, 6, 16, 21, 9, 10, tzinfo=UTC): SupportedVersions(min=2.0, max=2.6),
    datetime(2015, 6, 18, 14, 0, 54, tzinfo=UTC): SupportedVersions(min=2.0, max=2.6),
    datetime(2015, 9, 18, 8, 53, 15, tzinfo=UTC): SupportedVersions(min=2.0, max=2.5),
    # New Flash Update
    datetime(2017, 7, 11, 16, 6, 59, tzinfo=UTC): SupportedVersions(min=2.0, max=2.6),
}

CIRCLE_PLUS_FIRMWARE_SUPPORT: Final = {
    datetime(2008, 8, 26, 15, 46, tzinfo=UTC): SupportedVersions(min=1.0, max=1.1),
    datetime(2009, 9, 8, 14, 0, 32, tzinfo=UTC): SupportedVersions(min=2.0, max=2.4),
    datetime(2010, 4, 27, 11, 54, 15, tzinfo=UTC): SupportedVersions(min=2.0, max=2.4),
    datetime(2010, 8, 4, 12, 56, 59, tzinfo=UTC): SupportedVersions(min=2.0, max=2.4),
    datetime(2010, 8, 17, 7, 37, 57, tzinfo=UTC): SupportedVersions(min=2.0, max=2.4),
    datetime(2010, 8, 31, 10, 9, 18, tzinfo=UTC): SupportedVersions(min=2.0, max=2.4),
    datetime(2010, 10, 7, 14, 49, 29, tzinfo=UTC): SupportedVersions(min=2.0, max=2.4),
    datetime(2010, 11, 1, 13, 24, 49, tzinfo=UTC): SupportedVersions(min=2.0, max=2.4),
    datetime(2011, 3, 25, 17, 37, 55, tzinfo=UTC): SupportedVersions(min=2.0, max=2.5),
    datetime(2011, 5, 13, 7, 17, 7, tzinfo=UTC): SupportedVersions(min=2.0, max=2.5),
    datetime(2011, 6, 27, 8, 47, 37, tzinfo=UTC): SupportedVersions(min=2.0, max=2.5),
    # Legrand
    datetime(2011, 11, 3, 12, 55, 23, tzinfo=UTC): SupportedVersions(min=2.0, max=2.6),
    # Radio Test
    datetime(2012, 4, 19, 14, 3, 55, tzinfo=UTC): SupportedVersions(min=2.0, max=2.5),
    # SMA firmware 2015-06-16
    datetime(2015, 6, 18, 14, 42, 54, tzinfo=UTC): SupportedVersions(min=2.0, max=2.6),
    # New Flash Update
    datetime(2017, 7, 11, 16, 5, 57, tzinfo=UTC): SupportedVersions(min=2.0, max=2.6),
}

SCAN_FIRMWARE_SUPPORT: Final = {
    datetime(2010, 11, 4, 16, 58, 46, tzinfo=UTC): SupportedVersions(min=2.0, max=2.6),
    # Beta Scan Release
    datetime(2011, 1, 12, 8, 32, 56, tzinfo=UTC): SupportedVersions(min=2.0, max=2.5),
    # Beta Scan Release
    datetime(2011, 3, 4, 14, 43, 31, tzinfo=UTC): SupportedVersions(min=2.0, max=2.5),
    # Scan RC1
    datetime(2011, 3, 28, 9, 0, 24, tzinfo=UTC): SupportedVersions(min=2.0, max=2.5),
    datetime(2011, 5, 13, 7, 21, 55, tzinfo=UTC): SupportedVersions(min=2.0, max=2.5),
    datetime(2011, 11, 3, 13, 0, 56, tzinfo=UTC): SupportedVersions(min=2.0, max=2.6),
    # Legrand
    datetime(2011, 6, 27, 8, 55, 44, tzinfo=UTC): SupportedVersions(min=2.0, max=2.5),
    datetime(2017, 7, 11, 16, 8, 3, tzinfo=UTC): SupportedVersions(min=2.0, max=2.6),
    # New Flash Update
}

SENSE_FIRMWARE_SUPPORT: Final = {
    # pre - internal test release - fixed version
    datetime(2010, 12, 3, 10, 17, 7, tzinfo=UTC): SupportedVersions(min=2.0, max=2.5),
    # Proto release, with reset and join bug fixed
    datetime(2011, 1, 11, 14, 19, 36, tzinfo=UTC): SupportedVersions(min=2.0, max=2.5),
    datetime(2011, 3, 4, 14, 52, 30, tzinfo=UTC): SupportedVersions(min=2.0, max=2.5),
    datetime(2011, 3, 25, 17, 43, 2, tzinfo=UTC): SupportedVersions(min=2.0, max=2.5),
    datetime(2011, 5, 13, 7, 24, 26, tzinfo=UTC): SupportedVersions(min=2.0, max=2.5),
    datetime(2011, 6, 27, 8, 58, 19, tzinfo=UTC): SupportedVersions(min=2.0, max=2.5),
    # Legrand
    datetime(2011, 11, 3, 13, 7, 33, tzinfo=UTC): SupportedVersions(min=2.0, max=2.6),
    # Radio Test
    datetime(2012, 4, 19, 14, 10, 48, tzinfo=UTC): SupportedVersions(min=2.0, max=2.5),
    # New Flash Update
    datetime(2017, 7, 11, 16, 9, 5, tzinfo=UTC): SupportedVersions(min=2.0, max=2.6),
}

SWITCH_FIRMWARE_SUPPORT: Final = {
    datetime(2009, 9, 8, 14, 7, 4, tzinfo=UTC): SupportedVersions(min=2.0, max=2.4),
    datetime(2010, 1, 16, 14, 7, 13, tzinfo=UTC): SupportedVersions(min=2.0, max=2.4),
    datetime(2010, 4, 27, 11, 59, 31, tzinfo=UTC): SupportedVersions(min=2.0, max=2.4),
    datetime(2010, 8, 4, 14, 15, 25, tzinfo=UTC): SupportedVersions(min=2.0, max=2.4),
    datetime(2010, 8, 17, 7, 44, 24, tzinfo=UTC): SupportedVersions(min=2.0, max=2.4),
    datetime(2010, 8, 31, 10, 23, 32, tzinfo=UTC): SupportedVersions(min=2.0, max=2.4),
    datetime(2010, 10, 7, 14, 29, 55, tzinfo=UTC): SupportedVersions(min=2.0, max=2.4),
    datetime(2010, 11, 1, 13, 41, 30, tzinfo=UTC): SupportedVersions(min=2.0, max=2.4),
    datetime(2011, 3, 25, 17, 46, 41, tzinfo=UTC): SupportedVersions(min=2.0, max=2.5),
    datetime(2011, 5, 13, 7, 26, 54, tzinfo=UTC): SupportedVersions(min=2.0, max=2.5),
    datetime(2011, 6, 27, 9, 4, 10, tzinfo=UTC): SupportedVersions(min=2.0, max=2.5),
    # Legrand
    datetime(2011, 11, 3, 13, 10, 18, tzinfo=UTC): SupportedVersions(min=2.0, max=2.6),
    # Radio Test
    datetime(2012, 4, 19, 14, 10, 48, tzinfo=UTC): SupportedVersions(min=2.0, max=2.5),
    # New Flash Update
    datetime(2017, 7, 11, 16, 11, 10, tzinfo=UTC): SupportedVersions(min=2.0, max=2.6),
}

CELSIUS_FIRMWARE_SUPPORT: Final = {
    # Celsius Proto
    datetime(2013, 9, 25, 15, 9, 44, tzinfo=UTC): SupportedVersions(min=2.0, max=2.6),
    datetime(2013, 10, 11, 15, 15, 58, tzinfo=UTC): SupportedVersions(min=2.0, max=2.6),
    datetime(2013, 10, 17, 10, 13, 12, tzinfo=UTC): SupportedVersions(min=2.0, max=2.6),
    datetime(2013, 11, 19, 17, 35, 48, tzinfo=UTC): SupportedVersions(min=2.0, max=2.6),
    datetime(2013, 12, 5, 16, 25, 33, tzinfo=UTC): SupportedVersions(min=2.0, max=2.6),
    datetime(2013, 12, 11, 10, 53, 55, tzinfo=UTC): SupportedVersions(min=2.0, max=2.6),
    datetime(2014, 1, 30, 8, 56, 21, tzinfo=UTC): SupportedVersions(min=2.0, max=2.6),
    datetime(2014, 2, 3, 10, 9, 27, tzinfo=UTC): SupportedVersions(min=2.0, max=2.6),
    datetime(2014, 3, 7, 16, 7, 42, tzinfo=UTC): SupportedVersions(min=2.0, max=2.6),
    datetime(2014, 3, 24, 11, 12, 23, tzinfo=UTC): SupportedVersions(min=2.0, max=2.6),
    # MSPBootloader Image - Required to allow
    # a MSPBootload image for OTA update
    datetime(2014, 4, 14, 15, 45, 26, tzinfo=UTC): SupportedVersions(min=2.0, max=2.6),
    # CelsiusV Image
    datetime(2014, 7, 23, 19, 24, 18, tzinfo=UTC): SupportedVersions(min=2.0, max=2.6),
    # CelsiusV Image
    datetime(2014, 9, 12, 11, 36, 40, tzinfo=UTC): SupportedVersions(min=2.0, max=2.6),
    # New Flash Update
    datetime(2017, 7, 11, 16, 2, 50, tzinfo=UTC): SupportedVersions(min=2.0, max=2.6),
}

# endregion

# region - node firmware based features

FEATURE_SUPPORTED_AT_FIRMWARE: Final = {
    NodeFeature.BATTERY: 2.0,
    NodeFeature.CIRCLE: 2.0,
    NodeFeature.CIRCLEPLUS: 2.0,
    NodeFeature.INFO: 2.0,
    NodeFeature.SENSE: 2.0,
    NodeFeature.TEMPERATURE: 2.0,
    NodeFeature.HUMIDITY: 2.0,
    NodeFeature.ENERGY: 2.0,
    NodeFeature.POWER: 2.0,
    NodeFeature.RELAY: 2.0,
    NodeFeature.RELAY_INIT: 2.6,
    NodeFeature.RELAY_LOCK: 2.0,
    NodeFeature.MOTION: 2.0,
    NodeFeature.MOTION_CONFIG: 2.0,
    NodeFeature.SWITCH: 2.0,
}

# endregion
