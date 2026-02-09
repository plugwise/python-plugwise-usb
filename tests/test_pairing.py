"""Test pairing plus-device to plugwise USB Stick."""

import asyncio
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime as dt, timedelta as td
import importlib
import logging
import random
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest

import aiofiles  # type: ignore[import-untyped]
import crcmod
from freezegun import freeze_time

crc_fun = crcmod.mkCrcFun(0x11021, rev=False, initCrc=0x0000, xorOut=0x0000)

pw_stick = importlib.import_module("plugwise_usb")
pw_api = importlib.import_module("plugwise_usb.api")
pw_exceptions = importlib.import_module("plugwise_usb.exceptions")
pw_connection = importlib.import_module("plugwise_usb.connection")
pw_connection_manager = importlib.import_module("plugwise_usb.connection.manager")
pw_constants = importlib.import_module("plugwise_usb.constants")
pw_helpers_cache = importlib.import_module("plugwise_usb.helpers.cache")
pw_network_cache = importlib.import_module("plugwise_usb.network.cache")
pw_node_cache = importlib.import_module("plugwise_usb.nodes.helpers.cache")
pw_receiver = importlib.import_module("plugwise_usb.connection.receiver")
pw_sender = importlib.import_module("plugwise_usb.connection.sender")
pw_requests = importlib.import_module("plugwise_usb.messages.requests")
pw_responses = importlib.import_module("plugwise_usb.messages.responses")
pw_msg_properties = importlib.import_module("plugwise_usb.messages.properties")
pw_node = importlib.import_module("plugwise_usb.nodes.node")
pw_circle = importlib.import_module("plugwise_usb.nodes.circle")
pw_sed = importlib.import_module("plugwise_usb.nodes.sed")
pw_scan = importlib.import_module("plugwise_usb.nodes.scan")
pw_sense = importlib.import_module("plugwise_usb.nodes.sense")
pw_switch = importlib.import_module("plugwise_usb.nodes.switch")
pw_energy_counter = importlib.import_module("plugwise_usb.nodes.helpers.counter")
pw_energy_calibration = importlib.import_module("plugwise_usb.nodes.helpers")
pw_energy_pulses = importlib.import_module("plugwise_usb.nodes.helpers.pulses")

_LOGGER = logging.getLogger(__name__)
_LOGGER.setLevel(logging.DEBUG)

RESPONSE_MESSAGES = {
    b"\x05\x05\x03\x030001CAAB\r\n": (
        "Stick network info request",
        b"000000C1",  # Success ack
        b"0002"  # response msg_id
        + b"0123456789012345"  # stick-mac
        + b"0F"  # channel
        + b"FFFFFFFFFFFFFFFF"
        + b"0698765432101234"  # 06 + plus-device mac
        + b"FFFFFFFFFFFFFFFF"
        + b"0698765432101234"  # 06 + plus-device mac
        + b"1606"  # pan_id
        + b"01",  # index
    ),
    b"\x05\x05\x03\x03000AB43C\r\n": (
        "STICK INIT",
        b"000000C1",  # Success ack
        b"0011"  # msg_id
        + b"0123456789012345"  # stick mac
        + b"00"  # unknown1
        + b"00",  # network_is_offline
    ),
    b"\x05\x05\x03\x0300040000000000000000000098765432101234\r\n": (
        "Pair request of plus-device 0098765432101234",
        b"000000C1",  # Success ack
        b"0005"  # response msg_id
        + b"00"  # existing
        + b"01",  # allowed
    ),
    b"\x05\x05\x03\x0300230123456789012345A0EC\r\n": (
        "Node Info of stick 0123456789012345",
        b"000000C1",  # Success ack
        b"0024"  # msg_id
        + b"0123456789012345"  # mac
        + b"00000000"  # datetime
        + b"00000000"  # log address 0
        + b"00"  # relay
        + b"80"  # hz
        + b"653907008512"  # hw_ver
        + b"4E0843A9"  # fw_ver
        + b"00",  # node_type (Stick)
    ),
}

SECOND_RESPONSE_MESSAGES = {
    b"\x05\x05\x03\x03000D55555555555555555E46\r\n": (
        "ping reply for 5555555555555555",
        b"000000C1",  # Success ack
        b"000E"
        + b"5555555555555555"  # mac
        + b"44"  # rssi in
        + b"33"  # rssi out
        + b"0055",  # roundtrip
    )
}

def inc_seq_id(seq_id: bytes | None) -> bytes:
    """Increment sequence id."""
    if seq_id is None:
        return b"0000"
    temp_int = int(seq_id, 16) + 1
    if temp_int >= 65532:
        temp_int = 0
    temp_str = str(hex(temp_int)).lstrip("0x").upper()
    while len(temp_str) < 4:
        temp_str = "0" + temp_str
    return temp_str.encode()


def construct_message(data: bytes, seq_id: bytes = b"0000") -> bytes:
    """Construct plugwise message."""
    body = data[:4] + seq_id + data[4:]
    return bytes(
        pw_constants.MESSAGE_HEADER
        + body
        + bytes(f"{crc_fun(body):04X}", pw_constants.UTF8)
        + pw_constants.MESSAGE_FOOTER
    )


class DummyTransport:
    """Dummy transport class."""

    protocol_data_received: Callable[[bytes], None]

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        test_data: dict[bytes, tuple[str, bytes, bytes | None]] | None = None,
    ) -> None:
        """Initialize dummy transport class."""
        self._loop = loop
        self._msg = 0
        self._seq_id = b"1233"
        self._processed: list[bytes] = []
        self._first_response = test_data
        self._second_response = test_data
        if test_data is None:
            self._first_response = RESPONSE_MESSAGES
            self._second_response = SECOND_RESPONSE_MESSAGES
        self.random_extra_byte = 0
        self._closing = False

    def is_closing(self) -> bool:
        """Close connection."""
        return self._closing

    def write(self, data: bytes) -> None:
        """Write data back to system."""
        log = None
        ack = None
        response = None
        if data in self._processed and self._second_response is not None:
            log, ack, response = self._second_response.get(data, (None, None, None))
        if log is None and self._first_response is not None:
            log, ack, response = self._first_response.get(data, (None, None, None))
        if log is None:
            resp = PARTLY_RESPONSE_MESSAGES.get(
                data[:24], (None, None, None)
            )
            if resp is None:
                _LOGGER.debug("No msg response for %s", str(data))
                return
            log, ack, response = resp
        if ack is None:
            _LOGGER.debug("No ack response for %s", str(data))
            return

        self._seq_id = inc_seq_id(self._seq_id)
        if response and self._msg == 0:
            self.message_response_at_once(ack, response, self._seq_id)
            self._processed.append(data)
        else:
            self.message_response(ack, self._seq_id)
            self._processed.append(data)
            if response is None or self._closing:
                return
            self._loop.create_task(self._delayed_response(response, self._seq_id))
        self._msg += 1

    async def _delayed_response(self, data: bytes, seq_id: bytes) -> None:
        delay = random.uniform(0.005, 0.025)
        await asyncio.sleep(delay)
        self.message_response(data, seq_id)

    def message_response(self, data: bytes, seq_id: bytes) -> None:
        """Handle message response."""
        self.random_extra_byte += 1
        if self.random_extra_byte > 25:
            self.protocol_data_received(b"\x83")
            self.random_extra_byte = 0
            self.protocol_data_received(construct_message(data, seq_id) + b"\x83")
        else:
            self.protocol_data_received(construct_message(data, seq_id))

    def message_response_at_once(self, ack: bytes, data: bytes, seq_id: bytes) -> None:
        """Full message."""
        self.random_extra_byte += 1
        if self.random_extra_byte > 25:
            self.protocol_data_received(b"\x83")
            self.random_extra_byte = 0
            self.protocol_data_received(
                construct_message(ack, seq_id)
                + construct_message(data, seq_id)
                + b"\x83"
            )
        else:
            self.protocol_data_received(
                construct_message(ack, seq_id) + construct_message(data, seq_id)
            )

    def close(self) -> None:
        """Close connection."""
        self._closing = True


class MockSerial:
    """Mock serial connection."""

    def __init__(
        self, custom_response: dict[bytes, tuple[str, bytes, bytes | None]] | None
    ) -> None:
        """Init mocked serial connection."""
        self.custom_response = custom_response
        self._protocol: pw_receiver.StickReceiver | None = None  # type: ignore[name-defined]
        self._transport: DummyTransport | None = None

    def inject_message(self, data: bytes, seq_id: bytes) -> None:
        """Inject message to be received from stick."""
        if self._transport is None:
            return
        self._transport.message_response(data, seq_id)

    def trigger_connection_lost(self) -> None:
        """Trigger connection lost."""
        if self._protocol is None:
            return
        self._protocol.connection_lost()

    async def mock_connection(
        self,
        loop: asyncio.AbstractEventLoop,
        protocol_factory: Callable[[], pw_receiver.StickReceiver],  # type: ignore[name-defined]
        **kwargs: dict[str, Any],
    ) -> tuple[DummyTransport, pw_receiver.StickReceiver]:  # type: ignore[name-defined]
        """Mock connection with dummy connection."""
        self._protocol = protocol_factory()
        self._transport = DummyTransport(loop, self.custom_response)
        self._transport.protocol_data_received = self._protocol.data_received
        loop.call_soon_threadsafe(self._protocol.connection_made, self._transport)
        return self._transport, self._protocol


class MockOsPath:
    """Mock aiofiles.path class."""

    async def exists(self, file_or_path: str) -> bool:  # noqa:  PLR0911
        """Exists folder."""
        test_exists = [
            "mock_folder_that_exists",
            "mock_folder_that_exists/nodetype.cache",
            "mock_folder_that_exists\\nodetype.cache",
            "mock_folder_that_exists/0123456789ABCDEF.cache",
            "mock_folder_that_exists\\0123456789ABCDEF.cache",
            "mock_folder_that_exists\\file_that_exists.ext",
        ]
        if file_or_path in test_exists:
            return True
        return file_or_path == "mock_folder_that_exists/file_that_exists.ext"

    async def mkdir(self, path: str) -> None:
        """Make dir."""
        return


class MockStickController:
    """Mock stick controller."""

    def __init__(self) -> None:
        """Initialize MockStickController."""
        self.send_response: list[pw_responses.PlugwiseResponse] = []

    async def subscribe_to_messages(
        self,
        node_response_callback: Callable[  # type: ignore[name-defined]
            [pw_responses.PlugwiseResponse], Coroutine[Any, Any, bool]
        ],
        mac: bytes | None = None,
        message_ids: tuple[bytes] | None = None,
    ) -> Callable[[], None]:
        """Subscribe a awaitable callback to be called when a specific message is received.

        Returns function to unsubscribe.
        """

        def dummy_method() -> None:
            """Fake method."""

        return dummy_method

    def append_response(self, response) -> None:
        """Add response to queue."""
        self.send_response.append(response)

    def clear_responses(self) -> None:
        """Clear response queue."""
        self.send_response.clear()

    async def send(
        self,
        request: pw_requests.PlugwiseRequest,  # type: ignore[name-defined]
        suppress_node_errors=True,
    ) -> pw_responses.PlugwiseResponse | None:  # type: ignore[name-defined]
        """Submit request to queue and return response."""
        if self.send_response:
            return self.send_response.pop(0)
        return None


aiofiles.threadpool.wrap.register(MagicMock)(
    lambda *args, **kwargs: aiofiles.threadpool.AsyncBufferedIOBase(*args, **kwargs)  # pylint: disable=unnecessary-lambda
)


class TestStick:
    """Test USB Stick."""

    test_node_awake: asyncio.Future[str]
    test_node_loaded: asyncio.Future[str]
    test_node_join: asyncio.Future[str]
    test_connected: asyncio.Future[bool]
    test_disconnected: asyncio.Future[bool]
    test_relay_state_on: asyncio.Future[bool]
    test_relay_state_off: asyncio.Future[bool]
    test_motion_on: asyncio.Future[bool]
    test_motion_off: asyncio.Future[bool]
    test_init_relay_state_off: asyncio.Future[bool]
    test_init_relay_state_on: asyncio.Future[bool]

    async def connected(self, event: pw_api.StickEvent) -> None:  # type: ignore[name-defined]
        """Set connected state helper."""
        if event is pw_api.StickEvent.CONNECTED:
            self.test_connected.set_result(True)
        else:
            self.test_connected.set_exception(BaseException("Incorrect event"))

    async def dummy_fn(self, request: pw_requests.PlugwiseRequest, test: bool) -> None:  # type: ignore[name-defined]
        """Callable dummy routine."""
        return

    #    @pytest.mark.asyncio
    #    async def test_stick_connect(self, monkeypatch: pytest.MonkeyPatch) -> None:
    #        """Test connecting to stick."""
    #        monkeypatch.setattr(
    #            pw_connection_manager,
    #            "create_serial_connection",
    #            MockSerial(None).mock_connection,
    #        )
    #        stick = pw_stick.Stick(port="test_port", cache_enabled=False)

    #        unsub_connect = stick.subscribe_to_stick_events(
    #            stick_event_callback=self.connected,
    #            events=(pw_api.StickEvent.CONNECTED,),
    #        )
    #        self.test_connected = asyncio.Future()
    #        await stick.connect("test_port")
    #        assert await self.test_connected
    #        await stick.initialize()
    #        assert stick.mac_stick == "0123456789012345"
    #        assert stick.name == "Stick 12345"
    #        assert stick.mac_coordinator == "0098765432101234"
    #        assert stick.firmware == dt(2011, 6, 27, 8, 47, 37, tzinfo=UTC)
    #        assert stick.hardware == "070085"
    #        assert not stick.network_discovered
    #        assert stick.network_state
    #        assert stick.network_id == 17185
    #        unsub_connect()
    #        await stick.disconnect()
    #        assert not stick.network_state
    #        with pytest.raises(pw_exceptions.StickError):
    #            stick.mac_stick

    #    @pytest.mark.asyncio
    #    async def test_stick_network_down(self, monkeypatch: pytest.MonkeyPatch) -> None:
    #        """Testing Stick init without paired Circle."""
    #        mock_serial = MockSerial(
    #            {
    #                b"\x05\x05\x03\x03000AB43C\r\n": (
    #                    "STICK INIT",
    #                    b"000000C1",  # Success ack
    #                    b"0011"  # response msg_id
    #                    + b"0123456789012345"  # stick mac
    #                    + b"00"  # unknown1
    #                    + b"00",  # network_is_offline
    #                ),
    #            }
    #        )
    #        monkeypatch.setattr(
    #            pw_connection_manager,
    #            "create_serial_connection",
    #            mock_serial.mock_connection,
    #        )
    #        monkeypatch.setattr(pw_sender, "STICK_TIME_OUT", 0.2)
    #        monkeypatch.setattr(pw_requests, "NODE_TIME_OUT", 1.0)
    #        stick = pw_stick.Stick(port="test_port", cache_enabled=False)
    #        await stick.connect()
    #        with pytest.raises(pw_exceptions.StickError):
    #            await stick.initialize()
    #        await stick.disconnect()

    RESPONSE_MESSAGES = {
        b"\x05\x05\x03\x030001CAAB\r\n": (
            "Stick network info request",
            b"000000C1",  # Success ack
            b"0002"  # response msg_id
            + b"0123456789012345"  # stick-mac
            + b"0F"  # channel
            + b"FFFFFFFFFFFFFFFF"
            + b"FF98765432101234"  # 06 + plus-device mac
            + b"FFFFFFFFFFFFFFFF"
            + b"FF98765432101234"  # 06 + plus-device mac
            + b"04FF"  # pan_id
            + b"01",  # index
        ),
        b"\x05\x05\x03\x03000AB43C\r\n": (
            "STICK INIT",
            b"000000C1",  # Success ack
            b"0011"  # msg_id
            + b"0123456789012345"  # stick mac
            + b"00"  # unknown1
            + b"01"  # network_is_online
            + b"FF98765432101234"
            + b"04FF"
            + b"FF",
        ),
        b"\x05\x05\x03\x0300040000000000000000000098765432101234\r\n": (
            "Pair request of plus-device 0098765432101234",
            b"000000C1",  # Success ack
            b"0005"  # response msg_id
            + b"00"  # existing
            + b"01",  # allowed
        ),
        b"\x05\x05\x03\x0300230123456789012345A0EC\r\n": (
            "Node Info of stick 0123456789012345",
            b"000000C1",  # Success ack
            b"0024"  # msg_id
            + b"0123456789012345"  # mac
            + b"00000000"  # datetime
            + b"00000000"  # log address 0
            + b"00"  # relay
            + b"80"  # hz
            + b"653907008512"  # hw_ver
            + b"4E0843A9"  # fw_ver
            + b"00",  # node_type (Stick)
        ),
    }


    @pytest.mark.asyncio
    async def test_pair_plus(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test pairing a plus-device."""
        mock_serial = MockSerial(None)
        monkeypatch.setattr(
            pw_connection_manager,
            "create_serial_connection",
            MockSerial(None).mock_connection,
        )
        monkeypatch.setattr(pw_sender, "STICK_TIME_OUT", 0.1)
        monkeypatch.setattr(pw_requests, "NODE_TIME_OUT", 0.5)
        stick = pw_stick.Stick(port="test_port", cache_enabled=False)
        await stick.connect("test_port")
        with pytest.raises(pw_exceptions.StickError):
            await stick.initialize()

        await asyncio.sleep(5)
        await stick.plus_pair_request("0098765432101234")
        await asyncio.sleep(5)

        await stick.disconnect()


#    async def node_join(self, event: pw_api.NodeEvent, mac: str) -> None:  # type: ignore[name-defined]
#        """Handle join event callback."""
#        if event == pw_api.NodeEvent.JOIN:
#            self.test_node_join.set_result(mac)
#        else:
#            self.test_node_join.set_exception(
#                BaseException(
#                    f"Invalid {event} event, expected " + f"{pw_api.NodeEvent.JOIN}"
#                )
#            )

# @pytest.mark.asyncio
# async def test_stick_node_join_subscription(
#    self, monkeypatch: pytest.MonkeyPatch
# ) -> None:
# """Testing "new_node" subscription."""
# mock_serial = MockSerial(None)
# monkeypatch.setattr(
#    pw_connection_manager,
#    "create_serial_connection",
#    mock_serial.mock_connection,
# )
# monkeypatch.setattr(pw_sender, "STICK_TIME_OUT", 0.1)
# monkeypatch.setattr(pw_requests, "NODE_TIME_OUT", 0.5)
# stick = pw_stick.Stick("test_port", cache_enabled=False)
# await stick.connect()
# await stick.initialize()
# await stick.discover_nodes(load=False)

# self.test_node_join = asyncio.Future()
# unusb_join = stick.subscribe_to_node_events(
#    node_event_callback=self.node_join,
#    events=(pw_api.NodeEvent.JOIN,),
# )

## Inject NodeJoinAvailableResponse
# mock_serial.inject_message(b"00069999999999999999", b"1253")  # @bouwew: seq_id is not FFFC!
# mac_join_node = await self.test_node_join
# assert mac_join_node == "9999999999999999"
# unusb_join()
# await stick.disconnect()
