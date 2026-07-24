import struct


def build_packet(values: tuple) -> bytes:
    """Build one valid framed packet (header + len + payload + checksum)
    from float values matching mobbo.protocol's wire format."""
    payload = struct.pack("<" + "f" * len(values), *values)
    packet_len = len(payload) + 1
    checksum = (0xFE + packet_len + sum(payload)) & 0xFF
    return bytes([0xFF, 0xFF, packet_len]) + payload + bytes([checksum])
