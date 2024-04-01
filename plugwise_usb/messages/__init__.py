"""Plugwise messages."""

from __future__ import annotations

from typing import Any

from ..constants import MESSAGE_FOOTER, MESSAGE_HEADER, UTF8
from ..helpers.util import crc_fun


class PlugwiseMessage:
    """Plugwise message base class."""

    def __init__(self, identifier: bytes) -> None:
        """Initialize a plugwise message."""
        self._identifier = identifier
        self._mac: bytes | None = None
        self._checksum: bytes | None = None
        self._args: list[Any] = []
        self._seq_id: bytes | None = None

    @property
    def seq_id(self) -> bytes | None:
        """Return sequence id assigned to this request."""
        return self._seq_id

    @seq_id.setter
    def seq_id(self, seq_id: bytes) -> None:
        """Assign sequence id."""
        self._seq_id = seq_id

    @property
    def identifier(self) -> bytes:
        """Return the message ID."""
        return self._identifier

    @property
    def mac(self) -> bytes:
        """Return mac in bytes."""
        return self._mac

    @property
    def mac_decoded(self) -> str:
        """Return mac in decoded string format."""
        if self._mac is None:
            return "not defined"
        return self._mac.decode(UTF8)

    def serialize(self) -> bytes:
        """Return message in a serialized format that can be sent out."""
        data = self._identifier
        if self._mac is not None:
            data += self._mac
        data += b"".join(a.serialize() for a in self._args)
        self._checksum = self.calculate_checksum(data)
        return MESSAGE_HEADER + data + self._checksum + MESSAGE_FOOTER

    @staticmethod
    def calculate_checksum(data: bytes) -> bytes:
        """Calculate crc checksum."""
        return bytes("%04X" % crc_fun(data), UTF8)
