class FakeSerial:
    """Minimal stand-in for serial.Serial, driven by a pre-loaded byte buffer."""

    def __init__(self, data: bytes = b""):
        self._buffer = bytearray(data)
        self.written = bytearray()
        self.closed = False

    @property
    def in_waiting(self) -> int:
        return len(self._buffer)

    def read(self, size: int) -> bytes:
        n = min(size, len(self._buffer))
        chunk = bytes(self._buffer[:n])
        del self._buffer[:n]
        return chunk

    def feed(self, data: bytes) -> None:
        self._buffer.extend(data)

    def write(self, data: bytes) -> None:
        self.written.extend(data)

    def flush(self) -> None:
        pass

    def reset_input_buffer(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class RaisingFakeSerial(FakeSerial):
    """Raises on the Nth read() call, to simulate a mid-session disconnect."""

    def __init__(self, data: bytes = b"", fail_after_reads: int = 1):
        super().__init__(data)
        self._reads = 0
        self._fail_after = fail_after_reads

    def read(self, size: int) -> bytes:
        self._reads += 1
        if self._reads > self._fail_after:
            raise OSError("device disconnected")
        return super().read(size)
