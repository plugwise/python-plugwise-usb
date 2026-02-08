"""Plus-device pairing test data."""

RESPONSE_MESSAGES = {
    b"\x05\x05\x03\x030001CAAB\r\n": (
        "Stick network info request",
        b"000000C1",  # Success ack
        b"0002"  # response msg_id
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
