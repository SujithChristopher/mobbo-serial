import csv
import time

import pytest

from mobbo import board as board_module, config
from mobbo.exceptions import RecordingError
from tests.fake_serial import FakeSerial
from tests.helpers import build_packet


def _wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_home_dir", lambda: tmp_path)
    (tmp_path / "Documents").mkdir()
    yield tmp_path


def test_start_recording_creates_session_dir_and_stop_recording_writes_rows(monkeypatch):
    packet = build_packet((1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 7.0))
    fake = FakeSerial(packet * 3)
    monkeypatch.setattr(board_module.serial, "Serial", lambda *a, **k: fake)

    b = board_module.Board(port="COMX")
    b.connect()
    try:
        assert _wait_until(lambda: b.latest is not None)
        session_dir = b.start_recording("subject1")
        assert session_dir.exists()

        fake.feed(packet * 2)
        time.sleep(0.2)  # let a few more rows stream in

        csv_path = b.stop_recording()
        assert csv_path.exists()
        assert csv_path.parent == session_dir

        with open(csv_path, newline="") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) >= 1
        assert rows[0]["pulse"] == "7"
        assert rows[0]["f1"] == "1.0"
        assert rows[0]["board1_weight"] == "4.0"
        assert rows[0]["board1_valid"] == "True"
        assert rows[0]["board2_valid"] == "False"
    finally:
        b.disconnect()


def test_start_recording_twice_raises(monkeypatch):
    fake = FakeSerial(b"")
    monkeypatch.setattr(board_module.serial, "Serial", lambda *a, **k: fake)

    b = board_module.Board(port="COMX")
    b.connect()
    try:
        b.start_recording("subject1")
        with pytest.raises(RecordingError):
            b.start_recording("subject1")
    finally:
        b.disconnect()


def test_stop_recording_without_start_raises(monkeypatch):
    fake = FakeSerial(b"")
    monkeypatch.setattr(board_module.serial, "Serial", lambda *a, **k: fake)

    b = board_module.Board(port="COMX")
    b.connect()
    try:
        with pytest.raises(RecordingError):
            b.stop_recording()
    finally:
        b.disconnect()
