import time

import serial

from mobbo import board as board_module
from mobbo.exceptions import ConnectionError as MobboConnectionError
from tests.fake_serial import FakeSerial, RaisingFakeSerial
from tests.helpers import build_packet


def _wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_connect_failure_raises_mobbo_connection_error(monkeypatch):
    def raise_serial_exception(*args, **kwargs):
        raise serial.SerialException("port not found")

    monkeypatch.setattr(board_module.serial, "Serial", raise_serial_exception)

    b = board_module.Board(port="COM_NOPE")
    try:
        b.connect()
        assert False, "expected ConnectionError"
    except MobboConnectionError:
        pass


def test_connect_starts_reader_thread_and_updates_latest(monkeypatch):
    packet = build_packet((1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 7.0))
    fake = FakeSerial(packet)
    monkeypatch.setattr(board_module.serial, "Serial", lambda *a, **k: fake)

    b = board_module.Board(port="COMX")
    b.connect()
    try:
        assert _wait_until(lambda: b.latest is not None)
        assert b.status == "connected"
        assert b.latest.pulse == 7
        assert b.latest.forces == [1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0]
    finally:
        b.disconnect()

    assert b.status == "disconnected"
    assert fake.closed is True


def test_on_sample_callback_fires(monkeypatch):
    packet = build_packet((1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 7.0))
    fake = FakeSerial(packet)
    monkeypatch.setattr(board_module.serial, "Serial", lambda *a, **k: fake)

    received = []
    b = board_module.Board(port="COMX")
    b.on_sample(lambda sample: received.append(sample))
    b.connect()
    try:
        assert _wait_until(lambda: len(received) > 0)
        assert received[0].pulse == 7
    finally:
        b.disconnect()


def test_reader_thread_error_sets_status_and_fires_on_error(monkeypatch):
    fake = RaisingFakeSerial(b"", fail_after_reads=0)
    monkeypatch.setattr(board_module.serial, "Serial", lambda *a, **k: fake)

    errors = []
    b = board_module.Board(port="COMX")
    b.on_error(lambda exc: errors.append(exc))
    b.connect()
    try:
        assert _wait_until(lambda: b.status == "error")
        assert len(errors) == 1
    finally:
        b.disconnect()
