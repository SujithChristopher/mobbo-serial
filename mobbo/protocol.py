import struct
from dataclasses import dataclass

from . import constants


@dataclass
class Sample:
    time_ms: float
    forces: list[float]
    pulse: int


def pop_binary_payload(buffer: bytearray) -> bytes | None:
    while len(buffer) >= 4:
        if buffer[0] != constants.HEADER_BYTES[0] or buffer[1] != constants.HEADER_BYTES[1]:
            del buffer[0]
            continue

        packet_len = buffer[2]
        payload_len = packet_len - 1
        total_len = 3 + packet_len
        if payload_len <= 0:
            del buffer[0]
            continue
        if len(buffer) < total_len:
            return None

        payload = bytes(buffer[3:3 + payload_len])
        checksum = buffer[3 + payload_len]
        calc = (0xFE + packet_len + sum(payload)) & 0xFF
        if checksum != calc:
            del buffer[0]
            continue

        del buffer[:total_len]
        return payload
    return None


def parse_payload(payload: bytes) -> Sample | None:
    if len(payload) != constants.PAYLOAD_SIZE:
        return None
    try:
        values = struct.unpack("<10f", payload)
    except struct.error:
        return None
    return Sample(values[0], list(values[1:9]), int(round(values[9])))
