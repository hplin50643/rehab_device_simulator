import pytest

from rehab_simulator.protocol import Frame, FrameStream, decode, is_reply, parse_uid

GOLDENS = [
    (0x21, "01234567", "7A2104012345670A7F"),
    (0x21, "C8", "7A2101C8157F"),
    (0x21, "C9", "7A2101C9147F"),
    (0x23, "003C", "7A2302003C9E7F"),
    (0x23, "010A", "7A2302010ACF7F"),
    (0x23, "0250", "7A23020250887F"),
    (0x24, "00", "7A240100DA7F"),
    (0x20, "00", "7A200100DE7F"),
    (0x20, "00C8", "7A200200C8157F"),
    (0x20, "02C9", "7A200202C9127F"),
    (0x22, "01", "7A220101DB7F"),
    (0x22, "01C8", "7A220201C8127F"),
    (0x25, "0A", "7A25010ACF7F"),
    (0x25, "000A", "7A2502000ACE7F"),
    (0x25, "C8", "7A2501C8117F"),
]


@pytest.mark.parametrize("command,payload,wire", GOLDENS)
def test_golden_and_round_trip(command, payload, wire):
    frame = Frame(command, bytes.fromhex(payload))
    assert frame.encode().hex().upper() == wire
    assert decode(bytes.fromhex(wire)) == frame


@pytest.mark.parametrize("command,payload,wire", GOLDENS)
def test_every_split(command, payload, wire):
    packet = bytes.fromhex(wire)
    for split in range(len(packet) + 1):
        stream = FrameStream()
        first, errors = stream.feed(packet[:split], 0)
        second, more_errors = stream.feed(packet[split:], 0.1)
        assert not errors + more_errors
        assert first + second == [Frame(command, bytes.fromhex(payload))]


def test_coalesced_payload_delimiters_and_max_length():
    frames = [Frame(0x21, b"\x00\x7a\x7f\x00"), Frame(0x21, bytes(range(255)))]
    stream = FrameStream()
    result, errors = stream.feed(b"".join(frame.encode() for frame in frames), 0)
    assert result == frames and not errors


@pytest.mark.parametrize(
    "bad", [b"noise", b"\x7a\x23\x02\x00\x2c\x8e\x7f", b"\x7a\x20\x01\x00\xde\x00"]
)
def test_corrupt_then_good(bad):
    good = Frame(0x20, b"\x02")
    frames, errors = FrameStream().feed(bad + good.encode(), 0)
    assert errors and frames == [good]


def test_incomplete_deadline_recovers_and_bounds_memory():
    good = Frame(0x20, b"\x00")
    stream = FrameStream()
    assert stream.feed(b"\x7a\x99\xff" + good.encode(), 0)[0] == []
    assert stream.feed(b"", 1.99)[0] == []
    frames, errors = stream.feed(b"", 2)
    assert frames == [good] and errors
    stream.feed(b"x" * 100000, 4)
    assert len(stream.buffer) <= 16384


@pytest.mark.parametrize("value", ["", "0", "GG", "0x01", "01-02", "AA" * 256, 123, None])
def test_uid_invalid(value):
    with pytest.raises(ValueError):
        parse_uid(value)


@pytest.mark.parametrize(
    "value,expected",
    [("00 01 AB CD", b"\x00\x01\xab\xcd"), ("00000000", b"\0" * 4), ("00" * 7, b"\0" * 7)],
)
def test_uid_preserves_bytes(value, expected):
    assert parse_uid(value) == expected


def test_level_200_is_not_misclassified_as_ack():
    assert not is_reply(Frame(0x25, b"\xc8"))
    assert is_reply(Frame(0x21, b"\xc8"))


@pytest.mark.parametrize(
    "packet", [b"", b"\x7a\x20", b"\x7a\x20\x01\x00\xdf\x7f", b"\x7a\x20\x02\x00\xde\x7f"]
)
def test_decode_rejects_invalid(packet):
    with pytest.raises(ValueError):
        decode(packet)
