from datetime import UTC, datetime, timedelta
import importlib

pw_constants = importlib.import_module("plugwise_usb.constants")

# test using utc timezone
utc_now = datetime.now(tz=UTC).replace(tzinfo=UTC)


# generate energy log timestamps with fixed hour timestamp used in tests
hour_timestamp = utc_now.replace(minute=0, second=0, microsecond=0)

LOG_TIMESTAMPS = {}
_one_hour = timedelta(hours=1)
for x in range(168):
    delta_month = hour_timestamp - hour_timestamp.replace(day=1, hour=0)
    LOG_TIMESTAMPS[x] = (
        bytes(("%%0%dX" % 2) % (hour_timestamp.year - 2000), pw_constants.UTF8)
        + bytes(("%%0%dX" % 2) % hour_timestamp.month, pw_constants.UTF8)
        + bytes(
            ("%%0%dX" % 4)
            % int((delta_month.days * 1440) + (delta_month.seconds / 60)),
            pw_constants.UTF8,
        )
    )
    hour_timestamp -= _one_hour


RESPONSE_MESSAGES = {
    b"\x05\x05\x03\x03000AB43C\r\n": (
        "STICK INIT",
        b"000000C1",  # Success ack
        b"0011"  # msg_id
        + b"0123456789012345"  # stick mac
        + b"00"  # unknown1
        + b"01"  # network_is_online
        + b"0098765432101234"  # circle_plus_mac
        + b"4321"  # network_id
        + b"FF",  # unknown2
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
    b"\x05\x05\x03\x03002300987654321012341AE2\r\n": (
        "Node Info of network controller 0098765432101234",
        b"000000C1",  # Success ack
        b"0024"  # msg_id
        + b"0098765432101234"  # mac
        + b"22026A68"  # datetime
        + b"00044280"  # log address 20
        + b"01"  # relay
        + b"01"  # hz
        + b"000000730007" # hw_ver
        + b"4E0843A9"  # fw_ver
        + b"01",  # node_type (Circle+)
    ),
    b"\x05\x05\x03\x030008014068\r\n":(
        "Reply to CirclePlusAllowJoiningRequest",
        b"000000C1",  # Success ack
        b"000000D9"  # JOIN_ACCEPTED
        + b"0098765432101234",  # mac
    ),
    b"\x05\x05\x03\x03000D0098765432101234C208\r\n": (
        "ping reply for 0098765432101234",
        b"000000C1",  # Success ack
        b"000E"
        + b"0098765432101234"
        + b"45"  # rssi in
        + b"46"  # rssi out
        + b"0432",  # roundtrip
    ),
    b"\x05\x05\x03\x030018009876543210123400BEF9\r\n": (
        "SCAN 00",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"1111111111111111" + b"00",
    ),
    b"\x05\x05\x03\x030018009876543210123401AED8\r\n": (
        "SCAN 01",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"2222222222222222" + b"01",
    ),
    b"\x05\x05\x03\x0300180098765432101234029EBB\r\n": (
        "SCAN 02",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"3333333333333333" + b"02",
    ),
    b"\x05\x05\x03\x0300180098765432101234038E9A\r\n": (
        "SCAN 03",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"4444444444444444" + b"03",
    ),
    b"\x05\x05\x03\x030018009876543210123404FE7D\r\n": (
        "SCAN 04",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"5555555555555555" + b"04",
    ),
    b"\x05\x05\x03\x030018009876543210123405EE5C\r\n": (
        "SCAN 05",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"6666666666666666" + b"05",
    ),
    b"\x05\x05\x03\x030018009876543210123406DE3F\r\n": (
        "SCAN 06",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"7777777777777777" + b"06",
    ),
    b"\x05\x05\x03\x030018009876543210123407CE1E\r\n": (
        "SWITCH 01",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"8888888888888888" + b"07",
    ),
    b"\x05\x05\x03\x0300180098765432101234083FF1\r\n": (
        "SCAN 08",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"08",
    ),
    b"\x05\x05\x03\x0300180098765432101234092FD0\r\n": (
        "SCAN 09",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"09",
    ),
    b"\x05\x05\x03\x03001800987654321012340AD04F\r\n": (
        "SCAN 10",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"0A",
    ),
    b"\x05\x05\x03\x03001800987654321012340BE02C\r\n": (
        "SCAN 11",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"0B",
    ),
    b"\x05\x05\x03\x03001800987654321012340CF00D\r\n": (
        "SCAN 12",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"0C",
    ),
    b"\x05\x05\x03\x03001800987654321012340D80EA\r\n": (
        "SCAN 13",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"0D",
    ),
    b"\x05\x05\x03\x03001800987654321012340E90CB\r\n": (
        "SCAN 14",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"0E",
    ),
    b"\x05\x05\x03\x03001800987654321012340FA0A8\r\n": (
        "SCAN 15",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"0F",
    ),
    b"\x05\x05\x03\x0300180098765432101234108DC8\r\n": (
        "SCAN 16",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"10",
    ),
    b"\x05\x05\x03\x0300180098765432101234119DE9\r\n": (
        "SCAN 17",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"11",
    ),
    b"\x05\x05\x03\x030018009876543210123412AD8A\r\n": (
        "SCAN 18",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"12",
    ),
    b"\x05\x05\x03\x030018009876543210123413BDAB\r\n": (
        "SCAN 19",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"13",
    ),
    b"\x05\x05\x03\x030018009876543210123414CD4C\r\n": (
        "SCAN 20",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"14",
    ),
    b"\x05\x05\x03\x030018009876543210123415DD6D\r\n": (
        "SCAN 21",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"15",
    ),
    b"\x05\x05\x03\x030018009876543210123416ED0E\r\n": (
        "SCAN 22",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"16",
    ),
    b"\x05\x05\x03\x030018009876543210123417FD2F\r\n": (
        "SCAN 23",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"17",
    ),
    b"\x05\x05\x03\x0300180098765432101234180CC0\r\n": (
        "SCAN 24",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"18",
    ),
    b"\x05\x05\x03\x0300180098765432101234191CE1\r\n": (
        "SCAN 25",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"19",
    ),
    b"\x05\x05\x03\x03001800987654321012341AE37E\r\n": (
        "SCAN 26",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"1A",
    ),
    b"\x05\x05\x03\x03001800987654321012341BD31D\r\n": (
        "SCAN 27",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"1B",
    ),
    b"\x05\x05\x03\x03001800987654321012341CC33C\r\n": (
        "SCAN 28",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"1C",
    ),
    b"\x05\x05\x03\x03001800987654321012341DB3DB\r\n": (
        "SCAN 29",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"1D",
    ),
    b"\x05\x05\x03\x03001800987654321012341EA3FA\r\n": (
        "SCAN 30",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"1E",
    ),
    b"\x05\x05\x03\x03001800987654321012341F9399\r\n": (
        "SCAN 31",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"1F",
    ),
    b"\x05\x05\x03\x030018009876543210123420D89B\r\n": (
        "SCAN 32",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"20",
    ),
    b"\x05\x05\x03\x030018009876543210123421C8BA\r\n": (
        "SCAN 33",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"21",
    ),
    b"\x05\x05\x03\x030018009876543210123422F8D9\r\n": (
        "SCAN 34",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"22",
    ),
    b"\x05\x05\x03\x030018009876543210123423E8F8\r\n": (
        "SCAN 35",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"23",
    ),
    b"\x05\x05\x03\x030018009876543210123424981F\r\n": (
        "SCAN 36",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"24",
    ),
    b"\x05\x05\x03\x030018009876543210123425883E\r\n": (
        "SCAN 37",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"25",
    ),
    b"\x05\x05\x03\x030018009876543210123426B85D\r\n": (
        "SCAN 38",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"26",
    ),
    b"\x05\x05\x03\x030018009876543210123427A87C\r\n": (
        "SCAN 39",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"27",
    ),
    b"\x05\x05\x03\x0300180098765432101234285993\r\n": (
        "SCAN 40",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"28",
    ),
    b"\x05\x05\x03\x03001800987654321012342949B2\r\n": (
        "SCAN 41",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"29",
    ),
    b"\x05\x05\x03\x03001800987654321012342AB62D\r\n": (
        "SCAN 42",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"2A",
    ),
    b"\x05\x05\x03\x03001800987654321012342B864E\r\n": (
        "SCAN 43",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"2B",
    ),
    b"\x05\x05\x03\x03001800987654321012342C966F\r\n": (
        "SCAN 44",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"2C",
    ),
    b"\x05\x05\x03\x03001800987654321012342DE688\r\n": (
        "SCAN 45",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"2D",
    ),
    b"\x05\x05\x03\x03001800987654321012342EF6A9\r\n": (
        "SCAN 46",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"2E",
    ),
    b"\x05\x05\x03\x03001800987654321012342FC6CA\r\n": (
        "SCAN 47",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"2F",
    ),
    b"\x05\x05\x03\x030018009876543210123430EBAA\r\n": (
        "SCAN 48",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"30",
    ),
    b"\x05\x05\x03\x030018009876543210123431FB8B\r\n": (
        "SCAN 49",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"31",
    ),
    b"\x05\x05\x03\x030018009876543210123432CBE8\r\n": (
        "SCAN 50",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"32",
    ),
    b"\x05\x05\x03\x030018009876543210123433DBC9\r\n": (
        "SCAN 51",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"33",
    ),
    b"\x05\x05\x03\x030018009876543210123434AB2E\r\n": (
        "SCAN 52",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"34",
    ),
    b"\x05\x05\x03\x030018009876543210123435BB0F\r\n": (
        "SCAN 53",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"35",
    ),
    b"\x05\x05\x03\x0300180098765432101234368B6C\r\n": (
        "SCAN 54",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"36",
    ),
    b"\x05\x05\x03\x0300180098765432101234379B4D\r\n": (
        "SCAN 55",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"37",
    ),
    b"\x05\x05\x03\x0300180098765432101234386AA2\r\n": (
        "SCAN 56",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"38",
    ),
    b"\x05\x05\x03\x0300180098765432101234397A83\r\n": (
        "SCAN 57",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"39",
    ),
    b"\x05\x05\x03\x03001800987654321012343A851C\r\n": (
        "SCAN 58",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"3A",
    ),
    b"\x05\x05\x03\x03001800987654321012343BB57F\r\n": (
        "SCAN 59",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"3B",
    ),
    b"\x05\x05\x03\x03001800987654321012343CA55E\r\n": (
        "SCAN 60",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"3C",
    ),
    b"\x05\x05\x03\x03001800987654321012343DD5B9\r\n": (
        "SCAN 61",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"3D",
    ),
    b"\x05\x05\x03\x03001800987654321012343EC598\r\n": (
        "SCAN 62",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"3E",
    ),
    b"\x05\x05\x03\x03001800987654321012343FF5FB\r\n": (
        "SCAN 63",
        b"000000C1",  # Success ack
        b"0019" + b"0098765432101234" + b"FFFFFFFFFFFFFFFF" + b"3F",
    ),
    b"\x05\x05\x03\x03000D11111111111111110B1A\r\n": (
        "ping reply for 1111111111111111",
        b"000000C1",  # Success ack
        b"000E"
        + b"1111111111111111"
        + b"42"  # rssi in 66
        + b"45"  # rssi out 69
        + b"0237",  # roundtrip 567
    ),
    b"\x05\x05\x03\x03000D222222222222222234E3\r\n": (
        "ping reply for 2222222222222222",
        b"000000C1",  # Success ack
        b"000E"
        + b"2222222222222222"  # mac
        + b"44"  # rssi in
        + b"55"  # rssi out
        + b"4321",  # roundtrip
    ),
    b"\x05\x05\x03\x03000D333333333333333321B4\r\n": (
        "ping reply for 3333333333333333",
        b"000000C1",  # Success ack
        b"000E"
        + b"3333333333333333"  # mac
        + b"44"  # rssi in
        + b"55"  # rssi out
        + b"4321",  # roundtrip
    ),
    b"\x05\x05\x03\x03000D44444444444444444B11\r\n": (
        "ping reply for 4444444444444444",
        b"000000C1",  # Success ack
        b"000E"  # msg_id
        + b"4444444444444444"  # mac
        + b"33"  # rssi in
        + b"44"  # rssi out
        + b"1234",  # roundtrip
    ),
    b"\x05\x05\x03\x03000D55555555555555555E46\r\n": (
        "ping timeout for 5555555555555555",
        b"000000E1",  # Timeout
        None,
    ),
    b"\x05\x05\x03\x03000D666666666666666661BF\r\n": (
        "ping timeout for 6666666666666666",
        b"000000E1",  # Timeout
        None,
    ),
    b"\x05\x05\x03\x03000D777777777777777774E8\r\n": (
        "ping timeout for 7777777777777777",
        b"000000E1",  # Timeout
        None,
    ),
    b"\x05\x05\x03\x03000D8888888888888888B4F5\r\n": (
        "ping timeout for 8888888888888888",
        b"000000E1",  # Timeout
        None,
    ),
    b"\x05\x05\x03\x0300231111111111111111D3F0\r\n": (
        "Node info for 1111111111111111",
        b"000000C1",  # Success ack
        b"0024"  # msg_id
        + b"1111111111111111"  # mac
        + b"22026A68"  # datetime
        + b"000442C0"  # log address  44000
        + b"01"  # relay
        + b"01"  # hz
        + b"000007014000"  # hw_ver
        + b"4E0844C2"  # fw_ver
        + b"02",  # node_type (Circle)
    ),
    b"\x05\x05\x03\x0300232222222222222222EC09\r\n": (
        "Node info for 2222222222222222",
        b"000000C1",  # Success ack
        b"0024"  # msg_id
        + b"2222222222222222"  # mac
        + b"22026A68"  # datetime
        + b"00044300"  # log address
        + b"01"  # relay
        + b"01"  # hz
        + b"000009001100"  # hw_ver
        + b"4EB28FD5"  # fw_ver
        + b"09",  # node_type (Stealth - Legrand)
    ),
    b"\x05\x05\x03\x03013822222222222222220000265D\r\n": (
        "Get Node relay init state for 2222222222222222",
        b"000000C1",  # Success ack
        b"0139"  # msg_id
        + b"2222222222222222"  # mac
        + b"00"  # is_get
        + b"01",  # relay config
    ),
    b"\x05\x05\x03\x03013822222222222222220100116D\r\n": (
        "Set Node relay init state off for 2222222222222222",
        b"000000C1",  # Success ack
        b"0139"  # msg_id
        + b"2222222222222222"  # mac
        + b"01"  # is_get
        + b"00",  # relay config
    ),
    b"\x05\x05\x03\x03013822222222222222220101014C\r\n": (
        "Set Node relay init state on for 2222222222222222",
        b"000000C1",  # Success ack
        b"0139"  # msg_id
        + b"2222222222222222"  # mac
        + b"01"  # is_get
        + b"01",  # relay config
    ),
    b"\x05\x05\x03\x0300233333333333333333F95E\r\n": (
        "Node info for 3333333333333333",
        b"000000C1",  # Success ack
        b"0024"  # msg_id
        + b"3333333333333333"  # mac
        + b"22026A68"  # datetime
        + b"00044340"  # log address
        + b"01"  # relay
        + b"01"  # hz
        + b"000007007300"  # hw_ver
        + b"4DCCDB7B"  # fw_ver
        + b"02",  # node_type (Circle)
    ),
    b"\x05\x05\x03\x030023444444444444444493FB\r\n": (
        "Node info for 4444444444444444",
        b"000000C1",  # Success ack
        b"0024"  # msg_id
        + b"4444444444444444"  # mac
        + b"22026A68"  # datetime
        + b"000443C0"  # log address
        + b"01"  # relay
        + b"01"  # hz
        + b"000007007300"  # hw_ver
        + b"4E0844C2"  # fw_ver
        + b"02",  # node_type (Circle)
    ),
    b"\x05\x05\x03\x03002600987654321012344988\r\n": (
        "Calibration for 0098765432101234",
        b"000000C1",  # Success ack
        b"0027"  # msg_id
        + b"0098765432101234"  # mac
        + b"3F80308E"  # gain_a
        + b"B66CF94F"  # gain_b
        + b"00000000"  # off_tot
        + b"BD14BFEC",  # off_noise
    ),
    b"\x05\x05\x03\x0300261111111111111111809A\r\n": (
        "Calibration for 1111111111111111",
        b"000000C1",  # Success ack
        b"0027"  # msg_id
        + b"1111111111111111"  # mac
        + b"3F7AE254"  # gain_a
        + b"B638FFB4"  # gain_b
        + b"00000000"  # off_tot
        + b"BC726F67",  # off_noise
    ),
    b"\x05\x05\x03\x0300262222222222222222BF63\r\n": (
        "Calibration for 2222222222222222",
        b"000000C1",  # Success ack
        b"0027"  # msg_id
        + b"2222222222222222"  # mac
        + b"3F806192"  # gain_a
        + b"B56D8019"  # gain_b
        + b"00000000"  # off_tot
        + b"BB4FA127",  # off_noise
    ),
    b"\x05\x05\x03\x0300263333333333333333AA34\r\n": (
        "Calibration for 3333333333333333",
        b"000000C1",  # Success ack
        b"0027"  # msg_id
        + b"3333333333333333"  # mac
        + b"3F7D8AC6"  # gain_a
        + b"B5F45E13"  # gain_b
        + b"00000000"  # off_tot
        + b"3CC3A53F",  # off_noise
    ),
    b"\x05\x05\x03\x0300264444444444444444C091\r\n": (
        "Calibration for 4444444444444444",
        b"000000C1",  # Success ack
        b"0027"  # msg_id
        + b"4444444444444444"  # mac
        + b"3F7D8AC6"  # gain_a
        + b"B5F45E13"  # gain_b
        + b"00000000"  # off_tot
        + b"3CC3A53F",  # off_noise
    ),
    b"\x05\x05\x03\x03013844444444444444440000265D\r\n": (
        "Get Node relay init state for 4444444444444444",
        b"000000C1",  # Success ack
        b"0139"  # msg_id
        + b"4444444444444444"  # mac
        + b"00"  # is_get
        + b"01",  # relay config
    ),
    b"\x05\x05\x03\x0300290098765432101234BC36\r\n": (
        "Realtime clock for 0098765432101234",
        b"000000C1",  # Success ack
        b"003A"  # msg_id
        + b"0098765432101234"  # mac
        + bytes(("%%0%dd" % 2) % utc_now.second, pw_constants.UTF8)
        + bytes(("%%0%dd" % 2) % utc_now.minute, pw_constants.UTF8)
        + bytes(("%%0%dd" % 2) % utc_now.hour, pw_constants.UTF8)
        + bytes(("%%0%dd" % 2) % utc_now.weekday(), pw_constants.UTF8)
        + bytes(("%%0%dd" % 2) % utc_now.day, pw_constants.UTF8)
        + bytes(("%%0%dd" % 2) % utc_now.month, pw_constants.UTF8)
        + bytes(("%%0%dd" % 2) % (utc_now.year - 2000), pw_constants.UTF8),
    ),
    b"\x05\x05\x03\x03003E11111111111111111B8A\r\n": (
        "clock for 0011111111111111",
        b"000000C1",  # Success ack
        b"003F"  # msg_id
        + b"1111111111111111"  # mac
        + bytes(("%%0%dX" % 2) % utc_now.hour, pw_constants.UTF8)
        + bytes(("%%0%dX" % 2) % utc_now.minute, pw_constants.UTF8)
        + bytes(("%%0%dX" % 2) % utc_now.second, pw_constants.UTF8)
        + bytes(("%%0%dX" % 2) % utc_now.weekday(), pw_constants.UTF8)
        + b"00"  # unknown
        + b"0000",  # unknown2
    ),
    b"\x05\x05\x03\x03003E22222222222222222473\r\n": (
        "clock for 2222222222222222",
        b"000000C1",  # Success ack
        b"003F"  # msg_id
        + b"2222222222222222"  # mac
        + bytes(("%%0%dX" % 2) % utc_now.hour, pw_constants.UTF8)
        + bytes(("%%0%dX" % 2) % utc_now.minute, pw_constants.UTF8)
        + bytes(("%%0%dX" % 2) % utc_now.second, pw_constants.UTF8)
        + bytes(("%%0%dX" % 2) % utc_now.weekday(), pw_constants.UTF8)
        + b"00"  # unknown
        + b"0000",  # unknown2
    ),
    b"\x05\x05\x03\x03003E33333333333333333124\r\n": (
        "clock for 3333333333333333",
        b"000000C1",  # Success ack
        b"003F"  # msg_id
        + b"3333333333333333"  # mac
        + bytes(("%%0%dX" % 2) % utc_now.hour, pw_constants.UTF8)
        + bytes(("%%0%dX" % 2) % utc_now.minute, pw_constants.UTF8)
        + bytes(("%%0%dX" % 2) % utc_now.second, pw_constants.UTF8)
        + bytes(("%%0%dX" % 2) % utc_now.weekday(), pw_constants.UTF8)
        + b"00"  # unknown
        + b"0000",  # unknown2
    ),
    b"\x05\x05\x03\x03003E44444444444444445B81\r\n": (
        "clock for 4444444444444444",
        b"000000C1",  # Success ack
        b"003F"  # msg_id
        + b"4444444444444444"  # mac
        + bytes(("%%0%dX" % 2) % utc_now.hour, pw_constants.UTF8)
        + bytes(("%%0%dX" % 2) % utc_now.minute, pw_constants.UTF8)
        + bytes(("%%0%dX" % 2) % utc_now.second, pw_constants.UTF8)
        + bytes(("%%0%dX" % 2) % utc_now.weekday(), pw_constants.UTF8)
        + b"00"  # unknown
        + b"0000",  # unknown2
    ),
    b"\x05\x05\x03\x03001700987654321012340104F9\r\n": (
        "Relay on for 0098765432101234",
        b"000000C1",  # Success ack
        b"0000"  # msg_id
        + b"00D8"  # ack id for RelaySwitchedOn
        + b"0098765432101234",  # mac
    ),
    b"\x05\x05\x03\x03001700987654321012340014D8\r\n": (
        "Relay off for 0098765432101234",
        b"000000C1",  # Success ack
        b"0000"  # msg_id
        + b"00DE"  # ack id for RelaySwitchedOff
        + b"0098765432101234",  # mac
    ),
    b"\x05\x05\x03\x030023555555555555555586AC\r\n": (
        "Node info for 5555555555555555",
        b"000000C1",  # Success ack
        b"0024"  # msg_id
        + b"5555555555555555"  # mac
        + b"22026A68"  # datetime
        + b"00000000"  # log address
        + b"00"  # relay
        + b"01"  # hz
        + b"000008000700"  # hw_ver
        + b"4E084590"  # fw_ver
        + b"06",  # node_type (Scan)
    ),
    b"\x05\x05\x03\x03002388888888888888886C1F\r\n": (
        "Node info for 8888888888888888",
        b"000000C1",  # Success ack
        b"0024"  # msg_id
        + b"8888888888888888"  # mac
        + b"22026A68"  # datetime
        + b"00000000"  # log address
        + b"00"  # relay
        + b"01"  # hz
        + b"000007005100"  # hw_ver
        + b"4E08478A"  # fw_ver
        + b"03",  # node_type (Switch)
    ),
    b"\x05\x05\x03\x03001200987654321012340A72\r\n": (
        "Power usage for 0098765432101234",
        b"000000C1",  # Success ack
        b"0013"  # msg_id
        + b"0098765432101234"  # mac
        + b"000A"  # pulses 1s
        + b"FF9A"  # pulses 8s
        + b"00001234"
        + b"00000000"
        + b"0004",
    ),
    b"\x05\x05\x03\x0300480098765432101234000442808C54\r\n": (
        "Energy log for 0098765432101234 @ log ADDRESS 20",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"0098765432101234"  # mac
        + LOG_TIMESTAMPS[2]  # datetime
        + b"00000000"
        + LOG_TIMESTAMPS[1]  # datetime
        + b"00111111"
        + LOG_TIMESTAMPS[0]  # datetime
        + b"00111111"
        + b"FFFFFFFF"  # datetime
        + b"00000000"
        + b"00044280",  # log address
    ),
    b"\x05\x05\x03\x030048009876543210123400044260AF5B\r\n": (
        "Energy log for 0098765432101234 @ log ADDRESS 19",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"0098765432101234"  # mac
        + LOG_TIMESTAMPS[6]  # datetime
        + b"00000000"
        + LOG_TIMESTAMPS[5]  # datetime
        + b"00000000"
        + LOG_TIMESTAMPS[4]  # datetime
        + b"00000000"
        + LOG_TIMESTAMPS[3]  # datetime
        + b"00000000"
        + b"00044260",
    ),
    b"\x05\x05\x03\x030048009876543210123400044240C939\r\n": (
        "Energy log for 0098765432101234 @ log ADDRESS 18",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"0098765432101234"  # mac
        + LOG_TIMESTAMPS[10]  # datetime
        + b"00000000"
        + LOG_TIMESTAMPS[9]  # datetime
        + b"00000000"
        + LOG_TIMESTAMPS[8]  # datetime
        + b"00000000"
        + LOG_TIMESTAMPS[7]  # datetime
        + b"00000000"
        + b"00044240",
    ),
    b"\x05\x05\x03\x030048009876543210123400044220639F\r\n": (
        "Energy log for 0098765432101234 @ log ADDRESS 17",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"0098765432101234"  # mac
        + LOG_TIMESTAMPS[14]  # datetime
        + b"00000000"
        + LOG_TIMESTAMPS[13]  # datetime
        + b"00000000"
        + LOG_TIMESTAMPS[12]  # datetime
        + b"00000000"
        + LOG_TIMESTAMPS[11]  # datetime
        + b"00000000"
        + b"00044220",
    ),
    b"\x05\x05\x03\x03004800987654321012340004420005FD\r\n": (
        "Energy log for 0098765432101234 @ log ADDRESS 16",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"0098765432101234"  # mac
        + LOG_TIMESTAMPS[18]  # datetime
        + b"00000000"
        + LOG_TIMESTAMPS[17]  # datetime
        + b"00000000"
        + LOG_TIMESTAMPS[16]  # datetime
        + b"00000000"
        + LOG_TIMESTAMPS[15]  # datetime
        + b"00000000"
        + b"00044200",
    ),
    b"\x05\x05\x03\x0300480098765432101234000441E0AB01\r\n": (
        "Energy log for 0098765432101234 @ log ADDRESS 15",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"0098765432101234"  # mac
        + LOG_TIMESTAMPS[22]  # datetime
        + b"00000000"
        + LOG_TIMESTAMPS[21]  # datetime
        + b"00000000"
        + LOG_TIMESTAMPS[20]  # datetime
        + b"00000000"
        + LOG_TIMESTAMPS[19]  # datetime
        + b"00000000"
        + b"000441E0",
    ),
    b"\x05\x05\x03\x0300480098765432101234000441C001A7\r\n": (
        "Energy log for 0098765432101234 @ log ADDRESS 14",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"0098765432101234"  # mac
        + LOG_TIMESTAMPS[26]  # datetime
        + b"00001234"
        + LOG_TIMESTAMPS[25]  # datetime
        + b"00000080"
        + LOG_TIMESTAMPS[24]  # datetime
        + b"00000050"
        + LOG_TIMESTAMPS[23]  # datetime
        + b"00000000"
        + b"000441C0",
    ),
    b"\x05\x05\x03\x0300480098765432101234000441A067C5\r\n": (
        "Energy log for 0098765432101234 @ log ADDRESS 13",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"0098765432101234"  # mac
        + LOG_TIMESTAMPS[30]  # datetime
        + b"00000512"
        + LOG_TIMESTAMPS[29]  # datetime
        + b"00001224"
        + LOG_TIMESTAMPS[28]  # datetime
        + b"00000888"
        + LOG_TIMESTAMPS[27]  # datetime
        + b"00009999"
        + b"000441A0",
    ),
    b"\x05\x05\x03\x030048009876543210123400044180D504\r\n": (
        "Energy log for 0098765432101234 @ log ADDRESS 12",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"0098765432101234"  # mac
        + LOG_TIMESTAMPS[34]  # datetime
        + b"00000212"
        + LOG_TIMESTAMPS[33]  # datetime
        + b"00001664"
        + LOG_TIMESTAMPS[32]  # datetime
        + b"00000338"
        + LOG_TIMESTAMPS[31]  # datetime
        + b"00001299"
        + b"00044180",
    ),
    b"\x05\x05\x03\x030048009876543210123400044160F60B\r\n": (
        "Energy log for 0098765432101234 @ log ADDRESS 11",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"0098765432101234"  # mac
        + LOG_TIMESTAMPS[38]  # datetime
        + b"00001512"
        + LOG_TIMESTAMPS[37]  # datetime
        + b"00004324"
        + LOG_TIMESTAMPS[36]  # datetime
        + b"00000338"
        + LOG_TIMESTAMPS[35]  # datetime
        + b"00006666"
        + b"00044160",
    ),
    b"\x05\x05\x03\x0300480098765432101234000441409069\r\n": (
        "Energy log for 0098765432101234 @ log ADDRESS 10",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"0098765432101234"  # mac
        + LOG_TIMESTAMPS[42]  # datetime
        + b"00001542"
        + LOG_TIMESTAMPS[41]  # datetime
        + b"00004366"
        + LOG_TIMESTAMPS[40]  # datetime
        + b"00000638"
        + LOG_TIMESTAMPS[39]  # datetime
        + b"00005231"
        + b"00044140",
    ),
    b"\x05\x05\x03\x0300480098765432101234000441203ACF\r\n": (
        "Energy log for 0098765432101234 @ log ADDRESS 9",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"0098765432101234"  # mac
        + LOG_TIMESTAMPS[46]  # datetime
        + b"00001542"
        + LOG_TIMESTAMPS[45]  # datetime
        + b"00004366"
        + LOG_TIMESTAMPS[44]  # datetime
        + b"00000638"
        + LOG_TIMESTAMPS[43]  # datetime
        + b"00005231"
        + b"00044120",
    ),
    b"\x05\x05\x03\x0300480098765432101234000440E09C31\r\n": (
        "Energy log for 0098765432101234 @ log ADDRESS 8",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"0098765432101234"  # mac
        + LOG_TIMESTAMPS[50]  # datetime
        + b"00001542"
        + LOG_TIMESTAMPS[49]  # datetime
        + b"00004366"
        + LOG_TIMESTAMPS[48]  # datetime
        + b"00000638"
        + LOG_TIMESTAMPS[47]  # datetime
        + b"00005231"
        + b"000440E0",
    ),
    b"\x05\x05\x03\x0300480098765432101234000440C03697\r\n": (
        "Energy log for 0098765432101234 @ log ADDRESS 7",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"0098765432101234"  # mac
        + LOG_TIMESTAMPS[54]  # datetime
        + b"00001542"
        + LOG_TIMESTAMPS[53]  # datetime
        + b"00004366"
        + LOG_TIMESTAMPS[52]  # datetime
        + b"00000638"
        + LOG_TIMESTAMPS[51]  # datetime
        + b"00005231"
        + b"000440C0",
    ),
    b"\x05\x05\x03\x0300480098765432101234000440A050F5\r\n": (
        "Energy log for 0098765432101234 @ log ADDRESS 6",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"0098765432101234"  # mac
        + LOG_TIMESTAMPS[58]  # datetime
        + b"00001542"
        + LOG_TIMESTAMPS[57]  # datetime
        + b"00004366"
        + LOG_TIMESTAMPS[56]  # datetime
        + b"00000638"
        + LOG_TIMESTAMPS[55]  # datetime
        + b"00005231"
        + b"000440A0",
    ),
    b"\x05\x05\x03\x030048009876543210123400044080E234\r\n": (
        "Energy log for 0098765432101234 @ log ADDRESS 5",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"0098765432101234"  # mac
        + LOG_TIMESTAMPS[62]  # datetime
        + b"00001542"
        + LOG_TIMESTAMPS[61]  # datetime
        + b"00004366"
        + LOG_TIMESTAMPS[60]  # datetime
        + b"00000638"
        + LOG_TIMESTAMPS[59]  # datetime
        + b"00005231"
        + b"00044080",
    ),
    b"\x05\x05\x03\x030048009876543210123400044060C13B\r\n": (
        "Energy log for 0098765432101234 @ log ADDRESS 4",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"0098765432101234"  # mac
        + LOG_TIMESTAMPS[66]  # datetime
        + b"00001542"
        + LOG_TIMESTAMPS[65]  # datetime
        + b"00004366"
        + LOG_TIMESTAMPS[64]  # datetime
        + b"00000638"
        + LOG_TIMESTAMPS[63]  # datetime
        + b"00005231"
        + b"00044060",
    ),
    b"\x05\x05\x03\x030048009876543210123400044040A759\r\n": (
        "Energy log for 0098765432101234 @ log ADDRESS 3",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"0098765432101234"  # mac
        + LOG_TIMESTAMPS[70]  # datetime
        + b"00001542"
        + LOG_TIMESTAMPS[69]  # datetime
        + b"00004366"
        + LOG_TIMESTAMPS[68]  # datetime
        + b"00000638"
        + LOG_TIMESTAMPS[67]  # datetime
        + b"00005231"
        + b"00044040",
    ),
    b"\x05\x05\x03\x0300480098765432101234000440200DFF\r\n": (
        "Energy log for 0098765432101234 @ log ADDRESS 2",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"0098765432101234"  # mac
        + LOG_TIMESTAMPS[74]  # datetime
        + b"00001542"
        + LOG_TIMESTAMPS[73]  # datetime
        + b"00004366"
        + LOG_TIMESTAMPS[72]  # datetime
        + b"00000638"
        + LOG_TIMESTAMPS[71]  # datetime
        + b"00005231"
        + b"00044020",
    ),
    b"\x05\x05\x03\x0300480098765432101234000441005CAD\r\n": (
        "Energy log for 0098765432101234 @ log ADDRESS 1",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"0098765432101234"  # mac
        + LOG_TIMESTAMPS[78]  # datetime
        + b"00001542"
        + LOG_TIMESTAMPS[77]  # datetime
        + b"00004366"
        + LOG_TIMESTAMPS[76]  # datetime
        + b"00000638"
        + LOG_TIMESTAMPS[75]  # datetime
        + b"00005231"
        + b"00044100",
    ),
    b"\x05\x05\x03\x0300121111111111111111C360\r\n": (
        "Power usage for 1111111111111111",
        b"000000C1",  # Success ack
        b"0013"  # msg_id
        + b"1111111111111111"  # mac
        + b"005A"  # pulses 1s
        + b"0098"  # pulses 8s
        + b"00008787"
        + b"00008123"
        + b"0004",
    ),
    b"\x05\x05\x03\x0300481111111111111111000442C05D37\r\n": (
        "Energy log for 1111111111111111 @ log ADDRESS 20",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"1111111111111111"  # mac
        + LOG_TIMESTAMPS[0]  # datetime
        + b"00222222"
        + LOG_TIMESTAMPS[0]  # datetime
        + b"00111111"
        + b"FFFFFFFF"  # datetime
        + b"00000000"
        + b"FFFFFFFF"  # datetime
        + b"00000000"
        + b"000442C0",  # log address
    ),
    b"\x05\x05\x03\x0300481111111111111111000442A03B55\r\n": (
        "Energy log for 1111111111111111 @ log ADDRESS 19",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"1111111111111111"  # mac
        + LOG_TIMESTAMPS[2]  # datetime
        + b"00002000"
        + LOG_TIMESTAMPS[2]  # datetime
        + b"00001000"
        + LOG_TIMESTAMPS[1]  # datetime
        + b"00000500"
        + LOG_TIMESTAMPS[1]  # datetime
        + b"00000250"
        + b"000442A0",
    ),
    b"\x05\x05\x03\x0300481111111111111111000442808994\r\n": (
        "Energy log for 1111111111111111 @ log ADDRESS 18",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"1111111111111111"  # mac
        + LOG_TIMESTAMPS[4]  # datetime
        + b"00000000"
        + LOG_TIMESTAMPS[4]  # datetime
        + b"00000000"
        + LOG_TIMESTAMPS[3]  # datetime
        + b"00008000"
        + LOG_TIMESTAMPS[3]  # datetime
        + b"00004000"
        + b"00044280",
    ),
    b"\x05\x05\x03\x030048111111111111111100044260AA9B\r\n": (
        "Energy log for 1111111111111111 @ log ADDRESS 17",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"1111111111111111"  # mac
        + LOG_TIMESTAMPS[6]  # datetime
        + b"00000800"
        + LOG_TIMESTAMPS[6]  # datetime
        + b"00000400"
        + LOG_TIMESTAMPS[5]  # datetime
        + b"00040000"
        + LOG_TIMESTAMPS[5]  # datetime
        + b"00020000"
        + b"00044260",
    ),
    b"\x05\x05\x03\x030048111111111111111100044240CCF9\r\n": (
        "Energy log for 1111111111111111 @ log ADDRESS 16",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"1111111111111111"  # mac
        + LOG_TIMESTAMPS[8]  # datetime
        + b"00000000"
        + LOG_TIMESTAMPS[8]  # datetime
        + b"00000000"
        + LOG_TIMESTAMPS[7]  # datetime
        + b"00000000"
        + LOG_TIMESTAMPS[7]  # datetime
        + b"00000000"
        + b"00044240",
    ),
    b"\x05\x05\x03\x030048111111111111111100044220665F\r\n": (
        "Energy log for 1111111111111111 @ log ADDRESS 14",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"1111111111111111"  # mac
        + LOG_TIMESTAMPS[10]  # datetime
        + b"00004444"
        + LOG_TIMESTAMPS[10]  # datetime
        + b"00002222"
        + LOG_TIMESTAMPS[9]  # datetime
        + b"00011111"
        + LOG_TIMESTAMPS[9]  # datetime
        + b"00022222"
        + b"00044220",
    ),
    b"\x05\x05\x03\x030048111111111111111100044200003D\r\n": (
        "Energy log for 1111111111111111 @ log ADDRESS 13",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"1111111111111111"  # mac
        + LOG_TIMESTAMPS[12]  # datetime
        + b"00000660"
        + LOG_TIMESTAMPS[12]  # datetime
        + b"00000330"
        + LOG_TIMESTAMPS[11]  # datetime
        + b"00006400"
        + LOG_TIMESTAMPS[11]  # datetime
        + b"00003200"
        + b"00044200",  # log address
    ),
    b"\x05\x05\x03\x0300481111111111111111000441E0AEC1\r\n": (
        "Energy log for 1111111111111111 @ log ADDRESS 12",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"1111111111111111"  # mac
        + LOG_TIMESTAMPS[12]  # datetime
        + b"00000660"
        + LOG_TIMESTAMPS[12]  # datetime
        + b"00000330"
        + LOG_TIMESTAMPS[11]  # datetime
        + b"00006400"
        + LOG_TIMESTAMPS[11]  # datetime
        + b"00003200"
        + b"000441E0",  # log address
    ),
    b"\x05\x05\x03\x0300481111111111111111000441C00467\r\n": (
        "Energy log for 1111111111111111 @ log ADDRESS 11",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"1111111111111111"  # mac
        + LOG_TIMESTAMPS[14]  # datetime
        + b"00000512"
        + LOG_TIMESTAMPS[14]  # datetime
        + b"00000254"
        + LOG_TIMESTAMPS[13]  # datetime
        + b"00000888"
        + LOG_TIMESTAMPS[13]  # datetime
        + b"00000444"
        + b"000441C0",
    ),
    b"\x05\x05\x03\x0300481111111111111111000441A06205\r\n": (
        "Energy log for 1111111111111111 @ log ADDRESS 10",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"1111111111111111"  # mac
        + LOG_TIMESTAMPS[16]  # datetime
        + b"00000512"
        + LOG_TIMESTAMPS[16]  # datetime
        + b"00001224"
        + LOG_TIMESTAMPS[15]  # datetime
        + b"00000888"
        + LOG_TIMESTAMPS[15]  # datetime
        + b"00009999"
        + b"000441A0",
    ),
    b"\x05\x05\x03\x030048111111111111111100044180D0C4\r\n": (
        "Energy log for 1111111111111111 @ log ADDRESS 9",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"1111111111111111"  # mac
        + LOG_TIMESTAMPS[18]  # datetime
        + b"00000512"
        + LOG_TIMESTAMPS[18]  # datetime
        + b"00001224"
        + LOG_TIMESTAMPS[17]  # datetime
        + b"00000888"
        + LOG_TIMESTAMPS[17]  # datetime
        + b"00000444"
        + b"00044180",
    ),
    b"\x05\x05\x03\x030048111111111111111100044160F3CB\r\n": (
        "Energy log for 1111111111111111 @ log ADDRESS 8",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"1111111111111111"  # mac
        + LOG_TIMESTAMPS[20]  # datetime
        + b"00006666"
        + LOG_TIMESTAMPS[20]  # datetime
        + b"00003333"
        + LOG_TIMESTAMPS[19]  # datetime
        + b"00004848"
        + LOG_TIMESTAMPS[19]  # datetime
        + b"00002424"
        + b"00044160",
    ),
    b"\x05\x05\x03\x03004811111111111111110004414095A9\r\n": (
        "Energy log for 1111111111111111 @ log ADDRESS 7",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"1111111111111111"  # mac
        + LOG_TIMESTAMPS[22]  # datetime
        + b"00000512"
        + LOG_TIMESTAMPS[22]  # datetime
        + b"00001224"
        + LOG_TIMESTAMPS[21]  # datetime
        + b"00000888"
        + LOG_TIMESTAMPS[21]  # datetime
        + b"00009999"
        + b"00044140",
    ),
    b"\x05\x05\x03\x0300481111111111111111000441203F0F\r\n": (
        "Energy log for 1111111111111111 @ log ADDRESS 6",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"1111111111111111"  # mac
        + LOG_TIMESTAMPS[25]  # datetime
        + b"00001024"
        + LOG_TIMESTAMPS[25]  # datetime
        + b"00000512"
        + LOG_TIMESTAMPS[24]  # datetime
        + b"00004646"
        + LOG_TIMESTAMPS[24]  # datetime
        + b"00002323"
        + b"00044120",
    ),
    b"\x05\x05\x03\x030048111111111111111100044100596D\r\n": (
        "Energy log for 1111111111111111 @ log ADDRESS 5",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"1111111111111111"  # mac
        + LOG_TIMESTAMPS[27]  # datetime
        + b"00001024"
        + LOG_TIMESTAMPS[27]  # datetime
        + b"00000512"
        + LOG_TIMESTAMPS[26]  # datetime
        + b"00004646"
        + LOG_TIMESTAMPS[26]  # datetime
        + b"00002323"
        + b"00044100",
    ),
    b"\x05\x05\x03\x0300481111111111111111000440E099F1\r\n": (
        "Energy log for 1111111111111111 @ log ADDRESS 4",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"1111111111111111"  # mac
        + LOG_TIMESTAMPS[29]  # datetime
        + b"00001024"
        + LOG_TIMESTAMPS[29]  # datetime
        + b"00000512"
        + LOG_TIMESTAMPS[28]  # datetime
        + b"00004646"
        + LOG_TIMESTAMPS[28]  # datetime
        + b"00002323"
        + b"000440E0",
    ),
    b"\x05\x05\x03\x0300481111111111111111000440C03357\r\n": (
        "Energy log for 1111111111111111 @ log ADDRESS 3",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"1111111111111111"  # mac
        + LOG_TIMESTAMPS[31]  # datetime
        + b"00001024"
        + LOG_TIMESTAMPS[31]  # datetime
        + b"00000512"
        + LOG_TIMESTAMPS[30]  # datetime
        + b"00004646"
        + LOG_TIMESTAMPS[30]  # datetime
        + b"00002323"
        + b"000440C0",
    ),
    b"\x05\x05\x03\x0300481111111111111111000440A05535\r\n": (
        "Energy log for 1111111111111111 @ log ADDRESS 2",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"1111111111111111"  # mac
        + LOG_TIMESTAMPS[33]  # datetime
        + b"00001024"
        + LOG_TIMESTAMPS[33]  # datetime
        + b"00000512"
        + LOG_TIMESTAMPS[32]  # datetime
        + b"00004646"
        + LOG_TIMESTAMPS[32]  # datetime
        + b"00002323"
        + b"000440A0",
    ),
    b"\x05\x05\x03\x030048111111111111111100044080E7F4\r\n": (
        "Energy log for 1111111111111111 @ log ADDRESS 1",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"1111111111111111"  # mac
        + LOG_TIMESTAMPS[35]  # datetime
        + b"00001024"
        + LOG_TIMESTAMPS[35]  # datetime
        + b"00000512"
        + LOG_TIMESTAMPS[34]  # datetime
        + b"00004646"
        + LOG_TIMESTAMPS[34]  # datetime
        + b"00002323"
        + b"00044080",
    ),
    b"\x05\x05\x03\x030048111111111111111100044060C4FB\r\n": (
        "Energy log for 1111111111111111 @ log ADDRESS 1",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"1111111111111111"  # mac
        + LOG_TIMESTAMPS[38]  # datetime
        + b"00001024"
        + LOG_TIMESTAMPS[38]  # datetime
        + b"00000512"
        + LOG_TIMESTAMPS[36]  # datetime
        + b"00004646"
        + LOG_TIMESTAMPS[36]  # datetime
        + b"00002323"
        + b"00044060",
    ),
    b"\x05\x05\x03\x030048111111111111111100044040A299\r\n": (
        "Energy log for 1111111111111111 @ log ADDRESS 1",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"1111111111111111"  # mac
        + LOG_TIMESTAMPS[41]  # datetime
        + b"00001024"
        + LOG_TIMESTAMPS[41]  # datetime
        + b"00000512"
        + LOG_TIMESTAMPS[40]  # datetime
        + b"00004646"
        + LOG_TIMESTAMPS[40]  # datetime
        + b"00002323"
        + b"00044040",
    ),
    b"\x05\x05\x03\x030048111111111111111100044020083F\r\n": (
        "Energy log for 1111111111111111 @ log ADDRESS 6",
        b"000000C1",  # Success ack
        b"0049"  # msg_id
        + b"1111111111111111"  # mac
        + LOG_TIMESTAMPS[43]  # datetime
        + b"00000512"
        + LOG_TIMESTAMPS[43]  # datetime
        + b"00000254"
        + LOG_TIMESTAMPS[42]  # datetime
        + b"00000888"
        + LOG_TIMESTAMPS[42]  # datetime
        + b"00000444"
        + b"00044020",
    ),
}


PARTLY_RESPONSE_MESSAGES = {
    b"\x05\x05\x03\x0300161111111111111111": (
        "Clock set 1111111111111111",
        b"000000C1",  # Success ack
        b"0000" + b"00D7" + b"1111111111111111",  # msg_id, ClockAccepted, mac
    ),
    b"\x05\x05\x03\x0300162222222222222222": (
        "Clock set 2222222222222222",
        b"000000C1",  # Success ack
        b"0000" + b"00D7" + b"2222222222222222",  # msg_id, ClockAccepted, mac
    ),
    b"\x05\x05\x03\x0300163333333333333333": (
        "Clock set 3333333333333333",
        b"000000C1",  # Success ack
        b"0000" + b"00D7" + b"3333333333333333",  # msg_id, ClockAccepted, mac
    ),
    b"\x05\x05\x03\x0300164444444444444444": (
        "Clock set 4444444444444444",
        b"000000C1",  # Success ack
        b"0000" + b"00D7" + b"4444444444444444",  # msg_id, ClockAccepted, mac
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
