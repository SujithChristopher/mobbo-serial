import threading
import time

from mobbo import board as board_module
from tests.fake_serial import FakeSerial
from tests.helpers import build_packet


def _wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_tare_zeroes_out_steady_offset(monkeypatch):
    # forces = [2,2,2,2, 0,0,0,0]
    packet = build_packet((0.0, 2.0, 2.0, 2.0, 2.0, 0.0, 0.0, 0.0, 0.0))
    fake = FakeSerial(packet)  # one packet, just to get connected + latest populated
    monkeypatch.setattr(board_module.serial, "Serial", lambda *a, **k: fake)

    b = board_module.Board(port="COMX", tare_sample_count=3)
    b.connect()
    try:
        assert _wait_until(lambda: b.latest is not None)

        def run_tare():
            b.tare()

        t = threading.Thread(target=run_tare)
        t.start()
        # wait for tare() to flip the flag before feeding the samples it
        # needs to collect - otherwise the fake serial (which delivers
        # bytes instantly, unlike real hardware) could deliver and consume
        # them before taring is even armed, leaving tare() waiting forever.
        assert _wait_until(lambda: b._taring is True)

        fake.feed(packet * 3)  # enough samples to complete the tare
        t.join(timeout=2.0)
        assert not t.is_alive(), "tare() did not return in time"

        assert b._tare_offsets[:4] == [2.0, 2.0, 2.0, 2.0]

        # feed one more identical packet; post-tare forces should be ~0
        fake.feed(packet)
        before = b.latest
        assert _wait_until(lambda: b.latest is not before)
        assert b.latest.forces[:4] == [0.0, 0.0, 0.0, 0.0]
    finally:
        b.disconnect()
