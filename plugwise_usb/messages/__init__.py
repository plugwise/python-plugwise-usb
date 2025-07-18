"""Plugwise messages."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from ..constants import MESSAGE_FOOTER, MESSAGE_HEADER, UTF8
from ..exceptions import MessageError
from ..helpers.util import crc_fun


class Priority(Enum):
    """Message priority levels for USB-stick message requests."""

    CANCEL = 0
    HIGH = 1
    MEDIUM = 2
    LOW = 3


class PlugwiseMessage:
    """Plugwise message base class."""

    _identifier = b"FFFF"

    def __init__(self) -> None:
        """Initialize a plugwise message."""
        self._mac: bytes | None = None
        self._checksum: bytes | None = None
        self._args: list[Any] = []
        self._seq_id: bytes | None = None
        self.priority: Priority = Priority.MEDIUM
        self.timestamp = datetime.now(tz=UTC)

    @property
    def seq_id(self) -> bytes | None:
        """Return sequence id."""
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
        if self._mac is None:
            raise MessageError("Mac not set")
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
        return bytes(f"{crc_fun(data):04X}", UTF8)

    def __gt__(self, other: PlugwiseMessage) -> bool:
        """Greater than."""
        if self.priority.value == other.priority.value:
            if self.seq_id is not None and other.seq_id is not None:
                return self.seq_id < other.seq_id
            return self.timestamp > other.timestamp
        return self.priority.value < other.priority.value

    def __lt__(self, other: PlugwiseMessage) -> bool:
        """Less than."""
        if self.priority.value == other.priority.value:
            if self.seq_id is not None and other.seq_id is not None:
                return self.seq_id > other.seq_id
            return self.timestamp < other.timestamp
        return self.priority.value > other.priority.value

    def __ge__(self, other: PlugwiseMessage) -> bool:
        """Greater than or equal."""
        if self.priority.value == other.priority.value:
            if self.seq_id is not None and other.seq_id is not None:
                return self.seq_id < other.seq_id
            return self.timestamp >= other.timestamp
        return self.priority.value < other.priority.value

    def __le__(self, other: PlugwiseMessage) -> bool:
        """Less than or equal."""
        if self.priority.value == other.priority.value:
            if self.seq_id is not None and other.seq_id is not None:
                return self.seq_id <= other.seq_id
            return self.timestamp <= other.timestamp
        return self.priority.value > other.priority.value
