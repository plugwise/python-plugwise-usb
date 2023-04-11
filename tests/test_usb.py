# pylint: disable=protected-access
"""Test Plugwise Stick features."""

import importlib

pw_constants = importlib.import_module("plugwise_usb.constants")
pw_exceptions = importlib.import_module("plugwise_usb.exceptions")
pw_stick = importlib.import_module("plugwise_usb")


# No tests available
class TestPlugwise:  # pylint: disable=attribute-defined-outside-init
    """Tests for Plugwise USB."""

    async def test_connect_legacy_anna(self):
        """No tests available."""
        assert True
