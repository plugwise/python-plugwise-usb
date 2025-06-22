"""Test plugwise USB Stick."""

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
pw_userdata = importlib.import_module("stick_test_data")
pw_node = importlib.import_module("plugwise_usb.nodes.node")
pw_circle = importlib.import_module("plugwise_usb.nodes.circle")
pw_sed = importlib.import_module("plugwise_usb.nodes.sed")
pw_scan = importlib.import_module("plugwise_usb.nodes.scan")
pw_switch = importlib.import_module("plugwise_usb.nodes.switch")
pw_energy_counter = importlib.import_module("plugwise_usb.nodes.helpers.counter")
pw_energy_calibration = importlib.import_module("plugwise_usb.nodes.helpers")
pw_energy_pulses = importlib.import_module("plugwise_usb.nodes.helpers.pulses")

_LOGGER = logging.getLogger(__name__)
_LOGGER.setLevel(logging.DEBUG)


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
            self._first_response = pw_userdata.RESPONSE_MESSAGES
            self._second_response = pw_userdata.SECOND_RESPONSE_MESSAGES
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
            resp = pw_userdata.PARTLY_RESPONSE_MESSAGES.get(
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
        if file_or_path == "mock_folder_that_exists":
            return True
        if file_or_path == "mock_folder_that_exists/nodes.cache":
            return True
        if file_or_path == "mock_folder_that_exists\\nodes.cache":
            return True
        if file_or_path == "mock_folder_that_exists/0123456789ABCDEF.cache":
            return True
        if file_or_path == "mock_folder_that_exists\\0123456789ABCDEF.cache":
            return True
        if file_or_path == "mock_folder_that_exists\\file_that_exists.ext":
            return True
        return file_or_path == "mock_folder_that_exists/file_that_exists.ext"

    async def mkdir(self, path: str) -> None:
        """Make dir."""
        return


class MockStickController:
    """Mock stick controller."""

    send_response = None

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

    async def send(
        self,
        request: pw_requests.PlugwiseRequest,  # type: ignore[name-defined]
        suppress_node_errors=True,
    ) -> pw_responses.PlugwiseResponse | None:  # type: ignore[name-defined]
        """Submit request to queue and return response."""
        return self.send_response


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

    async def dummy_fn(self, request: pw_requests.PlugwiseRequest, test: bool) -> None:  # type: ignore[name-defined]
        """Callable dummy routine."""
        return

    @pytest.mark.asyncio
    async def test_sorting_request_messages(self) -> None:
        """Test request message priority sorting."""
        node_add_request = pw_requests.NodeAddRequest(
            self.dummy_fn, b"1111222233334444", True
        )
        await asyncio.sleep(0.001)  # Ensure timestamp is different
        relay_switch_request = pw_requests.CircleRelaySwitchRequest(
            self.dummy_fn, b"1234ABCD12341234", True
        )
        await asyncio.sleep(0.001)  # Ensure timestamp is different
        circle_plus_allow_joining_request = pw_requests.CirclePlusAllowJoiningRequest(
            self.dummy_fn, True
        )

        # validate sorting based on timestamp with same priority level
        assert node_add_request < circle_plus_allow_joining_request
        assert circle_plus_allow_joining_request > node_add_request
        assert circle_plus_allow_joining_request >= node_add_request
        assert node_add_request <= circle_plus_allow_joining_request

        # validate sorting based on priority
        assert relay_switch_request > node_add_request
        assert relay_switch_request >= node_add_request
        assert node_add_request < relay_switch_request
        assert node_add_request <= relay_switch_request
        assert relay_switch_request > circle_plus_allow_joining_request
        assert relay_switch_request >= circle_plus_allow_joining_request
        assert circle_plus_allow_joining_request < relay_switch_request
        assert circle_plus_allow_joining_request <= relay_switch_request

        # Change priority
        node_add_request.priority = pw_requests.Priority.LOW
        # Validate node_add_request is less than other requests
        assert node_add_request < relay_switch_request
        assert node_add_request <= relay_switch_request
        assert node_add_request < circle_plus_allow_joining_request
        assert node_add_request <= circle_plus_allow_joining_request
        assert relay_switch_request > node_add_request
        assert relay_switch_request >= node_add_request
        assert circle_plus_allow_joining_request > node_add_request
        assert circle_plus_allow_joining_request >= node_add_request

    @pytest.mark.asyncio
    async def test_msg_properties(self) -> None:
        """Test message properties."""
        # UnixTimestamp
        unix_timestamp = pw_msg_properties.UnixTimestamp(
            dt(2011, 6, 27, 9, 4, 10, tzinfo=UTC), 8
        )
        assert unix_timestamp.serialize() == b"4E08478A"
        with pytest.raises(pw_exceptions.MessageError):
            assert unix_timestamp.value == dt(2011, 6, 27, 9, 4, 10, tzinfo=UTC)
        unix_timestamp.deserialize(b"4E08478A")
        assert unix_timestamp.value == dt(2011, 6, 27, 9, 4, 10, tzinfo=UTC)

    @pytest.mark.asyncio
    async def test_stick_connect_without_port(self) -> None:
        """Test connecting to stick without port config."""
        stick = pw_stick.Stick()
        assert stick.nodes == {}
        assert stick.joined_nodes is None
        with pytest.raises(pw_exceptions.StickError):
            assert stick.mac_stick
        with pytest.raises(pw_exceptions.StickError):
            assert stick.mac_coordinator
        with pytest.raises(pw_exceptions.StickError):
            assert stick.network_id
        assert not stick.network_discovered
        assert not stick.network_state

        with pytest.raises(pw_exceptions.StickError):
            await stick.connect()
        stick.port = "null"
        with pytest.raises(pw_exceptions.StickError):
            await stick.connect()
        await stick.disconnect()

    @pytest.mark.asyncio
    async def test_stick_reconnect(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test connecting to stick while already connected."""
        monkeypatch.setattr(
            pw_connection_manager,
            "create_serial_connection",
            MockSerial(None).mock_connection,
        )
        stick = pw_stick.Stick()
        stick.port = "test_port"
        assert stick.port == "test_port"
        await stick.connect()
        # second time should raise
        with pytest.raises(pw_exceptions.StickError):
            await stick.connect()
        await stick.disconnect()

    @pytest.mark.asyncio
    async def test_stick_connect_without_response(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test connecting to stick without response."""
        monkeypatch.setattr(
            pw_connection_manager,
            "create_serial_connection",
            MockSerial(
                {
                    b"FFFF": (
                        "no response",
                        b"0000",
                        b"",
                    ),
                }
            ).mock_connection,
        )
        monkeypatch.setattr(pw_sender, "STICK_TIME_OUT", 0.2)
        stick = pw_stick.Stick()
        stick.port = "test_port"
        with pytest.raises(pw_exceptions.StickError):
            await stick.initialize()
        # Connect
        await stick.connect()
        # Still raise StickError connected but without response
        with pytest.raises(pw_exceptions.StickError):
            await stick.initialize()
        await stick.disconnect()

    @pytest.mark.asyncio
    async def test_stick_connect_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test connecting to stick."""
        monkeypatch.setattr(
            pw_connection_manager,
            "create_serial_connection",
            MockSerial(
                {
                    b"\x05\x05\x03\x03000AB43C\r\n": (
                        "STICK INIT timeout",
                        b"000000E1",  # Timeout ack
                        None,
                    ),
                }
            ).mock_connection,
        )
        monkeypatch.setattr(pw_sender, "STICK_TIME_OUT", 0.5)
        stick = pw_stick.Stick()
        await stick.connect("test_port")
        with pytest.raises(pw_exceptions.StickError):
            await stick.initialize()
        await stick.disconnect()

    async def connected(self, event: pw_api.StickEvent) -> None:  # type: ignore[name-defined]
        """Set connected state helper."""
        if event is pw_api.StickEvent.CONNECTED:
            self.test_connected.set_result(True)
        else:
            self.test_connected.set_exception(BaseException("Incorrect event"))

    @pytest.mark.asyncio
    async def test_stick_connect(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test connecting to stick."""
        monkeypatch.setattr(
            pw_connection_manager,
            "create_serial_connection",
            MockSerial(None).mock_connection,
        )
        stick = pw_stick.Stick(port="test_port", cache_enabled=False)

        unsub_connect = stick.subscribe_to_stick_events(
            stick_event_callback=self.connected,
            events=(pw_api.StickEvent.CONNECTED,),
        )
        self.test_connected = asyncio.Future()
        await stick.connect("test_port")
        assert await self.test_connected
        await stick.initialize()
        assert stick.mac_stick == "0123456789012345"
        assert stick.name == "Stick 12345"
        assert stick.mac_coordinator == "0098765432101234"
        assert stick.firmware == dt(2011, 6, 27, 8, 47, 37, tzinfo=UTC)
        assert stick.hardware == "070085"
        assert not stick.network_discovered
        assert stick.network_state
        assert stick.network_id == 17185
        unsub_connect()
        await stick.disconnect()
        assert not stick.network_state
        with pytest.raises(pw_exceptions.StickError):
            assert stick.mac_stick

    async def disconnected(self, event: pw_api.StickEvent) -> None:  # type: ignore[name-defined]
        """Handle disconnect event callback."""
        if event is pw_api.StickEvent.DISCONNECTED:
            self.test_disconnected.set_result(True)
        else:
            self.test_disconnected.set_exception(BaseException("Incorrect event"))

    @pytest.mark.asyncio
    async def test_stick_connection_lost(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test connecting to stick."""
        mock_serial = MockSerial(None)
        monkeypatch.setattr(
            pw_connection_manager,
            "create_serial_connection",
            mock_serial.mock_connection,
        )
        stick = pw_stick.Stick()
        await stick.connect("test_port")
        await stick.initialize()
        assert stick.network_state
        self.test_disconnected = asyncio.Future()
        unsub_disconnect = stick.subscribe_to_stick_events(
            stick_event_callback=self.disconnected,
            events=(pw_api.StickEvent.DISCONNECTED,),
        )
        # Trigger disconnect
        mock_serial.trigger_connection_lost()
        assert await self.test_disconnected
        assert not stick.network_state
        unsub_disconnect()
        await stick.disconnect()

    async def node_awake(self, event: pw_api.NodeEvent, mac: str) -> None:  # type: ignore[name-defined]
        """Handle awake event callback."""
        if event == pw_api.NodeEvent.AWAKE:
            self.test_node_awake.set_result(mac)
        else:
            self.test_node_awake.set_exception(
                BaseException(
                    f"Invalid {event} event, expected " + f"{pw_api.NodeEvent.AWAKE}"
                )
            )

    async def node_loaded(self, event: pw_api.NodeEvent, mac: str) -> None:  # type: ignore[name-defined]
        """Handle awake event callback."""
        if event == pw_api.NodeEvent.LOADED:
            self.test_node_loaded.set_result(mac)
        else:
            self.test_node_loaded.set_exception(
                BaseException(
                    f"Invalid {event} event, expected " + f"{pw_api.NodeEvent.LOADED}"
                )
            )

    async def node_motion_state(
        self,
        feature: pw_api.NodeFeature,  # type: ignore[name-defined]
        motion: pw_api.MotionState,  # type: ignore[name-defined]
    ) -> None:
        """Handle motion event callback."""
        if feature == pw_api.NodeFeature.MOTION:
            if motion.state:
                self.test_motion_on.set_result(motion.state)
            else:
                self.test_motion_off.set_result(motion.state)
        else:
            self.test_motion_on.set_exception(
                BaseException(
                    f"Invalid {feature} feature, expected "
                    + f"{pw_api.NodeFeature.MOTION}"
                )
            )
            self.test_motion_off.set_exception(
                BaseException(
                    f"Invalid {feature} feature, expected "
                    + f"{pw_api.NodeFeature.MOTION}"
                )
            )

    @pytest.mark.asyncio
    async def test_stick_node_discovered_subscription(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Testing "new_node" subscription for Scan."""
        mock_serial = MockSerial(None)
        monkeypatch.setattr(
            pw_connection_manager,
            "create_serial_connection",
            mock_serial.mock_connection,
        )
        monkeypatch.setattr(pw_sender, "STICK_TIME_OUT", 0.1)
        monkeypatch.setattr(pw_requests, "NODE_TIME_OUT", 0.5)
        stick = pw_stick.Stick("test_port", cache_enabled=False)
        await stick.connect()
        await stick.initialize()
        await stick.discover_nodes(load=False)
        self.test_node_awake = asyncio.Future()
        unsub_awake = stick.subscribe_to_node_events(
            node_event_callback=self.node_awake,
            events=(pw_api.NodeEvent.AWAKE,),
        )

        # Inject NodeAwakeResponse message to trigger a 'node discovered' event
        mock_serial.inject_message(b"004F555555555555555500", b"FFFE")
        mac_awake_node = await self.test_node_awake
        assert mac_awake_node == "5555555555555555"
        unsub_awake()

        assert await stick.nodes["5555555555555555"].load()
        assert stick.nodes["5555555555555555"].node_info.firmware == dt(
            2011, 6, 27, 8, 55, 44, tzinfo=UTC
        )
        assert stick.nodes["5555555555555555"].node_info.version == "080007"
        assert stick.nodes["5555555555555555"].node_info.model == "Scan"
        assert stick.nodes["5555555555555555"].node_info.model_type is None
        assert stick.nodes["5555555555555555"].available
        assert stick.nodes["5555555555555555"].node_info.is_battery_powered
        assert sorted(stick.nodes["5555555555555555"].features) == sorted(
            (
                pw_api.NodeFeature.AVAILABLE,
                pw_api.NodeFeature.BATTERY,
                pw_api.NodeFeature.INFO,
                pw_api.NodeFeature.PING,
                pw_api.NodeFeature.MOTION,
                pw_api.NodeFeature.MOTION_CONFIG,
            )
        )

        # Check Scan is raising NodeError for unsupported features
        with pytest.raises(pw_exceptions.FeatureError):
            assert stick.nodes["5555555555555555"].relay
        with pytest.raises(pw_exceptions.FeatureError):
            assert stick.nodes["5555555555555555"].relay_state
        with pytest.raises(pw_exceptions.FeatureError):
            assert stick.nodes["5555555555555555"].switch
        with pytest.raises(pw_exceptions.FeatureError):
            assert stick.nodes["5555555555555555"].power
        with pytest.raises(pw_exceptions.FeatureError):
            assert stick.nodes["5555555555555555"].sense
        with pytest.raises(pw_exceptions.FeatureError):
            assert stick.nodes["5555555555555555"].energy

        # Motion
        self.test_motion_on = asyncio.Future()
        self.test_motion_off = asyncio.Future()
        unsub_motion = stick.nodes["5555555555555555"].subscribe_to_feature_update(
            node_feature_callback=self.node_motion_state,
            features=(pw_api.NodeFeature.MOTION,),
        )
        # Inject motion message to trigger a 'motion on' event
        mock_serial.inject_message(b"005655555555555555550001", b"FFFF")
        motion_on = await self.test_motion_on
        assert motion_on
        assert stick.nodes["5555555555555555"].motion

        # Inject motion message to trigger a 'motion off' event
        mock_serial.inject_message(b"005655555555555555550000", b"FFFF")
        motion_off = await self.test_motion_off
        assert not motion_off
        assert not stick.nodes["5555555555555555"].motion
        unsub_motion()

        await stick.disconnect()

    async def node_join(self, event: pw_api.NodeEvent, mac: str) -> None:  # type: ignore[name-defined]
        """Handle join event callback."""
        if event == pw_api.NodeEvent.JOIN:
            self.test_node_join.set_result(mac)
        else:
            self.test_node_join.set_exception(
                BaseException(
                    f"Invalid {event} event, expected " + f"{pw_api.NodeEvent.JOIN}"
                )
            )

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

    @pytest.mark.asyncio
    async def test_node_discovery(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Testing discovery of nodes."""
        mock_serial = MockSerial(None)
        monkeypatch.setattr(
            pw_connection_manager,
            "create_serial_connection",
            mock_serial.mock_connection,
        )
        monkeypatch.setattr(pw_sender, "STICK_TIME_OUT", 0.2)
        monkeypatch.setattr(pw_requests, "NODE_TIME_OUT", 2.0)
        stick = pw_stick.Stick("test_port", cache_enabled=False)
        await stick.connect()
        await stick.initialize()
        await stick.discover_nodes(load=False)
        assert stick.joined_nodes == 11
        assert stick.nodes.get("0098765432101234") is not None
        assert len(stick.nodes) == 6  # Discovered nodes
        await stick.disconnect()

    async def node_relay_state(
        self,
        feature: pw_api.NodeFeature,  # type: ignore[name-defined]
        state: pw_api.RelayState,  # type: ignore[name-defined]
    ) -> None:
        """Handle relay event callback."""
        if feature in (pw_api.NodeFeature.RELAY, pw_api.NodeFeature.RELAY_LOCK):
            if feature == pw_api.NodeFeature.RELAY:
                if state.state:
                    self.test_relay_state_on.set_result(state.state)
                else:
                    self.test_relay_state_off.set_result(state.state)
            if feature == pw_api.NodeFeature.RELAY_LOCK:
                # Handle RELAY_LOCK callbacks if needed
                pass
        else:
            self.test_relay_state_on.set_exception(
                BaseException(
                    f"Invalid {feature} feature, expected "
                    + f"{pw_api.NodeFeature.RELAY}"
                )
            )
            self.test_relay_state_off.set_exception(
                BaseException(
                    f"Invalid {feature} feature, expected "
                    + f"{pw_api.NodeFeature.RELAY}"
                )
            )

    async def node_init_relay_state(
        self,
        feature: pw_api.NodeFeature,  # type: ignore[name-defined]
        config: pw_api.RelayConfig,  # type: ignore[name-defined]
    ) -> None:
        """Relay Callback for event."""
        if feature == pw_api.NodeFeature.RELAY_INIT:
            if config.init_state:
                self.test_init_relay_state_on.set_result(config.init_state)
            else:
                self.test_init_relay_state_off.set_result(config.init_state)
        else:
            self.test_init_relay_state_on.set_exception(
                BaseException(
                    f"Invalid {feature} feature, expected "
                    + f"{pw_api.NodeFeature.RELAY_INIT}"
                )
            )
            self.test_init_relay_state_off.set_exception(
                BaseException(
                    f"Invalid {feature} feature, expected "
                    + f"{pw_api.NodeFeature.RELAY_INIT}"
                )
            )

    @pytest.mark.asyncio
    async def test_node_relay_and_power(self, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa:  PLR0915
        """Testing discovery of nodes."""
        mock_serial = MockSerial(None)
        monkeypatch.setattr(
            pw_connection_manager,
            "create_serial_connection",
            mock_serial.mock_connection,
        )
        monkeypatch.setattr(pw_sender, "STICK_TIME_OUT", 0.2)
        monkeypatch.setattr(pw_requests, "NODE_TIME_OUT", 2.0)
        stick = pw_stick.Stick("test_port", cache_enabled=False)
        await stick.connect()
        await stick.initialize()
        await stick.discover_nodes(load=False)

        # Validate if NodeError is raised when device is not loaded
        with pytest.raises(pw_exceptions.NodeError):
            await stick.nodes["0098765432101234"].set_relay(True)

        with pytest.raises(pw_exceptions.NodeError):
            await stick.nodes["0098765432101234"].set_relay_lock(True)

        # Manually load node
        assert await stick.nodes["0098765432101234"].load()

        # Check relay_lock is set to False when not in cache
        assert stick.nodes["0098765432101234"].relay_lock
        assert not stick.nodes["0098765432101234"].relay_lock.state

        unsub_relay = stick.nodes["0098765432101234"].subscribe_to_feature_update(
            node_feature_callback=self.node_relay_state,
            features=(
                pw_api.NodeFeature.RELAY,
                pw_api.NodeFeature.RELAY_LOCK,
            ),
        )

        # Test async switching back from on to off
        self.test_relay_state_off = asyncio.Future()
        assert not await stick.nodes["0098765432101234"].set_relay(False)
        assert not await self.test_relay_state_off
        assert not stick.nodes["0098765432101234"].relay

        # Test blocked async switching due to relay-lock active
        await stick.nodes["0098765432101234"].set_relay_lock(True)
        assert stick.nodes["0098765432101234"].relay_lock.state
        assert not await stick.nodes["0098765432101234"].set_relay(True)
        assert not stick.nodes["0098765432101234"].relay
        # Make sure to turn lock off for further testing
        await stick.nodes["0098765432101234"].set_relay_lock(False)
        assert not stick.nodes["0098765432101234"].relay_lock.state

        # Test async switching back from off to on
        self.test_relay_state_on = asyncio.Future()
        assert await stick.nodes["0098765432101234"].set_relay(True)
        assert await self.test_relay_state_on
        assert stick.nodes["0098765432101234"].relay

        # Test async switching back from on to off
        self.test_relay_state_off = asyncio.Future()
        await stick.nodes["0098765432101234"].relay_off()
        assert not await self.test_relay_state_off
        assert not stick.nodes["0098765432101234"].relay
        assert not stick.nodes["0098765432101234"].relay_state.state

        # Test async switching back from off to on
        self.test_relay_state_on = asyncio.Future()
        await stick.nodes["0098765432101234"].relay_on()
        assert await self.test_relay_state_on
        assert stick.nodes["0098765432101234"].relay
        assert stick.nodes["0098765432101234"].relay_state.state

        unsub_relay()

        # Check if node is online
        assert await stick.nodes["0098765432101234"].is_online()

        # Test non-support relay configuration
        with pytest.raises(pw_exceptions.FeatureError):
            assert stick.nodes["0098765432101234"].relay_config is not None
        with pytest.raises(pw_exceptions.FeatureError):
            await stick.nodes["0098765432101234"].set_relay_init(True)
        with pytest.raises(pw_exceptions.FeatureError):
            await stick.nodes["0098765432101234"].set_relay_init(False)

        # Check Circle is raising NodeError for unsupported features
        with pytest.raises(pw_exceptions.FeatureError):
            assert stick.nodes["0098765432101234"].motion
        with pytest.raises(pw_exceptions.FeatureError):
            assert stick.nodes["0098765432101234"].switch
        with pytest.raises(pw_exceptions.FeatureError):
            assert stick.nodes["0098765432101234"].sense

        # Test relay init
        # load node 2222222222222222 which has
        # the firmware with init relay feature

        # Validate if NodeError is raised when device is not loaded
        with pytest.raises(pw_exceptions.NodeError):
            await stick.nodes["2222222222222222"].set_relay_init(True)

        assert await stick.nodes["2222222222222222"].load()
        self.test_init_relay_state_on = asyncio.Future()
        self.test_init_relay_state_off = asyncio.Future()
        unsub_inti_relay = stick.nodes["2222222222222222"].subscribe_to_feature_update(
            node_feature_callback=self.node_init_relay_state,
            features=(pw_api.NodeFeature.RELAY_INIT,),
        )

        # Test async switching back init_state from on to off
        assert stick.nodes["2222222222222222"].relay_config.init_state
        self.test_init_relay_state_off = asyncio.Future()
        assert not await stick.nodes["2222222222222222"].set_relay_init(False)
        assert not await self.test_init_relay_state_off
        assert not stick.nodes["2222222222222222"].relay_config.init_state

        # Test async switching back from off to on
        self.test_init_relay_state_on = asyncio.Future()
        assert await stick.nodes["2222222222222222"].set_relay_init(True)
        assert await self.test_init_relay_state_on
        assert stick.nodes["2222222222222222"].relay_config.init_state

        unsub_inti_relay()

        await stick.disconnect()

    @pytest.mark.asyncio
    async def test_energy_circle(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Testing energy retrieval."""
        mock_serial = MockSerial(None)
        monkeypatch.setattr(
            pw_connection_manager,
            "create_serial_connection",
            mock_serial.mock_connection,
        )
        monkeypatch.setattr(pw_energy_pulses, "MAX_LOG_HOURS", 25)
        monkeypatch.setattr(pw_sender, "STICK_TIME_OUT", 0.2)
        monkeypatch.setattr(pw_requests, "NODE_TIME_OUT", 2.0)

        async def fake_get_missing_energy_logs(address: int) -> None:
            """Mock missing energy logs."""

        monkeypatch.setattr(
            pw_circle.PlugwiseCircle,
            "get_missing_energy_logs",
            fake_get_missing_energy_logs,
        )
        stick = pw_stick.Stick("test_port", cache_enabled=False)
        await stick.connect()
        await stick.initialize()
        await stick.discover_nodes(load=False)

        # Check calibration in unloaded state
        assert not stick.nodes["0098765432101234"].calibrated

        # Manually load node
        assert await stick.nodes["0098765432101234"].load()

        # Check calibration in loaded state
        assert stick.nodes["0098765432101234"].calibrated

        # Test power state without request
        assert stick.nodes["0098765432101234"].power == pw_api.PowerStatistics(
            last_second=None, last_8_seconds=None, timestamp=None
        )
        pu = await stick.nodes["0098765432101234"].power_update()
        assert pu.last_second == 21.2780505980402
        assert pu.last_8_seconds == -27.150578775440106

        # Test energy state without request
        assert stick.nodes["0098765432101234"].energy == pw_api.EnergyStatistics(
            log_interval_consumption=None,
            log_interval_production=None,
            hour_consumption=None,
            hour_consumption_reset=None,
            day_consumption=None,
            day_consumption_reset=None,
            hour_production=None,
            hour_production_reset=None,
            day_production=None,
            day_production_reset=None,
        )
        # energy_update is not complete and should return none
        utc_now = dt.now(UTC)
        assert await stick.nodes["0098765432101234"].energy_update() is None
        # Allow for background task to finish

        assert stick.nodes["0098765432101234"].energy == pw_api.EnergyStatistics(
            log_interval_consumption=60,
            log_interval_production=None,
            hour_consumption=0.0026868922443345974,
            hour_consumption_reset=utc_now.replace(minute=0, second=0, microsecond=0),
            day_consumption=None,
            day_consumption_reset=None,
            hour_production=None,
            hour_production_reset=None,
            day_production=None,
            day_production_reset=None,
        )
        await stick.disconnect()

    @freeze_time("2025-04-03 22:00:00")
    def test_pulse_collection_consumption(  # noqa:  PLR0915
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Testing pulse collection class."""
        monkeypatch.setattr(pw_energy_pulses, "MAX_LOG_HOURS", 24)
        fixed_this_hour = dt.now(UTC)

        # Test consumption logs
        tst_consumption = pw_energy_pulses.PulseCollection(mac="0098765432101234")
        assert tst_consumption.log_addresses_missing is None
        assert tst_consumption.production_logging is None

        # Test consumption - Log import #1
        # No missing addresses yet
        test_timestamp = fixed_this_hour
        tst_consumption.add_log(100, 1, test_timestamp, 1000)
        assert tst_consumption.log_interval_consumption is None
        assert tst_consumption.log_interval_production is None
        assert tst_consumption.production_logging is None
        assert tst_consumption.collected_pulses(
            test_timestamp, is_consumption=True
        ) == (None, None)
        assert tst_consumption.log_addresses_missing is None

        # Test consumption - Log import #2, random log
        # No missing addresses yet
        # return intermediate missing addresses
        test_timestamp = fixed_this_hour - td(hours=17)
        tst_consumption.add_log(95, 4, test_timestamp, 1000)
        assert tst_consumption.log_interval_consumption is None
        assert tst_consumption.log_interval_production is None
        assert tst_consumption.production_logging is None
        assert tst_consumption.collected_pulses(
            test_timestamp, is_consumption=True
        ) == (None, None)
        assert tst_consumption.log_addresses_missing == [99, 98, 97, 96]

        # Test consumption - Log import #3
        # log next to existing with different timestamp
        # so 'production logging' should be marked as False now
        test_timestamp = fixed_this_hour - td(hours=18)
        tst_consumption.add_log(95, 3, test_timestamp, 1000)
        assert tst_consumption.log_interval_consumption is None
        assert tst_consumption.log_interval_production is None
        assert not tst_consumption.production_logging
        assert tst_consumption.collected_pulses(
            test_timestamp, is_consumption=True
        ) == (None, None)
        assert tst_consumption.log_addresses_missing == [99, 98, 97, 96]

        # Test consumption - Log import #4, no change
        test_timestamp = fixed_this_hour - td(hours=19)
        tst_consumption.add_log(95, 2, test_timestamp, 1000)
        assert tst_consumption.log_interval_consumption is None
        assert tst_consumption.log_interval_production is None
        assert not tst_consumption.production_logging
        assert tst_consumption.collected_pulses(
            test_timestamp, is_consumption=True
        ) == (None, None)
        assert tst_consumption.log_addresses_missing == [99, 98, 97, 96]

        # Test consumption - Log import #5
        # Complete log import for address 95 so it must drop from missing list
        test_timestamp = fixed_this_hour - td(hours=20)
        tst_consumption.add_log(95, 1, test_timestamp, 1000)
        assert tst_consumption.log_interval_consumption is None
        assert tst_consumption.log_interval_production is None
        assert not tst_consumption.production_logging
        assert tst_consumption.log_addresses_missing == [99, 98, 97, 96]

        # Test consumption - Log import #6
        # Add before last log so interval of consumption must be determined
        test_timestamp = fixed_this_hour - td(hours=1)
        tst_consumption.add_log(99, 4, test_timestamp, 750)
        assert tst_consumption.log_interval_consumption == 60
        assert tst_consumption.log_interval_production is None
        assert not tst_consumption.production_logging
        assert tst_consumption.log_addresses_missing == [99, 98, 97, 96]
        assert tst_consumption.collected_pulses(
            fixed_this_hour, is_consumption=True
        ) == (None, None)

        tst_consumption.add_log(99, 3, fixed_this_hour - td(hours=2), 1111)
        assert tst_consumption.log_interval_consumption == 60
        assert tst_consumption.log_interval_production is None
        assert not tst_consumption.production_logging
        assert tst_consumption.log_addresses_missing == [99, 98, 97, 96]
        assert tst_consumption.collected_pulses(
            fixed_this_hour, is_consumption=True
        ) == (None, None)

        # Test consumption - pulse update #1
        pulse_update_1 = fixed_this_hour + td(minutes=5)
        tst_consumption.update_pulse_counter(1234, 0, pulse_update_1)
        assert tst_consumption.collected_pulses(
            fixed_this_hour, is_consumption=True
        ) == (1234, pulse_update_1)
        assert tst_consumption.collected_pulses(
            fixed_this_hour, is_consumption=False
        ) == (None, None)
        assert tst_consumption.log_addresses_missing == [99, 98, 97, 96]

        # Test consumption - pulse update #2
        pulse_update_2 = fixed_this_hour + td(minutes=7)
        test_timestamp = fixed_this_hour
        tst_consumption.update_pulse_counter(2345, 0, pulse_update_2)
        assert tst_consumption.collected_pulses(
            test_timestamp, is_consumption=True
        ) == (2345, pulse_update_2)
        assert tst_consumption.collected_pulses(
            test_timestamp, is_consumption=False
        ) == (None, None)

        # Test consumption - pulses + log (address=100, slot=1)
        test_timestamp = fixed_this_hour - td(hours=1)
        assert tst_consumption.collected_pulses(
            test_timestamp, is_consumption=True
        ) == (2345 + 1000, pulse_update_2)
        assert tst_consumption.collected_pulses(
            test_timestamp, is_consumption=False
        ) == (None, None)
        assert tst_consumption.log_addresses_missing == [99, 98, 97, 96]

        # Test consumption - pulses + logs (address=100, slot=1 & address=99, slot=4)
        test_timestamp = fixed_this_hour - td(hours=2)
        assert tst_consumption.collected_pulses(
            test_timestamp, is_consumption=True
        ) == (2345 + 1000 + 750, pulse_update_2)
        assert tst_consumption.collected_pulses(
            test_timestamp, is_consumption=False
        ) == (None, None)

        # Test consumption - pulses + missing logs
        test_timestamp = fixed_this_hour - td(hours=3)
        assert tst_consumption.collected_pulses(
            test_timestamp, is_consumption=True
        ) == (None, None)
        assert tst_consumption.collected_pulses(
            test_timestamp, is_consumption=False
        ) == (None, None)

        assert not tst_consumption.log_rollover
        # add missing logs
        tst_consumption.add_log(99, 2, (fixed_this_hour - td(hours=3)), 1000)
        tst_consumption.add_log(99, 1, (fixed_this_hour - td(hours=4)), 1000)
        tst_consumption.add_log(98, 4, (fixed_this_hour - td(hours=5)), 1000)
        tst_consumption.add_log(98, 3, (fixed_this_hour - td(hours=6)), 1000)
        tst_consumption.add_log(98, 2, (fixed_this_hour - td(hours=7)), 1000)
        tst_consumption.add_log(98, 1, (fixed_this_hour - td(hours=8)), 1000)
        tst_consumption.add_log(97, 4, (fixed_this_hour - td(hours=9)), 1000)
        tst_consumption.add_log(97, 3, (fixed_this_hour - td(hours=10)), 1000)
        tst_consumption.add_log(97, 2, (fixed_this_hour - td(hours=11)), 1000)
        tst_consumption.add_log(97, 1, (fixed_this_hour - td(hours=12)), 1000)
        tst_consumption.add_log(96, 4, (fixed_this_hour - td(hours=13)), 1000)
        tst_consumption.add_log(96, 3, (fixed_this_hour - td(hours=14)), 1000)
        tst_consumption.add_log(96, 2, (fixed_this_hour - td(hours=15)), 1000)
        tst_consumption.add_log(96, 1, (fixed_this_hour - td(hours=16)), 1000)
        tst_consumption.add_log(94, 4, (fixed_this_hour - td(hours=21)), 1000)
        tst_consumption.add_log(94, 3, (fixed_this_hour - td(hours=22)), 1000)

        # Log 24 (max hours) must be dropped
        assert tst_consumption.collected_logs == 23
        tst_consumption.add_log(94, 2, (fixed_this_hour - td(hours=23)), 1000)
        assert tst_consumption.collected_logs == 24
        tst_consumption.add_log(94, 1, (fixed_this_hour - td(hours=24)), 1000)
        assert tst_consumption.collected_logs == 24

        # Test rollover by updating pulses before log record
        pulse_update_3 = fixed_this_hour + td(hours=1, minutes=0, seconds=30)
        test_timestamp = fixed_this_hour + td(hours=1)
        tst_consumption.update_pulse_counter(2500, 0, pulse_update_3)
        assert tst_consumption.log_rollover
        # Collected pulses last hour:
        assert tst_consumption.collected_pulses(
            test_timestamp, is_consumption=True
        ) == (2500, pulse_update_3)
        # Collected pulses last day:
        assert tst_consumption.collected_pulses(
            test_timestamp - td(hours=24), is_consumption=True
        ) == (2500 + 22861, pulse_update_3)
        pulse_update_4 = fixed_this_hour + td(hours=1, minutes=1)
        tst_consumption.update_pulse_counter(45, 0, pulse_update_4)
        assert tst_consumption.log_rollover
        # Collected pulses last hour:
        assert tst_consumption.collected_pulses(
            test_timestamp, is_consumption=True
        ) == (45, pulse_update_4)
        # Collected pulses last day:
        assert tst_consumption.collected_pulses(
            test_timestamp - td(hours=24), is_consumption=True
        ) == (45 + 22861, pulse_update_4)
        # pulse-count of 2500 is ignored, the code does not export this incorrect value

        tst_consumption.add_log(100, 2, (fixed_this_hour + td(hours=1)), 2222)
        assert not tst_consumption.log_rollover
        # Test collection of the last full hour
        assert tst_consumption.collected_pulses(
            fixed_this_hour, is_consumption=True
        ) == (45 + 2222, pulse_update_4)
        pulse_update_5 = fixed_this_hour + td(hours=1, minutes=1, seconds=18)
        tst_consumption.update_pulse_counter(145, 0, pulse_update_5)
        # Test collection of the last new hour
        assert tst_consumption.collected_pulses(
            test_timestamp, is_consumption=True
        ) == (145, pulse_update_5)

        # Test log rollover by updating log first before updating pulses
        tst_consumption.add_log(100, 3, (fixed_this_hour + td(hours=2)), 3333)
        assert tst_consumption.log_rollover
        assert tst_consumption.collected_pulses(
            fixed_this_hour, is_consumption=True
        ) == (145 + 2222 + 3333, pulse_update_5)
        pulse_update_6 = fixed_this_hour + td(hours=2, seconds=10)
        tst_consumption.update_pulse_counter(321, 0, pulse_update_6)
        assert not tst_consumption.log_rollover
        assert tst_consumption.collected_pulses(
            fixed_this_hour, is_consumption=True
        ) == (2222 + 3333 + 321, pulse_update_6)

    @freeze_time(dt.now())
    def test_pulse_collection_consumption_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Testing pulse collection class."""
        monkeypatch.setattr(pw_energy_pulses, "MAX_LOG_HOURS", 24)

        fixed_timestamp_utc = dt.now(UTC)
        fixed_this_hour = fixed_timestamp_utc.replace(minute=0, second=0, microsecond=0)

        # Import consumption logs
        tst_pc = pw_energy_pulses.PulseCollection(mac="0098765432101234")
        tst_pc.add_log(100, 1, fixed_this_hour - td(hours=5), 1000)
        assert tst_pc.log_addresses_missing is None
        tst_pc.add_log(99, 4, fixed_this_hour - td(hours=6), 750)
        assert tst_pc.log_addresses_missing == [99, 98, 97, 96, 95]
        tst_pc.add_log(99, 3, fixed_this_hour - td(hours=7), 3750)
        tst_pc.add_log(99, 2, fixed_this_hour - td(hours=8), 750)
        tst_pc.add_log(99, 1, fixed_this_hour - td(hours=9), 2750)
        assert tst_pc.log_addresses_missing == [98, 97, 96, 95]
        tst_pc.add_log(98, 4, fixed_this_hour - td(hours=10), 1750)
        assert tst_pc.log_addresses_missing == [98, 97, 96, 95]

        # test empty log prior
        tst_pc.add_empty_log(98, 3)
        assert tst_pc.log_addresses_missing == []

        tst_pc.add_log(100, 2, fixed_this_hour - td(hours=5), 1750)
        tst_pc.add_empty_log(100, 3)
        assert tst_pc.log_addresses_missing == []

        tst_pc.add_log(100, 3, fixed_this_hour - td(hours=4), 1750)
        assert tst_pc.log_addresses_missing == []

        tst_pc.add_log(101, 2, fixed_this_hour - td(hours=1), 1234)
        assert tst_pc.log_addresses_missing == [101, 100]

        tst_pc.add_log(101, 1, fixed_this_hour - td(hours=1), 1234)
        assert tst_pc.log_addresses_missing == [100]

    @freeze_time(dt.now())
    def test_pulse_collection_production(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Testing pulse collection class."""
        # Set log hours to 1 week
        monkeypatch.setattr(pw_energy_pulses, "MAX_LOG_HOURS", 168)

        fixed_timestamp_utc = dt.now(UTC)
        fixed_this_hour = fixed_timestamp_utc.replace(minute=0, second=0, microsecond=0)

        # Test consumption and production logs
        tst_production = pw_energy_pulses.PulseCollection(mac="0098765432101234")
        assert tst_production.log_addresses_missing is None
        assert tst_production.production_logging is None

        # Test consumption & production - Log import #1 - production
        # Missing addresses can not be determined yet
        test_timestamp = fixed_this_hour - td(hours=1)
        tst_production.add_log(200, 2, test_timestamp, -2000)
        assert tst_production.log_addresses_missing is None
        assert tst_production.production_logging is None

        # Test consumption & production - Log import #2 - consumption
        # production must be enabled & intervals are unknown
        # Log at address 200 is known and expect production logs too
        test_timestamp = fixed_this_hour - td(hours=1)
        tst_production.add_log(200, 1, test_timestamp, 0)
        assert tst_production.log_addresses_missing is None
        assert tst_production.log_interval_consumption is None
        assert tst_production.log_interval_production is None
        assert tst_production.production_logging

        # Test consumption & production - Log import #3 - production
        # Interval of consumption is not yet available
        test_timestamp = fixed_this_hour - td(hours=2)  # type: ignore[unreachable]
        tst_production.add_log(199, 4, test_timestamp, -2200)
        missing_check = list(range(199, 157, -1))
        assert tst_production.log_addresses_missing == missing_check
        assert tst_production.log_interval_consumption is None
        assert tst_production.log_interval_production == 60
        assert tst_production.production_logging

        # Test consumption & production - Log import #4
        # Interval of consumption is available
        test_timestamp = fixed_this_hour - td(hours=2)
        tst_production.add_log(199, 3, test_timestamp, 0)
        assert tst_production.log_addresses_missing == missing_check
        assert tst_production.log_interval_consumption == 60
        assert tst_production.log_interval_production == 60
        assert tst_production.production_logging

        pulse_update_1 = fixed_this_hour + td(minutes=5)
        tst_production.update_pulse_counter(0, -500, pulse_update_1)
        assert tst_production.collected_pulses(
            fixed_this_hour, is_consumption=True
        ) == (0, pulse_update_1)
        assert tst_production.collected_pulses(
            fixed_this_hour, is_consumption=False
        ) == (500, pulse_update_1)
        assert tst_production.collected_pulses(
            fixed_this_hour - td(hours=1), is_consumption=True
        ) == (0, pulse_update_1)
        assert tst_production.collected_pulses(
            fixed_this_hour - td(hours=2), is_consumption=True
        ) == (0 + 0, pulse_update_1)
        assert tst_production.collected_pulses(
            fixed_this_hour - td(hours=1), is_consumption=False
        ) == (500, pulse_update_1)
        assert tst_production.collected_pulses(
            fixed_this_hour - td(hours=2), is_consumption=False
        ) == (2000 + 500, pulse_update_1)

    _pulse_update = 0

    @freeze_time(dt.now())
    def test_log_address_rollover(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test log address rollover."""
        # Set log hours to 25
        monkeypatch.setattr(pw_energy_pulses, "MAX_LOG_HOURS", 24)

        fixed_timestamp_utc = dt.now(UTC)
        fixed_this_hour = fixed_timestamp_utc.replace(minute=0, second=0, microsecond=0)
        tst_pc = pw_energy_pulses.PulseCollection(mac="0098765432101234")
        tst_pc.add_log(2, 1, fixed_this_hour - td(hours=1), 3000)
        tst_pc.add_log(1, 4, fixed_this_hour - td(hours=2), 3000)
        tst_pc.add_log(1, 3, fixed_this_hour - td(hours=3), 3000)
        assert tst_pc.log_addresses_missing == [6015, 6014, 6013, 6012, 1, 0]

        # test
        tst_pc = pw_energy_pulses.PulseCollection(mac="0098765432101234")
        tst_pc.add_log(2, 4, fixed_this_hour - td(hours=1), 0)  # prod
        tst_pc.add_log(2, 3, fixed_this_hour - td(hours=1), 23935)  # con
        tst_pc.add_log(2, 2, fixed_this_hour - td(hours=2), 0)  # prod
        tst_pc.add_log(2, 1, fixed_this_hour - td(hours=2), 10786)  # con
        # <-- logs 0 & 1 are missing for hours 3, 4, 5 & 6 -->
        tst_pc.add_log(6015, 4, fixed_this_hour - td(hours=7), 0)
        tst_pc.add_log(6015, 3, fixed_this_hour - td(hours=7), 11709)
        tst_pc.add_log(6015, 2, fixed_this_hour - td(hours=8), 0)
        tst_pc.add_log(6015, 1, fixed_this_hour - td(hours=8), 10382)
        assert tst_pc.log_addresses_missing == [1, 0]

    def pulse_update(
        self, timestamp: dt, is_consumption: bool
    ) -> tuple[int | None, dt | None]:
        """Update pulse helper for energy counter."""
        self._pulse_update += 1
        if self._pulse_update == 1:
            return (None, None)
        if self._pulse_update == 2:
            return (None, timestamp + td(minutes=5))
        if self._pulse_update == 3:
            return (2222, None)
        if self._pulse_update == 4:
            return (2222, timestamp + td(minutes=10))
        return (3333, timestamp + td(minutes=15, seconds=10))

    @freeze_time(dt.now())
    def test_energy_counter(self) -> None:
        """Testing energy counter class."""
        pulse_col_mock = Mock()
        pulse_col_mock.collected_pulses.side_effect = self.pulse_update

        fixed_timestamp_utc = dt.now(UTC)
        fixed_timestamp_local = dt.now(dt.now(UTC).astimezone().tzinfo)

        _LOGGER.debug(
            "test_energy_counter | fixed_timestamp-utc = %s", str(fixed_timestamp_utc)
        )

        calibration_config = pw_energy_calibration.EnergyCalibration(1, 2, 3, 4)

        # Initialize hour counter
        energy_counter_init = pw_energy_counter.EnergyCounter(
            pw_energy_counter.EnergyType.CONSUMPTION_HOUR, "fake mac"
        )
        assert energy_counter_init.calibration is None
        energy_counter_init.calibration = calibration_config

        assert energy_counter_init.energy is None
        assert energy_counter_init.is_consumption
        assert energy_counter_init.last_reset is None
        assert energy_counter_init.last_update is None

        # First update (None, None)
        assert energy_counter_init.update(pulse_col_mock) == (None, None)
        assert energy_counter_init.energy is None
        assert energy_counter_init.last_reset is None
        assert energy_counter_init.last_update is None
        # Second update (None, timestamp)
        assert energy_counter_init.update(pulse_col_mock) == (None, None)
        assert energy_counter_init.energy is None
        assert energy_counter_init.last_reset is None
        assert energy_counter_init.last_update is None
        # Third update (2222, None)
        assert energy_counter_init.update(pulse_col_mock) == (None, None)
        assert energy_counter_init.energy is None
        assert energy_counter_init.last_reset is None
        assert energy_counter_init.last_update is None

        # forth update (2222, timestamp + 00:10:00)
        reset_timestamp = fixed_timestamp_local.replace(
            minute=0, second=0, microsecond=0
        )
        assert energy_counter_init.update(pulse_col_mock) == (
            0.07204743061527973,
            reset_timestamp,
        )
        assert energy_counter_init.energy == 0.07204743061527973
        assert energy_counter_init.last_reset == reset_timestamp
        assert energy_counter_init.last_update == reset_timestamp + td(minutes=10)

        # fifth update (3333, timestamp + 00:15:10)
        assert energy_counter_init.update(pulse_col_mock) == (
            0.08263379198066137,
            reset_timestamp,
        )
        assert energy_counter_init.energy == 0.08263379198066137
        assert energy_counter_init.last_reset == reset_timestamp
        assert energy_counter_init.last_update == reset_timestamp + td(
            minutes=15, seconds=10
        )

        # Production hour
        energy_counter_p_h = pw_energy_counter.EnergyCounter(
            pw_energy_counter.EnergyType.PRODUCTION_HOUR, "fake mac"
        )
        assert not energy_counter_p_h.is_consumption

    @pytest.mark.asyncio
    async def test_creating_request_messages(self) -> None:
        """Test create request message."""
        node_network_info_request = pw_requests.StickNetworkInfoRequest(
            self.dummy_fn, None
        )
        assert node_network_info_request.serialize() == b"\x05\x05\x03\x030001CAAB\r\n"
        circle_plus_connect_request = pw_requests.CirclePlusConnectRequest(
            self.dummy_fn, b"1111222233334444"
        )
        assert (
            circle_plus_connect_request.serialize()
            == b"\x05\x05\x03\x030004000000000000000000001111222233334444BDEC\r\n"
        )
        node_add_request = pw_requests.NodeAddRequest(
            self.dummy_fn, b"1111222233334444", True
        )
        assert (
            node_add_request.serialize()
            == b"\x05\x05\x03\x0300070111112222333344445578\r\n"
        )
        node_reset_request = pw_requests.NodeResetRequest(
            self.dummy_fn, b"1111222233334444", 2, 5
        )
        assert (
            node_reset_request.serialize()
            == b"\x05\x05\x03\x030009111122223333444402053D5C\r\n"
        )
        node_image_activate_request = pw_requests.NodeImageActivateRequest(
            self.dummy_fn, b"1111222233334444", 2, 5
        )
        assert (
            node_image_activate_request.serialize()
            == b"\x05\x05\x03\x03000F1111222233334444020563AA\r\n"
        )
        circle_log_data_request = pw_requests.CircleLogDataRequest(
            self.dummy_fn,
            b"1111222233334444",
            dt(2022, 5, 3, 0, 0, 0),
            dt(2022, 5, 10, 23, 0, 0),
        )
        assert (
            circle_log_data_request.serialize()
            == b"\x05\x05\x03\x030014111122223333444416050B4016053804AD3A\r\n"
        )
        node_remove_request = pw_requests.NodeRemoveRequest(
            self.dummy_fn, b"1111222233334444", "5555666677778888"
        )
        assert (
            node_remove_request.serialize()
            == b"\x05\x05\x03\x03001C11112222333344445555666677778888D89C\r\n"
        )

        circle_plus_realtimeclock_request = (
            pw_requests.CirclePlusRealTimeClockSetRequest(
                self.dummy_fn, b"1111222233334444", dt(2022, 5, 4, 3, 1, 0)
            )
        )
        assert (
            circle_plus_realtimeclock_request.serialize()
            == b"\x05\x05\x03\x030028111122223333444400010302040522ADE2\r\n"
        )

        node_sleep_config_request = pw_requests.NodeSleepConfigRequest(
            self.dummy_fn,
            b"1111222233334444",
            5,  # Duration in seconds the SED will be awake for receiving commands
            360,  # Duration in minutes the SED will be in sleeping mode and not able to respond any command
            1440,  # Interval in minutes the node will wake up and able to receive commands
            False,  # Enable/disable clock sync
            0,  # Duration in minutes the node synchronize its clock
        )
        assert (
            node_sleep_config_request.serialize()
            == b"\x05\x05\x03\x030050111122223333444405016805A00000008C9D\r\n"
        )

        scan_configure_request = pw_requests.ScanConfigureRequest(
            self.dummy_fn,
            b"1111222233334444",
            5,  # Delay in minutes when signal is send when no motion is detected
            30,  # Sensitivity of Motion sensor (High, Medium, Off)
            False,  # Daylight override to only report motion when lightlevel is below calibrated level
        )
        assert (
            scan_configure_request.serialize()
            == b"\x05\x05\x03\x03010111112222333344441E0005025E\r\n"
        )

    @pytest.mark.asyncio
    async def test_stick_network_down(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Testing timeout Circle + discovery."""
        mock_serial = MockSerial(
            {
                b"\x05\x05\x03\x03000AB43C\r\n": (
                    "STICK INIT",
                    b"000000C1",  # Success ack
                    b"0011"  # msg_id
                    + b"0123456789012345"  # stick mac
                    + b"00"  # unknown1
                    + b"00"  # network_is_online
                    + b"0098765432101234"  # circle_plus_mac
                    + b"4321"  # network_id
                    + b"00",  # unknown2
                ),
            }
        )
        monkeypatch.setattr(
            pw_connection_manager,
            "create_serial_connection",
            mock_serial.mock_connection,
        )
        monkeypatch.setattr(pw_sender, "STICK_TIME_OUT", 0.2)
        monkeypatch.setattr(pw_requests, "NODE_TIME_OUT", 1.0)
        stick = pw_stick.Stick(port="test_port", cache_enabled=False)
        await stick.connect()
        with pytest.raises(pw_exceptions.StickError):
            await stick.initialize()
        await stick.disconnect()

    def fake_env(self, env: str) -> str | None:
        """Fake environment."""
        if env == "APPDATA":
            return "appdata_folder"
        if env == "~":
            return "/home/usr"
        return None

    def os_path_join(self, str_a: str, str_b: str) -> str:
        """Join path."""
        return f"{str_a}/{str_b}"

    @pytest.mark.asyncio
    async def test_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa:  PLR0915
        """Test PlugwiseCache class."""
        monkeypatch.setattr(pw_helpers_cache, "os_name", "nt")
        monkeypatch.setattr(pw_helpers_cache, "os_getenv", self.fake_env)
        monkeypatch.setattr(pw_helpers_cache, "os_path_expand_user", self.fake_env)
        monkeypatch.setattr(pw_helpers_cache, "os_path_join", self.os_path_join)

        async def aiofiles_os_remove(file: str) -> None:
            if file == "mock_folder_that_exists/file_that_exists.ext":
                return
            if file == "mock_folder_that_exists/nodes.cache":
                return
            if file == "mock_folder_that_exists/0123456789ABCDEF.cache":
                return
            raise pw_exceptions.CacheError("Invalid file")

        async def makedirs(cache_dir: str, exist_ok: bool) -> None:
            if cache_dir == "mock_folder_that_exists":
                return
            if cache_dir == "non_existing_folder":
                return
            raise pw_exceptions.CacheError("wrong folder to create")

        monkeypatch.setattr(pw_helpers_cache, "aiofiles_os_remove", aiofiles_os_remove)
        monkeypatch.setattr(pw_helpers_cache, "makedirs", makedirs)
        monkeypatch.setattr(pw_helpers_cache, "ospath", MockOsPath())

        pw_cache = pw_helpers_cache.PlugwiseCache("test-file", "non_existing_folder")
        assert not pw_cache.initialized
        assert pw_cache.cache_root_directory == "non_existing_folder"
        with pytest.raises(pw_exceptions.CacheError):
            await pw_cache.initialize_cache()
        assert not pw_cache.initialized

        # test create folder
        await pw_cache.initialize_cache(create_root_folder=True)
        assert pw_cache.initialized

        # Windows
        pw_cache = pw_helpers_cache.PlugwiseCache(
            "file_that_exists.ext", "mock_folder_that_exists"
        )
        pw_cache.cache_root_directory = "mock_folder_that_exists"
        assert not pw_cache.initialized

        # Test raising CacheError when cache is not initialized yet
        with pytest.raises(pw_exceptions.CacheError):
            await pw_cache.read_cache()
            await pw_cache.write_cache({"key1": "value z"})

        await pw_cache.initialize_cache()
        assert pw_cache.initialized

        # Mock reading
        mock_read_data = [
            "key1;value a\n",
            "key2;first duplicate is ignored\n\r",
            "key2;value b|value c\n\r",
            "key3;value d \r\n",
        ]
        file_chunks_iter = iter(mock_read_data)
        mock_file_stream = MagicMock(readlines=lambda *args, **kwargs: file_chunks_iter)
        with patch("aiofiles.threadpool.sync_open", return_value=mock_file_stream):
            assert await pw_cache.read_cache() == {
                "key1": "value a",
                "key2": "value b|value c",
                "key3": "value d",
            }
        file_chunks_iter = iter(mock_read_data)
        mock_file_stream = MagicMock(readlines=lambda *args, **kwargs: file_chunks_iter)
        with patch("aiofiles.threadpool.sync_open", return_value=mock_file_stream):
            await pw_cache.write_cache({"key1": "value z"})
            mock_file_stream.writelines.assert_called_with(
                ["key1;value z\n", "key2;value b|value c\n", "key3;value d\n"]
            )

        file_chunks_iter = iter(mock_read_data)
        mock_file_stream = MagicMock(readlines=lambda *args, **kwargs: file_chunks_iter)
        with patch("aiofiles.threadpool.sync_open", return_value=mock_file_stream):
            await pw_cache.write_cache({"key4": "value e"}, rewrite=True)
            mock_file_stream.writelines.assert_called_with(
                [
                    "key4;value e\n",
                ]
            )

        monkeypatch.setattr(pw_helpers_cache, "os_name", "linux")
        pw_cache = pw_helpers_cache.PlugwiseCache(
            "file_that_exists.ext", "mock_folder_that_exists"
        )
        pw_cache.cache_root_directory = "mock_folder_that_exists"
        assert not pw_cache.initialized
        await pw_cache.initialize_cache()
        assert pw_cache.initialized
        await pw_cache.delete_cache()
        pw_cache.cache_root_directory = "mock_folder_that_does_not_exists"
        await pw_cache.delete_cache()
        pw_cache = pw_helpers_cache.PlugwiseCache(
            "file_that_exists.ext", "mock_folder_that_does_not_exists"
        )
        await pw_cache.delete_cache()

    @pytest.mark.asyncio
    async def test_network_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test NetworkRegistrationCache class."""
        monkeypatch.setattr(pw_helpers_cache, "os_name", "nt")
        monkeypatch.setattr(pw_helpers_cache, "os_getenv", self.fake_env)
        monkeypatch.setattr(pw_helpers_cache, "os_path_expand_user", self.fake_env)
        monkeypatch.setattr(pw_helpers_cache, "os_path_join", self.os_path_join)

        async def aiofiles_os_remove(file: str) -> None:
            if file == "mock_folder_that_exists/file_that_exists.ext":
                return
            if file == "mock_folder_that_exists/nodes.cache":
                return
            if file == "mock_folder_that_exists/0123456789ABCDEF.cache":
                return
            raise pw_exceptions.CacheError("Invalid file")

        async def makedirs(cache_dir: str, exist_ok: bool) -> None:
            if cache_dir == "mock_folder_that_exists":
                return
            if cache_dir == "non_existing_folder":
                return
            raise pw_exceptions.CacheError("wrong folder to create")

        monkeypatch.setattr(pw_helpers_cache, "aiofiles_os_remove", aiofiles_os_remove)
        monkeypatch.setattr(pw_helpers_cache, "makedirs", makedirs)
        monkeypatch.setattr(pw_helpers_cache, "ospath", MockOsPath())

        pw_nw_cache = pw_network_cache.NetworkRegistrationCache(
            "mock_folder_that_exists"
        )
        await pw_nw_cache.initialize_cache()
        # test with invalid data
        mock_read_data = [
            "-1;0123456789ABCDEF;NodeType.CIRCLE_PLUS",
            "0;FEDCBA9876543210xxxNodeType.CIRCLE",
            "invalid129834765AFBECD|NodeType.CIRCLE",
        ]
        file_chunks_iter = iter(mock_read_data)
        mock_file_stream = MagicMock(readlines=lambda *args, **kwargs: file_chunks_iter)
        with patch("aiofiles.threadpool.sync_open", return_value=mock_file_stream):
            await pw_nw_cache.restore_cache()
            assert pw_nw_cache.registrations == {
                -1: ("0123456789ABCDEF", pw_api.NodeType.CIRCLE_PLUS),
            }

        # test with valid data
        mock_read_data = [
            "-1;0123456789ABCDEF;NodeType.CIRCLE_PLUS",
            "0;FEDCBA9876543210;NodeType.CIRCLE",
            "1;1298347650AFBECD;NodeType.SCAN",
            "2;;",
        ]
        file_chunks_iter = iter(mock_read_data)
        mock_file_stream = MagicMock(readlines=lambda *args, **kwargs: file_chunks_iter)
        with patch("aiofiles.threadpool.sync_open", return_value=mock_file_stream):
            await pw_nw_cache.restore_cache()
            assert pw_nw_cache.registrations == {
                -1: ("0123456789ABCDEF", pw_api.NodeType.CIRCLE_PLUS),
                0: ("FEDCBA9876543210", pw_api.NodeType.CIRCLE),
                1: ("1298347650AFBECD", pw_api.NodeType.SCAN),
                2: ("", None),
            }
        pw_nw_cache.update_registration(3, "1234ABCD4321FEDC", pw_api.NodeType.STEALTH)

        with patch("aiofiles.threadpool.sync_open", return_value=mock_file_stream):
            await pw_nw_cache.save_cache()
            mock_file_stream.writelines.assert_called_with(
                [
                    "-1;0123456789ABCDEF|NodeType.CIRCLE_PLUS\n",
                    "0;FEDCBA9876543210|NodeType.CIRCLE\n",
                    "1;1298347650AFBECD|NodeType.SCAN\n",
                    "2;|\n",
                    "3;1234ABCD4321FEDC|NodeType.STEALTH\n",
                    "4;|\n",
                ]
                + [f"{address};|\n" for address in range(5, 64)]
            )
        assert pw_nw_cache.registrations == {
            -1: ("0123456789ABCDEF", pw_api.NodeType.CIRCLE_PLUS),
            0: ("FEDCBA9876543210", pw_api.NodeType.CIRCLE),
            1: ("1298347650AFBECD", pw_api.NodeType.SCAN),
            2: ("", None),
            3: ("1234ABCD4321FEDC", pw_api.NodeType.STEALTH),
        }

    @pytest.mark.asyncio
    async def test_node_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test NodeCache class."""
        monkeypatch.setattr(pw_helpers_cache, "ospath", MockOsPath())
        monkeypatch.setattr(pw_helpers_cache, "os_name", "nt")
        monkeypatch.setattr(pw_helpers_cache, "os_getenv", self.fake_env)
        monkeypatch.setattr(pw_helpers_cache, "os_path_expand_user", self.fake_env)
        monkeypatch.setattr(pw_helpers_cache, "os_path_join", self.os_path_join)

        node_cache = pw_node_cache.NodeCache(
            "0123456789ABCDEF", "mock_folder_that_exists"
        )
        await node_cache.initialize_cache()
        # test with invalid data
        mock_read_data = [
            "firmware;2011-6-27-8-52-18",
            "hardware;000004400107",
            "node_info_timestamp;2024-3-18-19-30-28",
            "node_type;2",
            "relay;True",
            "current_log_address;127",
            "calibration_gain_a;0.9903987646102905",
            "calibration_gain_b;-1.8206795857622637e-06",
            "calibration_noise;0.0",
            "calibration_tot;0.023882506415247917",
            "energy_collection;102:4:2024-3-14-19-0-0:47|102:3:2024-3-14-18-0-0:48|102:2:2024-3-14-17-0-0:45",
        ]
        file_chunks_iter = iter(mock_read_data)
        mock_file_stream = MagicMock(readlines=lambda *args, **kwargs: file_chunks_iter)
        with patch("aiofiles.threadpool.sync_open", return_value=mock_file_stream):
            await node_cache.restore_cache()
        assert node_cache.states == {
            "firmware": "2011-6-27-8-52-18",
            "hardware": "000004400107",
            "node_info_timestamp": "2024-3-18-19-30-28",
            "node_type": "2",
            "relay": "True",
            "current_log_address": "127",
            "calibration_gain_a": "0.9903987646102905",
            "calibration_gain_b": "-1.8206795857622637e-06",
            "calibration_noise": "0.0",
            "calibration_tot": "0.023882506415247917",
            "energy_collection": "102:4:2024-3-14-19-0-0:47|102:3:2024-3-14-18-0-0:48|102:2:2024-3-14-17-0-0:45",
        }
        assert node_cache.get_state("hardware") == "000004400107"
        node_cache.update_state("current_log_address", "128")
        assert node_cache.get_state("current_log_address") == "128"
        node_cache.remove_state("calibration_gain_a")
        assert node_cache.get_state("calibration_gain_a") is None

        with patch("aiofiles.threadpool.sync_open", return_value=mock_file_stream):
            await node_cache.save_cache()
            mock_file_stream.writelines.assert_called_with(
                [
                    "firmware;2011-6-27-8-52-18\n",
                    "hardware;000004400107\n",
                    "node_info_timestamp;2024-3-18-19-30-28\n",
                    "node_type;2\n",
                    "relay;True\n",
                    "current_log_address;128\n",
                    "calibration_gain_b;-1.8206795857622637e-06\n",
                    "calibration_noise;0.0\n",
                    "calibration_tot;0.023882506415247917\n",
                    "energy_collection;102:4:2024-3-14-19-0-0:47|102:3:2024-3-14-18-0-0:48|102:2:2024-3-14-17-0-0:45\n",
                ]
            )

    @pytest.mark.asyncio
    async def test_base_node(self, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa:  PLR0915
        """Testing properties of base node."""
        mock_stick_controller = MockStickController()

        async def load_callback(event: pw_api.NodeEvent, mac: str) -> None:  # type: ignore[name-defined]
            """Load callback for event."""

        test_node = pw_sed.PlugwiseBaseNode(
            "1298347650AFBECD", 1, mock_stick_controller, load_callback
        )

        # Validate base node properties which are always set
        assert not test_node.is_battery_powered

        # Validate to raise exception when node is not yet loaded
        with pytest.raises(pw_exceptions.NodeError):
            assert await test_node.set_awake_duration(5) is not None

        with pytest.raises(pw_exceptions.NodeError):
            assert test_node.battery_config is not None

        with pytest.raises(pw_exceptions.NodeError):
            assert await test_node.set_clock_interval(5) is not None

        with pytest.raises(pw_exceptions.NodeError):
            assert await test_node.set_clock_sync(False) is not None

        with pytest.raises(pw_exceptions.NodeError):
            assert await test_node.set_sleep_duration(5) is not None

        with pytest.raises(pw_exceptions.NodeError):
            assert await test_node.set_motion_daylight_mode(True) is not None

        with pytest.raises(pw_exceptions.NodeError):
            assert (
                await test_node.set_motion_sensitivity_level(
                    pw_api.MotionSensitivity.HIGH
                )
                is not None
            )

        with pytest.raises(pw_exceptions.NodeError):
            assert await test_node.set_motion_reset_timer(5) is not None

        # Validate to raise NotImplementedError calling load() at basenode
        with pytest.raises(NotImplementedError):
            await test_node.load()
        # Mark test node as loaded
        test_node._loaded = True  # pylint: disable=protected-access

        # Validate to raise exception when feature is not supported
        with pytest.raises(pw_exceptions.FeatureError):
            assert await test_node.set_awake_duration(5) is not None

        with pytest.raises(pw_exceptions.FeatureError):
            assert test_node.battery_config is not None

        with pytest.raises(pw_exceptions.FeatureError):
            assert await test_node.set_clock_interval(5) is not None

        with pytest.raises(pw_exceptions.FeatureError):
            assert await test_node.set_clock_sync(False) is not None

        with pytest.raises(pw_exceptions.FeatureError):
            assert await test_node.set_sleep_duration(5) is not None

        with pytest.raises(pw_exceptions.FeatureError):
            assert await test_node.set_motion_daylight_mode(True) is not None

        with pytest.raises(pw_exceptions.FeatureError):
            assert (
                await test_node.set_motion_sensitivity_level(
                    pw_api.MotionSensitivity.HIGH
                )
                is not None
            )

        with pytest.raises(pw_exceptions.FeatureError):
            assert await test_node.set_motion_reset_timer(5) is not None

        # Add battery feature to test raising not implemented
        # for battery related properties
        test_node._features += (pw_api.NodeFeature.BATTERY,)  # pylint: disable=protected-access
        with pytest.raises(NotImplementedError):
            assert await test_node.set_awake_duration(5) is not None

        with pytest.raises(NotImplementedError):
            assert test_node.battery_config is not None

        with pytest.raises(NotImplementedError):
            assert await test_node.set_clock_interval(5) is not None

        with pytest.raises(NotImplementedError):
            assert await test_node.set_clock_sync(False) is not None

        with pytest.raises(NotImplementedError):
            assert await test_node.set_sleep_duration(5) is not None

        test_node._features += (pw_api.NodeFeature.MOTION,)  # pylint: disable=protected-access
        with pytest.raises(NotImplementedError):
            assert await test_node.set_motion_daylight_mode(True) is not None
        with pytest.raises(NotImplementedError):
            assert (
                await test_node.set_motion_sensitivity_level(
                    pw_api.MotionSensitivity.HIGH
                )
                is not None
            )
        with pytest.raises(NotImplementedError):
            assert await test_node.set_motion_reset_timer(5) is not None

        assert not test_node.cache_enabled
        assert test_node.mac == "1298347650AFBECD"

    @pytest.mark.asyncio
    async def test_sed_node(self, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa:  PLR0915
        """Testing properties of SED."""

        def fake_cache(dummy: object, setting: str) -> str | None:  # noqa: PLR0911
            """Fake cache retrieval."""
            if setting == pw_node.CACHE_FIRMWARE:
                return "2011-6-27-8-55-44"
            if setting == pw_node.CACHE_HARDWARE:
                return "080007"
            if setting == pw_node.CACHE_NODE_TYPE:
                return "6"
            if setting == pw_node.CACHE_NODE_INFO_TIMESTAMP:
                return "2024-12-7-1-0-0"
            if setting == pw_sed.CACHE_AWAKE_DURATION:
                return "20"
            if setting == pw_sed.CACHE_CLOCK_INTERVAL:
                return "12600"
            if setting == pw_sed.CACHE_CLOCK_SYNC:
                return "True"
            if setting == pw_sed.CACHE_MAINTENANCE_INTERVAL:
                return "43200"
            if setting == pw_sed.CACHE_SLEEP_DURATION:
                return "120"
            return None

        monkeypatch.setattr(pw_node.PlugwiseBaseNode, "_get_cache", fake_cache)
        mock_stick_controller = MockStickController()

        async def load_callback(event: pw_api.NodeEvent, mac: str) -> None:  # type: ignore[name-defined]
            """Load callback for event."""

        test_sed = pw_sed.NodeSED(
            "1298347650AFBECD", 1, mock_stick_controller, load_callback
        )
        assert not test_sed.cache_enabled

        # Validate SED properties raise exception when node is not yet loaded
        with pytest.raises(pw_exceptions.NodeError):
            assert test_sed.battery_config is not None

        with pytest.raises(pw_exceptions.NodeError):
            assert test_sed.battery_config is not None

        with pytest.raises(pw_exceptions.NodeError):
            assert await test_sed.set_maintenance_interval(10)

        assert test_sed.node_info.is_battery_powered
        assert test_sed.is_battery_powered
        assert await test_sed.load()
        assert sorted(test_sed.features) == sorted(
            (
                pw_api.NodeFeature.AVAILABLE,
                pw_api.NodeFeature.BATTERY,
                pw_api.NodeFeature.INFO,
                pw_api.NodeFeature.PING,
            )
        )

        sed_config_accepted = pw_responses.NodeResponse()
        sed_config_accepted.deserialize(
            construct_message(b"000000F65555555555555555", b"0000")
        )
        sed_config_failed = pw_responses.NodeResponse()
        sed_config_failed.deserialize(
            construct_message(b"000000F75555555555555555", b"0000")
        )

        # test awake duration
        assert test_sed.awake_duration == 10
        assert test_sed.battery_config.awake_duration == 10
        with pytest.raises(ValueError):
            assert await test_sed.set_awake_duration(0)
        with pytest.raises(ValueError):
            assert await test_sed.set_awake_duration(256)
        assert not await test_sed.set_awake_duration(10)
        assert not test_sed.sed_config_task_scheduled
        assert await test_sed.set_awake_duration(15)
        assert test_sed.sed_config_task_scheduled
        assert test_sed.battery_config.awake_duration == 15
        assert test_sed.awake_duration == 15

        # Restore to original settings after failed config
        awake_response1 = pw_responses.NodeAwakeResponse()
        awake_response1.deserialize(
            construct_message(b"004F555555555555555500", b"FFFE")
        )
        mock_stick_controller.send_response = sed_config_failed
        await test_sed._awake_response(awake_response1)  # pylint: disable=protected-access
        await asyncio.sleep(0.001)  # Ensure time for task to be executed
        assert not test_sed.sed_config_task_scheduled
        assert test_sed.battery_config.awake_duration == 10
        assert test_sed.awake_duration == 10

        # Successful config
        awake_response2 = pw_responses.NodeAwakeResponse()
        awake_response2.deserialize(
            construct_message(b"004F555555555555555500", b"FFFE")
        )
        awake_response2.timestamp = awake_response1.timestamp + td(
            seconds=pw_sed.AWAKE_RETRY
        )
        assert await test_sed.set_awake_duration(15)
        assert test_sed.sed_config_task_scheduled
        mock_stick_controller.send_response = sed_config_accepted
        await test_sed._awake_response(awake_response2)  # pylint: disable=protected-access
        await asyncio.sleep(0.001)  # Ensure time for task to be executed
        assert not test_sed.sed_config_task_scheduled
        assert test_sed.battery_config.awake_duration == 15
        assert test_sed.awake_duration == 15

        # test maintenance interval
        assert test_sed.maintenance_interval == 60
        assert test_sed.battery_config.maintenance_interval == 60
        with pytest.raises(ValueError):
            assert await test_sed.set_maintenance_interval(0)
        with pytest.raises(ValueError):
            assert await test_sed.set_maintenance_interval(65536)
        assert not await test_sed.set_maintenance_interval(60)
        assert await test_sed.set_maintenance_interval(30)
        assert test_sed.sed_config_task_scheduled
        awake_response3 = pw_responses.NodeAwakeResponse()
        awake_response3.deserialize(
            construct_message(b"004F555555555555555500", b"FFFE")
        )
        awake_response3.timestamp = awake_response2.timestamp + td(
            seconds=pw_sed.AWAKE_RETRY
        )
        await test_sed._awake_response(awake_response3)  # pylint: disable=protected-access
        await asyncio.sleep(0.001)  # Ensure time for task to be executed
        assert not test_sed.sed_config_task_scheduled
        assert test_sed.battery_config.maintenance_interval == 30
        assert test_sed.maintenance_interval == 30

        # test clock interval
        assert test_sed.clock_interval == 25200
        assert test_sed.battery_config.clock_interval == 25200
        with pytest.raises(ValueError):
            assert await test_sed.set_clock_interval(0)
        with pytest.raises(ValueError):
            assert await test_sed.set_clock_interval(65536)
        assert not await test_sed.set_clock_interval(25200)
        assert await test_sed.set_clock_interval(12600)
        assert test_sed.sed_config_task_scheduled
        awake_response4 = pw_responses.NodeAwakeResponse()
        awake_response4.deserialize(
            construct_message(b"004F555555555555555500", b"FFFE")
        )
        awake_response4.timestamp = awake_response3.timestamp + td(
            seconds=pw_sed.AWAKE_RETRY
        )
        await test_sed._awake_response(awake_response4)  # pylint: disable=protected-access
        await asyncio.sleep(0.001)  # Ensure time for task to be executed
        assert not test_sed.sed_config_task_scheduled
        assert test_sed.battery_config.clock_interval == 12600
        assert test_sed.clock_interval == 12600

        # test clock sync
        assert not test_sed.clock_sync
        assert not test_sed.battery_config.clock_sync
        assert not await test_sed.set_clock_sync(False)
        assert await test_sed.set_clock_sync(True)
        assert test_sed.sed_config_task_scheduled
        awake_response5 = pw_responses.NodeAwakeResponse()
        awake_response5.deserialize(
            construct_message(b"004F555555555555555500", b"FFFE")
        )
        awake_response5.timestamp = awake_response4.timestamp + td(
            seconds=pw_sed.AWAKE_RETRY
        )
        await test_sed._awake_response(awake_response5)  # pylint: disable=protected-access
        await asyncio.sleep(0.001)  # Ensure time for task to be executed
        assert not test_sed.sed_config_task_scheduled
        assert test_sed.battery_config.clock_sync
        assert test_sed.clock_sync

        # test sleep duration
        assert test_sed.sleep_duration == 60
        assert test_sed.battery_config.sleep_duration == 60
        with pytest.raises(ValueError):
            assert await test_sed.set_sleep_duration(0)
        with pytest.raises(ValueError):
            assert await test_sed.set_sleep_duration(65536)
        assert not await test_sed.set_sleep_duration(60)
        assert await test_sed.set_sleep_duration(120)
        assert test_sed.sed_config_task_scheduled
        awake_response6 = pw_responses.NodeAwakeResponse()
        awake_response6.deserialize(
            construct_message(b"004F555555555555555500", b"FFFE")
        )
        awake_response6.timestamp = awake_response5.timestamp + td(
            seconds=pw_sed.AWAKE_RETRY
        )
        await test_sed._awake_response(awake_response6)  # pylint: disable=protected-access
        await asyncio.sleep(0.001)  # Ensure time for task to be executed
        assert not test_sed.sed_config_task_scheduled
        assert test_sed.battery_config.sleep_duration == 120
        assert test_sed.sleep_duration == 120

    @pytest.mark.asyncio
    async def test_scan_node(self, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: PLR0915
        """Testing properties of scan."""

        def fake_cache(dummy: object, setting: str) -> str | None:  # noqa: PLR0911 PLR0912
            """Fake cache retrieval."""
            if setting == pw_node.CACHE_FIRMWARE:
                return "2011-6-27-8-55-44"
            if setting == pw_node.CACHE_HARDWARE:
                return "080007"
            if setting == pw_node.CACHE_NODE_TYPE:
                return "6"
            if setting == pw_node.CACHE_NODE_INFO_TIMESTAMP:
                return "2024-12-7-1-0-0"
            if setting == pw_sed.CACHE_AWAKE_DURATION:
                return "20"
            if setting == pw_sed.CACHE_CLOCK_INTERVAL:
                return "12600"
            if setting == pw_sed.CACHE_CLOCK_SYNC:
                return "True"
            if setting == pw_sed.CACHE_MAINTENANCE_INTERVAL:
                return "43200"
            if setting == pw_sed.CACHE_SLEEP_DURATION:
                return "120"
            if setting == pw_scan.CACHE_MOTION_STATE:
                return "False"
            if setting == pw_scan.CACHE_MOTION_TIMESTAMP:
                return "2024-12-6-1-0-0"
            if setting == pw_scan.CACHE_MOTION_RESET_TIMER:
                return "10"
            if setting == pw_scan.CACHE_SCAN_SENSITIVITY:
                return "MEDIUM"
            if setting == pw_scan.CACHE_SCAN_DAYLIGHT_MODE:
                return "True"
            return None

        monkeypatch.setattr(pw_node.PlugwiseBaseNode, "_get_cache", fake_cache)
        mock_stick_controller = MockStickController()

        scan_config_accepted = pw_responses.NodeAckResponse()
        scan_config_accepted.deserialize(
            construct_message(b"0100555555555555555500BE", b"0000")
        )
        scan_config_failed = pw_responses.NodeAckResponse()
        scan_config_failed.deserialize(
            construct_message(b"0100555555555555555500BF", b"0000")
        )

        async def load_callback(event: pw_api.NodeEvent, mac: str) -> None:  # type: ignore[name-defined]
            """Load callback for event."""

        test_scan = pw_scan.PlugwiseScan(
            "1298347650AFBECD", 1, mock_stick_controller, load_callback
        )
        assert not test_scan.cache_enabled
        node_info = pw_api.NodeInfoMessage(
            current_logaddress_pointer=None,
            firmware=dt(2011, 6, 27, 8, 55, 44, tzinfo=UTC),
            hardware="080007",
            node_type=None,
            relay_state=None,
        )
        await test_scan.update_node_details(node_info)
        assert await test_scan.load()

        # test motion reset timer
        assert test_scan.reset_timer == 10
        assert test_scan.motion_config.reset_timer == 10
        with pytest.raises(ValueError):
            assert await test_scan.set_motion_reset_timer(0)
        with pytest.raises(ValueError):
            assert await test_scan.set_motion_reset_timer(256)
        assert not await test_scan.set_motion_reset_timer(10)
        assert not test_scan.scan_config_task_scheduled
        assert await test_scan.set_motion_reset_timer(15)
        assert test_scan.scan_config_task_scheduled
        assert test_scan.reset_timer == 15
        assert test_scan.motion_config.reset_timer == 15

        # Restore to original settings after failed config
        awake_response1 = pw_responses.NodeAwakeResponse()
        awake_response1.deserialize(
            construct_message(b"004F555555555555555500", b"FFFE")
        )
        mock_stick_controller.send_response = scan_config_failed
        await test_scan._awake_response(awake_response1)  # pylint: disable=protected-access
        await asyncio.sleep(0.001)  # Ensure time for task to be executed
        assert not test_scan.scan_config_task_scheduled

        # Successful config
        awake_response2 = pw_responses.NodeAwakeResponse()
        awake_response2.deserialize(
            construct_message(b"004F555555555555555500", b"FFFE")
        )
        awake_response2.timestamp = awake_response1.timestamp + td(
            seconds=pw_sed.AWAKE_RETRY
        )
        mock_stick_controller.send_response = scan_config_accepted
        assert await test_scan.set_motion_reset_timer(25)
        assert test_scan.scan_config_task_scheduled
        await test_scan._awake_response(awake_response2)  # pylint: disable=protected-access
        await asyncio.sleep(0.001)  # Ensure time for task to be executed
        assert not test_scan.scan_config_task_scheduled
        assert test_scan.reset_timer == 25
        assert test_scan.motion_config.reset_timer == 25

        # test motion daylight mode
        assert not test_scan.daylight_mode
        assert not test_scan.motion_config.daylight_mode
        assert not await test_scan.set_motion_daylight_mode(False)
        assert not test_scan.scan_config_task_scheduled
        assert await test_scan.set_motion_daylight_mode(True)
        assert test_scan.scan_config_task_scheduled
        awake_response3 = pw_responses.NodeAwakeResponse()
        awake_response3.deserialize(
            construct_message(b"004F555555555555555500", b"FFFE")
        )
        awake_response3.timestamp = awake_response2.timestamp + td(
            seconds=pw_sed.AWAKE_RETRY
        )
        await test_scan._awake_response(awake_response3)  # pylint: disable=protected-access
        await asyncio.sleep(0.001)  # Ensure time for task to be executed
        assert not test_scan.scan_config_task_scheduled
        assert test_scan.daylight_mode
        assert test_scan.motion_config.daylight_mode

        # test motion sensitivity level
        assert test_scan.sensitivity_level == pw_api.MotionSensitivity.MEDIUM
        assert (
            test_scan.motion_config.sensitivity_level == pw_api.MotionSensitivity.MEDIUM
        )
        assert not await test_scan.set_motion_sensitivity_level(
            pw_api.MotionSensitivity.MEDIUM
        )
        assert not test_scan.scan_config_task_scheduled
        assert await test_scan.set_motion_sensitivity_level(
            pw_api.MotionSensitivity.HIGH
        )
        assert test_scan.scan_config_task_scheduled
        awake_response4 = pw_responses.NodeAwakeResponse()
        awake_response4.deserialize(
            construct_message(b"004F555555555555555500", b"FFFE")
        )
        awake_response4.timestamp = awake_response3.timestamp + td(
            seconds=pw_sed.AWAKE_RETRY
        )
        await test_scan._awake_response(awake_response4)  # pylint: disable=protected-access
        await asyncio.sleep(0.001)  # Ensure time for task to be executed
        assert not test_scan.scan_config_task_scheduled
        assert test_scan.sensitivity_level == pw_api.MotionSensitivity.HIGH
        assert (
            test_scan.motion_config.sensitivity_level == pw_api.MotionSensitivity.HIGH
        )

        # scan with cache enabled
        mock_stick_controller.send_response = None
        test_scan = pw_scan.PlugwiseScan(
            "1298347650AFBECD", 1, mock_stick_controller, load_callback
        )
        node_info = pw_api.NodeInfoMessage(
            current_logaddress_pointer=None,
            firmware=dt(2011, 6, 27, 8, 55, 44, tzinfo=UTC),
            hardware="080007",
            node_type=None,
            relay_state=None,
        )
        await test_scan.update_node_details(node_info)
        test_scan.cache_enabled = True
        assert await test_scan.load()
        assert sorted(test_scan.features) == sorted(
            (
                pw_api.NodeFeature.AVAILABLE,
                pw_api.NodeFeature.BATTERY,
                pw_api.NodeFeature.INFO,
                pw_api.NodeFeature.MOTION,
                pw_api.NodeFeature.MOTION_CONFIG,
                pw_api.NodeFeature.PING,
            )
        )

        state = await test_scan.get_state(
            (
                pw_api.NodeFeature.AVAILABLE,
                pw_api.NodeFeature.BATTERY,
                pw_api.NodeFeature.INFO,
                pw_api.NodeFeature.MOTION,
                pw_api.NodeFeature.MOTION_CONFIG,
            )
        )
        assert not state[pw_api.NodeFeature.AVAILABLE].state

    @pytest.mark.asyncio
    async def test_switch_node(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Testing properties of switch."""

        def fake_cache(dummy: object, setting: str) -> str | None:  # noqa: PLR0911
            """Fake cache retrieval."""
            if setting == pw_node.CACHE_FIRMWARE:
                return "2011-5-13-7-26-54"
            if setting == pw_node.CACHE_HARDWARE:
                return "080029"
            if setting == pw_node.CACHE_NODE_TYPE:
                return "3"
            if setting == pw_node.CACHE_NODE_INFO_TIMESTAMP:
                return "2024-12-7-1-0-0"
            if setting == pw_sed.CACHE_AWAKE_DURATION:
                return "15"
            if setting == pw_sed.CACHE_CLOCK_INTERVAL:
                return "14600"
            if setting == pw_sed.CACHE_CLOCK_SYNC:
                return "False"
            if setting == pw_sed.CACHE_MAINTENANCE_INTERVAL:
                return "900"
            if setting == pw_sed.CACHE_SLEEP_DURATION:
                return "180"
            return None

        monkeypatch.setattr(pw_node.PlugwiseBaseNode, "_get_cache", fake_cache)
        mock_stick_controller = MockStickController()

        async def load_callback(event: pw_api.NodeEvent, mac: str) -> None:  # type: ignore[name-defined]
            """Load callback for event."""

        test_switch = pw_switch.PlugwiseSwitch(
            "1298347650AFBECD", 1, mock_stick_controller, load_callback
        )
        assert not test_switch.cache_enabled

        assert sorted(test_switch.features) == sorted(
            (
                pw_api.NodeFeature.AVAILABLE,
                pw_api.NodeFeature.INFO,
                pw_api.NodeFeature.PING,
            )
        )
        node_info = pw_api.NodeInfoMessage(
            current_logaddress_pointer=None,
            firmware=dt(2011, 6, 27, 9, 4, 10, tzinfo=UTC),
            hardware="070051",
            node_type=None,
            relay_state=None,
        )
        await test_switch.update_node_details(node_info)
        assert await test_switch.load()

        #  Switch specific defaults
        assert test_switch.switch is False

        # switch with cache enabled
        test_switch = pw_switch.PlugwiseSwitch(
            "1298347650AFBECD", 1, mock_stick_controller, load_callback
        )
        node_info = pw_api.NodeInfoMessage(
            current_logaddress_pointer=None,
            firmware=dt(2011, 6, 27, 9, 4, 10, tzinfo=UTC),
            hardware="070051",
            node_type=None,
            relay_state=None,
        )
        await test_switch.update_node_details(node_info)
        test_switch.cache_enabled = True
        assert test_switch.cache_enabled is True
        assert await test_switch.load()
        assert sorted(test_switch.features) == sorted(
            (
                pw_api.NodeFeature.AVAILABLE,
                pw_api.NodeFeature.BATTERY,
                pw_api.NodeFeature.INFO,
                pw_api.NodeFeature.PING,
                pw_api.NodeFeature.SWITCH,
            )
        )

        state = await test_switch.get_state(
            (
                pw_api.NodeFeature.AVAILABLE,
                pw_api.NodeFeature.BATTERY,
                pw_api.NodeFeature.INFO,
                pw_api.NodeFeature.SWITCH,
            )
        )
        assert not state[pw_api.NodeFeature.AVAILABLE].state

    @pytest.mark.asyncio
    async def test_node_discovery_and_load(  # noqa: PLR0915
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Testing discovery of nodes."""
        mock_serial = MockSerial(None)
        monkeypatch.setattr(
            pw_connection_manager,
            "create_serial_connection",
            mock_serial.mock_connection,
        )
        monkeypatch.setattr(pw_sender, "STICK_TIME_OUT", 0.2)
        monkeypatch.setattr(pw_requests, "NODE_TIME_OUT", 2.0)
        monkeypatch.setattr(pw_helpers_cache, "ospath", MockOsPath())
        monkeypatch.setattr(pw_helpers_cache, "os_name", "nt")
        monkeypatch.setattr(pw_helpers_cache, "os_getenv", self.fake_env)
        monkeypatch.setattr(pw_helpers_cache, "os_path_expand_user", self.fake_env)
        monkeypatch.setattr(pw_helpers_cache, "os_path_join", self.os_path_join)
        mock_read_data = [""]
        file_chunks_iter = iter(mock_read_data)
        mock_file_stream = MagicMock(readlines=lambda *args, **kwargs: file_chunks_iter)

        stick = pw_stick.Stick("test_port", cache_enabled=True)
        await stick.connect()
        with patch("aiofiles.threadpool.sync_open", return_value=mock_file_stream):
            await stick.initialize()
            await stick.discover_nodes(load=True)

        assert len(stick.nodes) == 6

        assert stick.nodes["0098765432101234"].is_loaded
        assert stick.nodes["0098765432101234"].name == "Circle + 01234"
        assert stick.nodes["0098765432101234"].node_info.firmware == dt(
            2011, 6, 27, 8, 47, 37, tzinfo=UTC
        )
        assert stick.nodes["0098765432101234"].node_info.version == "070073"
        assert stick.nodes["0098765432101234"].node_info.model == "Circle +"
        assert stick.nodes["0098765432101234"].node_info.model_type == "type F"
        assert stick.nodes["0098765432101234"].node_info.name == "Circle + 01234"
        assert stick.nodes["0098765432101234"].available
        assert not stick.nodes["0098765432101234"].node_info.is_battery_powered
        assert not stick.nodes["0098765432101234"].is_battery_powered
        assert stick.nodes["0098765432101234"].network_address == -1
        assert stick.nodes["0098765432101234"].cache_folder == ""
        assert not stick.nodes["0098765432101234"].cache_folder_create
        assert stick.nodes["0098765432101234"].cache_enabled

        # Check an unsupported state feature raises an error
        with pytest.raises(pw_exceptions.NodeError):
            await stick.nodes["0098765432101234"].get_state(
                (pw_api.NodeFeature.MOTION,)
            )

        # Get state
        get_state_timestamp = dt.now(UTC).replace(minute=0, second=0, microsecond=0)
        state = await stick.nodes["0098765432101234"].get_state(
            (
                pw_api.NodeFeature.AVAILABLE,
                pw_api.NodeFeature.PING,
                pw_api.NodeFeature.INFO,
                pw_api.NodeFeature.RELAY,
                pw_api.NodeFeature.RELAY_LOCK,
            )
        )

        # Check Available
        assert state[pw_api.NodeFeature.AVAILABLE].state
        assert (
            state[pw_api.NodeFeature.AVAILABLE].last_seen.replace(
                minute=0, second=0, microsecond=0
            )
            == get_state_timestamp
        )

        # Check Ping
        assert state[pw_api.NodeFeature.PING].rssi_in == 69
        assert state[pw_api.NodeFeature.PING].rssi_out == 70
        assert state[pw_api.NodeFeature.PING].rtt == 1074
        assert (
            state[pw_api.NodeFeature.PING].timestamp.replace(
                minute=0, second=0, microsecond=0
            )
            == get_state_timestamp
        )
        assert stick.nodes["0098765432101234"].ping_stats.rssi_in == 69
        assert stick.nodes["0098765432101234"].ping_stats.rssi_out == 70
        assert stick.nodes["0098765432101234"].ping_stats.rtt == 1074
        assert (
            stick.nodes["0098765432101234"].ping_stats.timestamp.replace(
                minute=0, second=0, microsecond=0
            )
            == get_state_timestamp
        )

        # Check INFO
        assert state[pw_api.NodeFeature.INFO].mac == "0098765432101234"
        assert state[pw_api.NodeFeature.INFO].zigbee_address == -1
        assert not state[pw_api.NodeFeature.INFO].is_battery_powered
        assert sorted(state[pw_api.NodeFeature.INFO].features) == sorted(
            (
                pw_api.NodeFeature.AVAILABLE,
                pw_api.NodeFeature.CIRCLE,
                pw_api.NodeFeature.CIRCLEPLUS,
                pw_api.NodeFeature.INFO,
                pw_api.NodeFeature.PING,
                pw_api.NodeFeature.RELAY,
                pw_api.NodeFeature.RELAY_LOCK,
                pw_api.NodeFeature.ENERGY,
                pw_api.NodeFeature.POWER,
            )
        )
        assert state[pw_api.NodeFeature.INFO].firmware == dt(
            2011, 6, 27, 8, 47, 37, tzinfo=UTC
        )
        assert state[pw_api.NodeFeature.INFO].name == "Circle + 01234"
        assert state[pw_api.NodeFeature.INFO].model == "Circle +"
        assert state[pw_api.NodeFeature.INFO].model_type == "type F"
        assert state[pw_api.NodeFeature.INFO].node_type == pw_api.NodeType.CIRCLE_PLUS
        assert (
            state[pw_api.NodeFeature.INFO].timestamp.replace(
                minute=0, second=0, microsecond=0
            )
            == get_state_timestamp
        )
        assert state[pw_api.NodeFeature.INFO].version == "070073"

        assert state[pw_api.NodeFeature.RELAY].state
        assert not state[pw_api.NodeFeature.RELAY_LOCK].state

        # Check 1111111111111111
        get_state_timestamp = dt.now(UTC).replace(minute=0, second=0, microsecond=0)
        state = await stick.nodes["1111111111111111"].get_state(
            (
                pw_api.NodeFeature.PING,
                pw_api.NodeFeature.INFO,
                pw_api.NodeFeature.RELAY,
                pw_api.NodeFeature.RELAY_LOCK,
            )
        )

        assert state[pw_api.NodeFeature.INFO].mac == "1111111111111111"
        assert state[pw_api.NodeFeature.INFO].zigbee_address == 0
        assert not state[pw_api.NodeFeature.INFO].is_battery_powered
        assert state[pw_api.NodeFeature.INFO].version == "070140"
        assert state[pw_api.NodeFeature.INFO].node_type == pw_api.NodeType.CIRCLE
        assert (
            state[pw_api.NodeFeature.INFO].timestamp.replace(
                minute=0, second=0, microsecond=0
            )
            == get_state_timestamp
        )
        assert sorted(state[pw_api.NodeFeature.INFO].features) == sorted(
            (
                pw_api.NodeFeature.AVAILABLE,
                pw_api.NodeFeature.CIRCLE,
                pw_api.NodeFeature.INFO,
                pw_api.NodeFeature.PING,
                pw_api.NodeFeature.RELAY,
                pw_api.NodeFeature.RELAY_LOCK,
                pw_api.NodeFeature.ENERGY,
                pw_api.NodeFeature.POWER,
            )
        )
        assert state[pw_api.NodeFeature.AVAILABLE].state
        assert state[pw_api.NodeFeature.RELAY].state
        assert not state[pw_api.NodeFeature.RELAY_LOCK].state

        # region Scan
        self.test_node_awake = asyncio.Future()
        unsub_awake = stick.subscribe_to_node_events(
            node_event_callback=self.node_awake,
            events=(pw_api.NodeEvent.AWAKE,),
        )
        mock_serial.inject_message(b"004F555555555555555500", b"FFFE")
        assert await self.test_node_awake
        unsub_awake()

        assert stick.nodes["5555555555555555"].node_info.firmware == dt(
            2011, 6, 27, 8, 55, 44, tzinfo=UTC
        )
        assert stick.nodes["5555555555555555"].node_info.version == "080007"
        assert stick.nodes["5555555555555555"].node_info.model == "Scan"
        assert stick.nodes["5555555555555555"].node_info.model_type is None
        assert stick.nodes["5555555555555555"].available
        assert stick.nodes["5555555555555555"].node_info.is_battery_powered
        assert sorted(stick.nodes["5555555555555555"].features) == sorted(
            (
                pw_api.NodeFeature.AVAILABLE,
                pw_api.NodeFeature.BATTERY,
                pw_api.NodeFeature.INFO,
                pw_api.NodeFeature.PING,
                pw_api.NodeFeature.MOTION,
                pw_api.NodeFeature.MOTION_CONFIG,
            )
        )
        state = await stick.nodes["5555555555555555"].get_state(
            (
                pw_api.NodeFeature.AVAILABLE,
                pw_api.NodeFeature.BATTERY,
                pw_api.NodeFeature.INFO,
                pw_api.NodeFeature.MOTION,
                pw_api.NodeFeature.MOTION_CONFIG,
            )
        )
        assert state[pw_api.NodeFeature.AVAILABLE].state
        assert state[pw_api.NodeFeature.BATTERY].maintenance_interval == 60
        assert state[pw_api.NodeFeature.BATTERY].awake_duration == 10
        assert not state[pw_api.NodeFeature.BATTERY].clock_sync
        assert state[pw_api.NodeFeature.BATTERY].clock_interval == 25200
        assert state[pw_api.NodeFeature.BATTERY].sleep_duration == 60

        # Motion
        self.test_motion_on = asyncio.Future()
        self.test_motion_off = asyncio.Future()
        unsub_motion = stick.nodes["5555555555555555"].subscribe_to_feature_update(
            node_feature_callback=self.node_motion_state,
            features=(pw_api.NodeFeature.MOTION,),
        )
        # Inject motion message to trigger a 'motion on' event
        mock_serial.inject_message(b"005655555555555555550001", b"FFFF")
        motion_on = await self.test_motion_on
        assert motion_on
        assert stick.nodes["5555555555555555"].motion

        # Inject motion message to trigger a 'motion off' event
        mock_serial.inject_message(b"005655555555555555550000", b"FFFF")
        motion_off = await self.test_motion_off
        assert not motion_off
        assert not stick.nodes["5555555555555555"].motion
        unsub_motion()
        # endregion

        # region Switch
        self.test_node_loaded = asyncio.Future()
        unsub_loaded = stick.subscribe_to_node_events(
            node_event_callback=self.node_loaded,
            events=(pw_api.NodeEvent.LOADED,),
        )
        mock_serial.inject_message(b"004F888888888888888800", b"FFFE")
        assert await self.test_node_loaded
        unsub_loaded()

        assert stick.nodes["8888888888888888"].node_info.firmware == dt(
            2011, 6, 27, 9, 4, 10, tzinfo=UTC
        )
        assert stick.nodes["8888888888888888"].node_info.version == "070051"
        assert stick.nodes["8888888888888888"].node_info.model == "Switch"
        assert stick.nodes["8888888888888888"].node_info.model_type is None
        assert stick.nodes["8888888888888888"].available
        assert stick.nodes["8888888888888888"].node_info.is_battery_powered
        assert sorted(stick.nodes["8888888888888888"].features) == sorted(
            (
                pw_api.NodeFeature.AVAILABLE,
                pw_api.NodeFeature.BATTERY,
                pw_api.NodeFeature.INFO,
                pw_api.NodeFeature.PING,
                pw_api.NodeFeature.SWITCH,
            )
        )
        state = await stick.nodes["8888888888888888"].get_state(
            (
                pw_api.NodeFeature.AVAILABLE,
                pw_api.NodeFeature.BATTERY,
                pw_api.NodeFeature.INFO,
                pw_api.NodeFeature.SWITCH,
            )
        )
        assert state[pw_api.NodeFeature.AVAILABLE].state
        assert state[pw_api.NodeFeature.BATTERY].maintenance_interval == 60
        assert state[pw_api.NodeFeature.BATTERY].awake_duration == 10
        assert not state[pw_api.NodeFeature.BATTERY].clock_sync
        assert state[pw_api.NodeFeature.BATTERY].clock_interval == 25200
        assert state[pw_api.NodeFeature.BATTERY].sleep_duration == 60
        # endregion

        # test disable cache
        assert stick.cache_enabled
        stick.cache_enabled = False
        assert not stick.cache_enabled

        # test changing cache_folder
        assert stick.cache_folder == ""
        stick.cache_folder = "mock_folder_that_exists"
        assert stick.cache_folder == "mock_folder_that_exists"

        with patch("aiofiles.threadpool.sync_open", return_value=mock_file_stream):
            await stick.disconnect()
        await asyncio.sleep(1)
