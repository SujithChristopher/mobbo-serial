from mobbo import protocol
from tests.helpers import build_packet


SAMPLE_VALUES = (123.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0)


def test_pop_binary_payload_extracts_valid_packet():
    packet = build_packet(SAMPLE_VALUES)
    buffer = bytearray(packet)
    payload = protocol.pop_binary_payload(buffer)
    assert payload is not None
    assert len(payload) == 40
    assert len(buffer) == 0


def test_pop_binary_payload_returns_none_on_incomplete_buffer():
    packet = build_packet(SAMPLE_VALUES)
    buffer = bytearray(packet[:-5])  # truncated, missing tail bytes
    assert protocol.pop_binary_payload(buffer) is None


def test_pop_binary_payload_skips_garbage_before_header():
    packet = build_packet(SAMPLE_VALUES)
    buffer = bytearray(b"\x01\x02\x03") + bytearray(packet)
    payload = protocol.pop_binary_payload(buffer)
    assert payload is not None
    assert len(buffer) == 0


def test_pop_binary_payload_rejects_bad_checksum_and_resyncs():
    packet = bytearray(build_packet(SAMPLE_VALUES))
    packet[-1] ^= 0xFF  # corrupt checksum
    good_packet = build_packet(SAMPLE_VALUES)
    buffer = bytearray(packet) + bytearray(good_packet)
    payload = protocol.pop_binary_payload(buffer)
    assert payload is not None
    values = __import__("struct").unpack("<10f", payload)
    assert values[0] == 123.0


def test_parse_payload_unpacks_floats():
    packet = build_packet(SAMPLE_VALUES)
    buffer = bytearray(packet)
    payload = protocol.pop_binary_payload(buffer)
    sample = protocol.parse_payload(payload)
    assert sample is not None
    assert sample.time_ms == 123.0
    assert sample.forces == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
    assert sample.pulse == 9


def test_parse_payload_wrong_length_returns_none():
    assert protocol.parse_payload(b"\x00" * 10) is None
