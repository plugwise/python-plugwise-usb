import aiofiles
import asyncio
from concurrent import futures
from datetime import datetime as dt, timedelta as td, timezone as tz
import importlib
import logging
from unittest import mock
from unittest.mock import Mock

import crcmod
from freezegun import freeze_time
import pytest


crc_fun = crcmod.mkCrcFun(0x11021, rev=False, initCrc=0x0000, xorOut=0x0000)

pw_stick = importlib.import_module("plugwise_usb")
pw_api = importlib.import_module("plugwise_usb.api")
pw_exceptions = importlib.import_module("plugwise_usb.exceptions")
pw_connection = importlib.import_module("plugwise_usb.connection")
pw_connection_manager = importlib.import_module(
    "plugwise_usb.connection.manager"
)
pw_network = importlib.import_module("plugwise_usb.network")
pw_receiver = importlib.import_module("plugwise_usb.connection.receiver")
pw_sender = importlib.import_module("plugwise_usb.connection.sender")
pw_constants = importlib.import_module("plugwise_usb.constants")
pw_requests = importlib.import_module("plugwise_usb.messages.requests")
pw_responses = importlib.import_module("plugwise_usb.messages.responses")
pw_userdata = importlib.import_module("testdata.stick")
pw_energy_counter = importlib.import_module(
    "plugwise_usb.nodes.helpers.counter"
)
pw_energy_calibration = importlib.import_module(
    "plugwise_usb.nodes.helpers"
)
pw_energy_pulses = importlib.import_module("plugwise_usb.nodes.helpers.pulses")

_LOGGER = logging.getLogger(__name__)
_LOGGER.setLevel(logging.DEBUG)


def inc_seq_id(seq_id: bytes) -> bytes:
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
    """construct plugwise message."""
    body = data[:4] + seq_id + data[4:]
    return (
        pw_constants.MESSAGE_HEADER
        + body
        + bytes("%04X" % crc_fun(body), pw_constants.UTF8)
        + pw_constants.MESSAGE_FOOTER
    )


class DummyTransport:
    def __init__(self, loop, test_data=None) -> None:
        self._loop = loop
        self._msg = 0
        self._seq_id = b"1233"
        self.protocol_data_received = None
        self._processed = []
        self._first_response = test_data
        self._second_response = test_data
        if test_data is None:
            self._first_response = pw_userdata.RESPONSE_MESSAGES
            self._second_response = pw_userdata.SECOND_RESPONSE_MESSAGES
        self.random_extra_byte = 0

    def is_closing(self) -> bool:
        return False

    def write(self, data: bytes) -> None:
        log = None
        if data in self._processed:
            log, ack, response = self._second_response.get(
                data, (None, None, None)
            )
        if log is None:
            log, ack, response = self._first_response.get(
                data, (None, None, None)
            )
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
        self.message_response(ack, self._seq_id)
        self._processed.append(data)
        if response is None:
            return
        self._loop.create_task(
            # 0.5,
            self._delayed_response(response, self._seq_id)
        )

    async def _delayed_response(self, data: bytes, seq_id: bytes) -> None:
        await asyncio.sleep(0.5)
        self.message_response(data, seq_id)

    def message_response(self, data: bytes, seq_id: bytes) -> None:
        self.random_extra_byte += 1
        if self.random_extra_byte > 25:
            self.protocol_data_received(b"\x83")
            self.random_extra_byte = 0
            self.protocol_data_received(
                construct_message(data, seq_id) + b"\x83"
            )
        else:
            self.protocol_data_received(construct_message(data, seq_id))

    def close(self) -> None:
        pass


class MockSerial:
    def __init__(self, custom_response) -> None:
        self.custom_response = custom_response
        self._protocol = None
        self._transport = None

    async def mock_connection(self, loop, protocol_factory, **kwargs):
        """Mock connection with dummy connection."""
        self._protocol = protocol_factory()
        self._transport = DummyTransport(loop, self.custom_response)
        self._transport.protocol_data_received = self._protocol.data_received
        loop.call_soon_threadsafe(
            self._protocol.connection_made, self._transport
        )
        return self._transport, self._protocol


class TestStick:

    @pytest.mark.asyncio
    async def test_sorting_request_messages(self):
        """Test request message priority sorting"""

        node_add_request = pw_requests.NodeAddRequest(
            b"1111222233334444", True
        )
        await asyncio.sleep(0.001)
        relay_switch_request = pw_requests.CircleRelaySwitchRequest(
            b"1234ABCD12341234", True
        )
        await asyncio.sleep(0.001)
        circle_plus_allow_joining_request = pw_requests.CirclePlusAllowJoiningRequest(
            True
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
    async def test_stick_connect_without_port(self):
        """Test connecting to stick without port config"""
        stick = pw_stick.Stick()
        assert stick.accept_join_request is None
        assert stick.nodes == {}
        assert stick.joined_nodes is None
        with pytest.raises(pw_exceptions.StickError):
            assert stick.mac_stick
            assert stick.mac_coordinator
            assert stick.network_id
        assert not stick.network_discovered
        assert not stick.network_state
        unsub_connect = stick.subscribe_to_stick_events(
            stick_event_callback=lambda x: print(x),
            events=(pw_api.StickEvent.CONNECTED,),
        )
        unsub_nw_online = stick.subscribe_to_stick_events(
            stick_event_callback=lambda x: print(x),
            events=(pw_api.StickEvent.NETWORK_ONLINE,),
        )
        with pytest.raises(pw_exceptions.StickError):
            await stick.connect()
            stick.port = "null"
            await stick.connect()

    @pytest.mark.asyncio
    async def test_stick_reconnect(self, monkeypatch):
        """Test connecting to stick while already connected"""
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
    async def test_stick_connect_without_response(self, monkeypatch):
        """Test connecting to stick without response"""
        monkeypatch.setattr(
            pw_connection_manager,
            "create_serial_connection",
            MockSerial(
                {
                    b"dummy": (
                        "no response",
                        b"0000",
                        None,
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

    @pytest.mark.asyncio
    async def test_stick_connect_timeout(self, monkeypatch):
        """Test connecting to stick"""
        monkeypatch.setattr(
            pw_connection_manager,
            "create_serial_connection",
            MockSerial(
                {
                    b"\x05\x05\x03\x03000AB43C\r\n": (
                        "STICK INIT timeout",
                        b"000000E1",  # Timeout ack
                        None,  #
                    ),
                }
            ).mock_connection,
        )
        monkeypatch.setattr(pw_requests, "NODE_TIME_OUT", 5)
        stick = pw_stick.Stick()
        await stick.connect("test_port")
        with pytest.raises(pw_exceptions.StickTimeout):
            await stick.initialize()
        await stick.disconnect()

    @pytest.mark.asyncio
    async def test_stick_connect(self, monkeypatch):
        """Test connecting to stick"""
        monkeypatch.setattr(
            pw_connection_manager,
            "create_serial_connection",
            MockSerial(None).mock_connection,
        )
        stick = pw_stick.Stick(port="test_port", cache_enabled=False)
        await stick.connect("test_port")
        await stick.initialize()
        assert stick.mac_stick == "0123456789012345"
        assert stick.mac_coordinator == "0098765432101234"
        assert not stick.network_discovered
        assert stick.network_state
        assert stick.network_id == 17185
        assert stick.accept_join_request is None
        # test failing of join requests without active discovery
        with pytest.raises(pw_exceptions.StickError):
            stick.accept_join_request = True
        await stick.disconnect()
        assert not stick.network_state
        with pytest.raises(pw_exceptions.StickError):
            assert stick.mac_stick

    async def disconnected(self, event):
        """Callback helper for stick disconnect event"""
        if event is pw_api.StickEvent.DISCONNECTED:
            self.test_disconnected.set_result(True)
        else:
            self.test_disconnected.set_exception(BaseException("Incorrect event"))

    @pytest.mark.asyncio
    async def test_stick_connection_lost(self, monkeypatch):
        """Test connecting to stick"""
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
        unsub_connect = stick.subscribe_to_stick_events(
            stick_event_callback=self.disconnected,
            events=(pw_api.StickEvent.DISCONNECTED,),
        )
        # Trigger disconnect
        mock_serial._protocol.connection_lost()
        assert await self.test_disconnected
        assert not stick.network_state
        unsub_connect()
        await stick.disconnect()

    async def node_discovered(self, event: pw_api.NodeEvent, mac: str):
        """Callback helper for node discovery"""
        if event == pw_api.NodeEvent.DISCOVERED:
            self.test_node_discovered.set_result(mac)
        else:
            self.test_node_discovered.set_exception(
                BaseException(
                    f"Invalid {event} event, expected " +
                    f"{pw_api.NodeEvent.DISCOVERED}"
                )
            )

    async def node_awake(self, event: pw_api.NodeEvent, mac: str):
        """Callback helper for node discovery"""
        if event == pw_api.NodeEvent.AWAKE:
            self.test_node_awake.set_result(mac)
        else:
            self.test_node_awake.set_exception(
                BaseException(
                    f"Invalid {event} event, expected " +
                    f"{pw_api.NodeEvent.AWAKE}"
                )
            )

    async def node_motion_state(
        self,
        feature: pw_api.NodeFeature,
        state: bool,
    ):
        """Callback helper for node_motion event"""
        if feature == pw_api.NodeFeature.MOTION:
            self.motion_on.set_result(state)
        else:
            self.motion_off.set_exception(
                BaseException(
                    f"Invalid {feature} feature, expected " +
                    f"{pw_api.NodeFeature.MOTION}"
                )
            )

    async def node_ping(
        self,
        feature: pw_api.NodeFeature,
        ping_collection,
    ):
        """Callback helper for node ping collection"""
        if feature == pw_api.NodeFeature.PING:
            self.node_ping_result.set_result(ping_collection)
        else:
            self.node_ping_result.set_exception(
                BaseException(
                    f"Invalid {feature} feature, expected " +
                    f"{pw_api.NodeFeature.PING}"
                )
            )

    @pytest.mark.asyncio
    async def test_stick_node_discovered_subscription(self, monkeypatch):
        """Testing "new_node" subscription for Scan"""
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
        stick.accept_join_request = True
        self.test_node_awake = asyncio.Future()
        unsub_awake = stick.subscribe_to_node_events(
            node_event_callback=self.node_awake,
            events=(pw_api.NodeEvent.AWAKE,),
        )
        self.test_node_discovered = asyncio.Future()
        unsub_discovered = stick.subscribe_to_node_events(
            node_event_callback=self.node_discovered,
            events=(pw_api.NodeEvent.DISCOVERED,),
        )
        # Inject NodeAwakeResponse message to trigger a 'node discovered' event
        mock_serial._transport.message_response(b"004F555555555555555500", b"FFFE")
        mac_awake_node = await self.test_node_awake
        assert mac_awake_node == "5555555555555555"
        unsub_awake()



# No tests available
class TestPlugwise:  # pylint: disable=attribute-defined-outside-init
    """Tests for Plugwise USB."""

    async def test_connect_legacy_anna(self):
        """No tests available."""
        assert True
