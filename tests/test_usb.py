import asyncio
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
        if response and self._msg == 0:
            self.message_response_at_once(ack, response, self._seq_id)
            self._processed.append(data)
        else:
            self.message_response(ack, self._seq_id)
            self._processed.append(data)
            if response is None:
                return
            self._loop.create_task(
                # 0.5,
                self._delayed_response(response, self._seq_id)
            )
        self._msg += 1

    async def _delayed_response(self, data: bytes, seq_id: bytes) -> None:
        import random
        delay = random.uniform(0.05, 0.25)
        await asyncio.sleep(delay)
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

    def message_response_at_once(self, ack: bytes, data: bytes, seq_id: bytes) -> None:
        self.random_extra_byte += 1
        if self.random_extra_byte > 25:
            self.protocol_data_received(b"\x83")
            self.random_extra_byte = 0
            self.protocol_data_received(
                construct_message(ack, seq_id) + construct_message(data, seq_id) + b"\x83"
            )
        else:
            self.protocol_data_received(construct_message(ack, seq_id) + construct_message(data, seq_id))

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
        with pytest.raises(pw_exceptions.StickError):
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
        async with asyncio.timeout(10.0):
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
        state: pw_api.MotionState,
    ):
        """Callback helper for node_motion event"""
        if feature == pw_api.NodeFeature.MOTION:
            if state.motion:
                self.motion_on.set_result(state.motion)
            else:
                self.motion_off.set_result(state.motion)
        else:
            if state.motion:
                self.motion_on.set_exception(
                    BaseException(
                        f"Invalid {feature} feature, expected " +
                        f"{pw_api.NodeFeature.MOTION}"
                    )
                )
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
        async with asyncio.timeout(15.0):
            await stick.discover_nodes(load=False)
        stick.accept_join_request = True
        self.test_node_awake = asyncio.Future()
        unsub_awake = stick.subscribe_to_node_events(
            node_event_callback=self.node_awake,
            events=(pw_api.NodeEvent.AWAKE,),
        )

        # Inject NodeAwakeResponse message to trigger a 'node discovered' event
        mock_serial._transport.message_response(b"004F555555555555555500", b"FFFE")
        mac_awake_node = await self.test_node_awake
        assert mac_awake_node == "5555555555555555"
        unsub_awake()

        assert await stick.nodes["5555555555555555"].load()
        assert stick.nodes["5555555555555555"].node_info.firmware == dt(
            2011, 6, 27, 8, 55, 44, tzinfo=tz.utc
        )
        assert stick.nodes["5555555555555555"].node_info.version == "000000070008"
        assert stick.nodes["5555555555555555"].node_info.model == "Scan"
        assert stick.nodes["5555555555555555"].available
        assert stick.nodes["5555555555555555"].node_info.battery_powered
        assert sorted(stick.nodes["5555555555555555"].features) == sorted(
            (
                pw_api.NodeFeature.AVAILABLE,
                pw_api.NodeFeature.INFO,
                pw_api.NodeFeature.PING,
                pw_api.NodeFeature.MOTION,
            )
        )

        # Motion
        self.motion_on = asyncio.Future()
        self.motion_off = asyncio.Future()
        unsub_motion = stick.nodes[
            "5555555555555555"
        ].subscribe_to_feature_update(
            node_feature_callback=self.node_motion_state,
            features=(pw_api.NodeFeature.MOTION,),
        )
        # Inject motion message to trigger a 'motion on' event
        mock_serial._transport.message_response(b"005655555555555555550001", b"FFFF")
        motion_on = await self.motion_on
        assert motion_on

        # Inject motion message to trigger a 'motion off' event
        mock_serial._transport.message_response(b"005655555555555555550000", b"FFFF")
        motion_off = await self.motion_off
        assert not motion_off
        unsub_motion()


        await stick.disconnect()

    async def node_join(self, event: pw_api.NodeEvent, mac: str):
        """Callback helper for node_join event"""
        if event == pw_api.NodeEvent.JOIN:
            self.test_node_join.set_result(mac)
        else:
            self.test_node_join.set_exception(
                BaseException(
                    f"Invalid {event} event, expected " +
                    f"{pw_api.NodeEvent.JOIN}"
                )
            )

    @pytest.mark.asyncio
    async def test_stick_node_join_subscription(self, monkeypatch):
        """Testing "new_node" subscription"""
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
        self.test_node_join = asyncio.Future()
        unusb_join = stick.subscribe_to_node_events(
            node_event_callback=self.node_join,
            events=(pw_api.NodeEvent.JOIN,),
        )

        # Inject node join request message
        mock_serial._transport.message_response(b"00069999999999999999", b"FFFC")
        mac_join_node = await self.test_node_join
        assert mac_join_node == "9999999999999999"
        unusb_join()
        await stick.disconnect()

    @pytest.mark.asyncio
    async def test_node_discovery(self, monkeypatch):
        """Testing discovery of nodes"""
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
        assert len(stick.nodes) == 6  # Discovered nodes
        await stick.disconnect()

    async def node_relay_state(
        self,
        feature: pw_api.NodeFeature,
        state: pw_api.RelayState,
    ):
        """Callback helper for relay event"""
        if feature == pw_api.NodeFeature.RELAY:
            if state.relay_state:
                self.test_relay_state_on.set_result(state.relay_state)
            else:
                self.test_relay_state_off.set_result(state.relay_state)
        else:
            self.test_relay_state_on.set_exception(
                BaseException(
                    f"Invalid {feature} feature, expected " +
                    f"{pw_api.NodeFeature.RELAY}"
                )
            )
            self.test_relay_state_off.set_exception(
                BaseException(
                    f"Invalid {feature} feature, expected " +
                    f"{pw_api.NodeFeature.RELAY}"
                )
            )

    async def node_init_relay_state(
        self,
        feature: pw_api.NodeFeature,
        state: bool,
    ):
        """Callback helper for relay event"""
        if feature == pw_api.NodeFeature.RELAY_INIT:
            if state:
                self.test_init_relay_state_on.set_result(state)
            else:
                self.test_init_relay_state_off.set_result(state)
        else:
            self.test_init_relay_state_on.set_exception(
                BaseException(
                    f"Invalid {feature} feature, expected " +
                    f"{pw_api.NodeFeature.RELAY_INIT}"
                )
            )
            self.test_init_relay_state_off.set_exception(
                BaseException(
                    f"Invalid {feature} feature, expected " +
                    f"{pw_api.NodeFeature.RELAY_INIT}"
                )
            )

    @pytest.mark.asyncio
    async def test_node_relay(self, monkeypatch):
        """Testing discovery of nodes"""
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

        # Manually load node
        assert await stick.nodes["0098765432101234"].load()

        self.test_relay_state_on = asyncio.Future()
        self.test_relay_state_off = asyncio.Future()
        unsub_relay = stick.nodes[
            "0098765432101234"
        ].subscribe_to_feature_update(
            node_feature_callback=self.node_relay_state,
            features=(pw_api.NodeFeature.RELAY,),
        )
        # Test sync switching from on to off
        assert stick.nodes["0098765432101234"].relay
        stick.nodes["0098765432101234"].relay = False
        assert not await self.test_relay_state_off
        assert not stick.nodes["0098765432101234"].relay

        # Test sync switching back from off to on
        stick.nodes["0098765432101234"].relay = True
        assert await self.test_relay_state_on
        assert stick.nodes["0098765432101234"].relay

        # Test async switching back from on to off
        self.test_relay_state_off = asyncio.Future()
        assert not await stick.nodes["0098765432101234"].switch_relay(False)
        assert not await self.test_relay_state_off
        assert not stick.nodes["0098765432101234"].relay

        # Test async switching back from off to on
        self.test_relay_state_on = asyncio.Future()
        assert await stick.nodes["0098765432101234"].switch_relay(True)
        assert await self.test_relay_state_on
        assert stick.nodes["0098765432101234"].relay

        unsub_relay()

        # Test non-support init relay state
        with pytest.raises(pw_exceptions.NodeError):
            assert stick.nodes["0098765432101234"].relay_init
        with pytest.raises(pw_exceptions.NodeError):
            await stick.nodes["0098765432101234"].switch_init_relay(True)
            await stick.nodes["0098765432101234"].switch_init_relay(False)

        # Test relay init
        # load node 2222222222222222 which has
        # the firmware with init relay feature
        assert await stick.nodes["2222222222222222"].load()
        self.test_init_relay_state_on = asyncio.Future()
        self.test_init_relay_state_off = asyncio.Future()
        unsub_inti_relay = stick.nodes[
            "0098765432101234"
        ].subscribe_to_feature_update(
            node_feature_callback=self.node_init_relay_state,
            features=(pw_api.NodeFeature.RELAY_INIT,),
        )
        # Test sync switching init_state from on to off
        assert stick.nodes["2222222222222222"].relay_init
        stick.nodes["2222222222222222"].relay_init = False
        assert not await self.test_init_relay_state_off
        assert not stick.nodes["2222222222222222"].relay_init

        # Test sync switching back init_state from off to on
        stick.nodes["2222222222222222"].relay_init = True
        assert await self.test_init_relay_state_on
        assert stick.nodes["2222222222222222"].relay_init

        # Test async switching back init_state from on to off
        self.test_init_relay_state_off = asyncio.Future()
        assert not await stick.nodes["2222222222222222"].switch_init_relay(False)
        assert not await self.test_init_relay_state_off
        assert not stick.nodes["2222222222222222"].relay_init

        # Test async switching back from off to on
        self.test_init_relay_state_on = asyncio.Future()
        assert await stick.nodes["2222222222222222"].switch_init_relay(True)
        assert await self.test_init_relay_state_on
        assert stick.nodes["2222222222222222"].relay_init

        unsub_inti_relay()

        await stick.disconnect()

    @freeze_time(dt.now())
    def test_pulse_collection(self):
        """Testing pulse collection class"""

        fixed_timestamp_utc = dt.now(tz.utc)
        fixed_this_hour = fixed_timestamp_utc.replace(
            minute=0, second=0, microsecond=0
        )
        missing_check = []

        # Test consumption logs
        tst_consumption = pw_energy_pulses.PulseCollection(mac="0098765432101234")
        assert tst_consumption.log_addresses_missing is None
        assert tst_consumption.production_logging is None

        # Test consumption - Log import #1
        # No missing addresses yet
        test_timestamp = fixed_this_hour - td(hours=1)
        tst_consumption.add_log(100, 1, test_timestamp, 1000)
        assert tst_consumption.log_interval_consumption is None
        assert tst_consumption.log_interval_production is None
        assert tst_consumption.production_logging is None
        assert tst_consumption.collected_pulses(test_timestamp, is_consumption=True) == (
            None,
            None,
        )
        assert tst_consumption.log_addresses_missing == missing_check

        # Test consumption - Log import #2, random log
        # return intermediate missing addresses
        test_timestamp = fixed_this_hour - td(hours=18)
        tst_consumption.add_log(95, 4, test_timestamp, 1000)
        missing_check += [99, 98, 97, 96]
        assert tst_consumption.log_interval_consumption is None
        assert tst_consumption.log_interval_production is None
        assert tst_consumption.production_logging is None
        assert tst_consumption.log_addresses_missing == missing_check

        # Test consumption - Log import #3
        # log next to existing with different timestamp
        # so 'production logging' should be marked as False now
        test_timestamp = fixed_this_hour - td(hours=19)
        tst_consumption.add_log(95, 3, test_timestamp, 1000)
        assert tst_consumption.log_interval_consumption is None
        assert tst_consumption.log_interval_production is None
        assert tst_consumption.production_logging is False
        assert tst_consumption.log_addresses_missing == missing_check

        # Test consumption - Log import #4, no change
        test_timestamp = fixed_this_hour - td(hours=20)
        tst_consumption.add_log(95, 2, test_timestamp, 1000)
        assert tst_consumption.log_interval_consumption is None
        assert tst_consumption.log_interval_production is None
        assert tst_consumption.production_logging is False
        assert tst_consumption.log_addresses_missing == missing_check

        # Test consumption - Log import #5
        # Complete log import for address 95 so it must drop from missing list
        test_timestamp = fixed_this_hour - td(hours=21)
        tst_consumption.add_log(95, 1, test_timestamp, 1000)

        assert tst_consumption.log_interval_consumption is None
        assert tst_consumption.log_interval_production is None
        assert tst_consumption.production_logging is False
        assert tst_consumption.log_addresses_missing == missing_check

        # Test consumption - Log import #6
        # Add before last log so interval of consumption must be determined
        test_timestamp = fixed_this_hour - td(hours=2)
        tst_consumption.add_log(99, 4, test_timestamp, 750)
        assert tst_consumption.log_interval_consumption == 60
        assert tst_consumption.log_interval_production is None
        assert tst_consumption.production_logging is False
        assert tst_consumption.log_addresses_missing == missing_check
        assert tst_consumption.collected_pulses(fixed_this_hour, is_consumption=True) == (
            None,
            None,
        )

        # Test consumption - pulse update #1
        pulse_update_1 = fixed_this_hour + td(minutes=5)
        tst_consumption.update_pulse_counter(1234, 0, pulse_update_1)
        assert tst_consumption.collected_pulses(fixed_this_hour, is_consumption=True) == (
            1234,
            pulse_update_1,
        )
        assert tst_consumption.collected_pulses(fixed_this_hour, is_consumption=False) == (
            None,
            None,
        )
        # Test consumption - pulse update #2
        pulse_update_2 = fixed_this_hour + td(minutes=7)
        test_timestamp = fixed_this_hour
        tst_consumption.update_pulse_counter(2345, 0, pulse_update_2)
        assert tst_consumption.collected_pulses(test_timestamp, is_consumption=True) == (
            2345,
            pulse_update_2,
        )
        assert tst_consumption.collected_pulses(test_timestamp, is_consumption=False) == (
            None,
            None,
        )
        # Test consumption - pulses + log (address=100, slot=1)
        test_timestamp = fixed_this_hour - td(hours=1)
        assert tst_consumption.collected_pulses(test_timestamp, is_consumption=True) == (
            2345 + 1000,
            pulse_update_2,
        )
        assert tst_consumption.collected_pulses(test_timestamp, is_consumption=False) == (
            None,
            None,
        )
        # Test consumption - pulses + logs (address=100, slot=1 & address=99, slot=4)
        test_timestamp = fixed_this_hour - td(hours=2)
        assert tst_consumption.collected_pulses(test_timestamp, is_consumption=True) == (
            2345 + 1000 + 750,
            pulse_update_2,
        )
        assert tst_consumption.collected_pulses(test_timestamp, is_consumption=False) == (
            None,
            None,
        )

        # Test consumption - pulses + missing logs
        test_timestamp = fixed_this_hour - td(hours=3)
        assert tst_consumption.collected_pulses(test_timestamp, is_consumption=True) == (
            None,
            None,
        )
        assert tst_consumption.collected_pulses(test_timestamp, is_consumption=False) == (
            None,
            None,
        )

        # Test consumption and production logs
        tst_production = pw_energy_pulses.PulseCollection(mac="0098765432101234")
        assert tst_production.log_addresses_missing is None
        assert tst_production.production_logging is None

        # Test consumption & production - Log import #1
        # Missing addresses must be populated
        test_timestamp = fixed_this_hour - td(hours=1)
        tst_production.add_log(200, 2, test_timestamp, 2000)
        missing_check = []
        assert tst_production.log_addresses_missing == missing_check
        assert tst_production.production_logging is None

        # Test consumption & production - Log import #2
        # production must be enabled & intervals are unknown
        # Log at address 200 is known and expect production logs too
        test_timestamp = fixed_this_hour - td(hours=1)
        tst_production.add_log(200, 1, test_timestamp, 1000)
        assert tst_production.log_addresses_missing == missing_check
        assert tst_production.log_interval_consumption == 0
        assert tst_production.log_interval_production is None
        assert tst_production.production_logging

        # Test consumption & production - Log import #3
        # Interval of production is not yet available
        test_timestamp = fixed_this_hour - td(hours=2)
        tst_production.add_log(199, 4, test_timestamp, 4000)
        missing_check = list(range(199, 157, -1))
        assert tst_production.log_addresses_missing == missing_check
        assert tst_production.log_interval_consumption == 0 # FIXME
        assert tst_production.log_interval_production is None
        assert tst_production.production_logging

        # Test consumption & production - Log import #4
        # Interval of consumption is available
        test_timestamp = fixed_this_hour - td(hours=2)
        tst_production.add_log(199, 3, test_timestamp, 3000)
        assert tst_production.log_addresses_missing == missing_check
        assert tst_production.log_interval_consumption == 0 # FIXME
        assert tst_production.log_interval_production == 60
        assert tst_production.production_logging

    _pulse_update = 0

    def pulse_update(self, timestamp: dt, is_consumption: bool):
        """Callback helper for pulse updates for energy counter"""
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
    def test_energy_counter(self):
        """Testing energy counter class"""
        pulse_col_mock = Mock()
        pulse_col_mock.collected_pulses.side_effect = self.pulse_update

        fixed_timestamp_utc = dt.now(tz.utc)
        fixed_timestamp_local = dt.now(dt.now(tz.utc).astimezone().tzinfo)

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
    async def test_creating_request_messages(self):

        node_network_info_request = pw_requests.StickNetworkInfoRequest()
        assert node_network_info_request.serialize() == b"\x05\x05\x03\x030001CAAB\r\n"
        circle_plus_connect_request = pw_requests.CirclePlusConnectRequest(
            b"1111222233334444"
        )
        assert (
            circle_plus_connect_request.serialize()
            == b"\x05\x05\x03\x030004000000000000000000001111222233334444BDEC\r\n"
        )
        node_add_request = pw_requests.NodeAddRequest(b"1111222233334444", True)
        assert (
            node_add_request.serialize()
            == b"\x05\x05\x03\x0300070111112222333344445578\r\n"
        )
        node_reset_request = pw_requests.NodeResetRequest(b"1111222233334444", 2, 5)
        assert (
            node_reset_request.serialize()
            == b"\x05\x05\x03\x030009111122223333444402053D5C\r\n"
        )
        node_image_activate_request = pw_requests.NodeImageActivateRequest(
            b"1111222233334444", 2, 5
        )
        assert (
            node_image_activate_request.serialize()
            == b"\x05\x05\x03\x03000F1111222233334444020563AA\r\n"
        )
        circle_log_data_request = pw_requests.CircleLogDataRequest(
            b"1111222233334444",
            dt(2022, 5, 3, 0, 0, 0),
            dt(2022, 5, 10, 23, 0, 0),
        )
        assert (
            circle_log_data_request.serialize()
            == b"\x05\x05\x03\x030014111122223333444416050B4016053804AD3A\r\n"
        )
        node_remove_request = pw_requests.NodeRemoveRequest(
            b"1111222233334444", "5555666677778888"
        )
        assert (
            node_remove_request.serialize()
            == b"\x05\x05\x03\x03001C11112222333344445555666677778888D89C\r\n"
        )

        circle_plus_realtimeclock_request = (
            pw_requests.CirclePlusRealTimeClockSetRequest(
                b"1111222233334444", dt(2022, 5, 4, 3, 1, 0)
            )
        )
        assert (
            circle_plus_realtimeclock_request.serialize()
            == b"\x05\x05\x03\x030028111122223333444400010302040522ADE2\r\n"
        )

        node_sleep_config_request = pw_requests.NodeSleepConfigRequest(
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
    async def test_stick_network_down(self, monkeypatch):
        """Testing timeout circle+ discovery"""
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
        monkeypatch.setattr(pw_requests, "NODE_TIME_OUT", 2.0)
        stick = pw_stick.Stick(port="test_port", cache_enabled=False)
        await stick.connect()
        with pytest.raises(pw_exceptions.StickError):
            await stick.initialize()

