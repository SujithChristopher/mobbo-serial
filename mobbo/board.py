import threading
import time
from dataclasses import dataclass
from pathlib import Path

import serial
import serial.tools.list_ports

from . import constants, protocol, storage
from .cop import BoardCop, board_offsets, compute_board_cop, compute_combined_cop
from .exceptions import ConnectionError as MobboConnectionError, RecordingError


@dataclass
class EnrichedSample:
    time_ms: float
    forces: list[float]
    pulse: int
    cop1: BoardCop
    cop2: BoardCop
    combined: BoardCop
    pct_board1: float
    pct_board2: float
    layout: str


def list_ports() -> list[str]:
    return [p.device for p in serial.tools.list_ports.comports()]


class Board:
    def __init__(
        self,
        port: str,
        layout: str = constants.LAYOUT_SIDE_BY_SIDE,
        baud: int = constants.BAUD_RATE,
        tare_sample_count: int = constants.TARE_SAMPLE_COUNT,
    ):
        self.port = port
        self.layout = layout
        self.baud = baud
        self.tare_sample_count = tare_sample_count

        self.status = "disconnected"

        self._serial = None
        self._reader_thread = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._latest: EnrichedSample | None = None

        self._on_sample = None
        self._on_error = None

        self._tare_offsets = [0.0] * 8
        self._taring = False
        self._tare_buffer: list[list[float]] = []
        self._tare_done_event = threading.Event()

        self._recording = False
        self._record_file = None
        self._record_writer = None

    def on_sample(self, callback) -> None:
        self._on_sample = callback

    def on_error(self, callback) -> None:
        self._on_error = callback

    @property
    def latest(self) -> EnrichedSample | None:
        with self._lock:
            return self._latest

    def connect(self) -> None:
        try:
            ser = serial.Serial(self.port, self.baud, timeout=0, write_timeout=0)
        except serial.SerialException as exc:
            raise MobboConnectionError(str(exc)) from exc

        self._serial = ser
        try:
            ser.reset_input_buffer()
            ser.write(b"0")
            ser.flush()
            ser.reset_input_buffer()
        except serial.SerialTimeoutException:
            pass

        self.status = "connected"
        self._stop_event.clear()
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def disconnect(self) -> None:
        self._stop_event.set()
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=2.0)
        self._reader_thread = None

        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None

        if self._record_file is not None:
            self._close_recording()

        self.status = "disconnected"

    def tare(self) -> None:
        self._tare_buffer = []
        self._tare_done_event.clear()
        self._taring = True
        self._tare_done_event.wait()

    def start_recording(self, subject_id: str) -> Path:
        if self._recording:
            raise RecordingError("Already recording; call stop_recording() first.")
        session_dir = storage.create_session_dir(subject_id)
        csv_path = storage.session_csv_path(session_dir)
        self._record_file, self._record_writer = storage.open_csv_writer(csv_path)
        self._recording = True
        return session_dir

    def stop_recording(self) -> Path:
        if not self._recording:
            raise RecordingError("Not currently recording.")
        path = Path(self._record_file.name)
        self._close_recording()
        return path

    def _close_recording(self) -> None:
        if self._record_file is not None:
            self._record_file.flush()
            self._record_file.close()
        self._record_file = None
        self._record_writer = None
        self._recording = False

    def _finish_taring(self) -> None:
        channel_sums = [0.0] * 8
        for forces in self._tare_buffer:
            for i in range(8):
                channel_sums[i] += forces[i]
        n = len(self._tare_buffer)
        self._tare_offsets = [s / n for s in channel_sums]
        self._tare_buffer = []
        self._taring = False
        self._tare_done_event.set()

    def _read_loop(self) -> None:
        rx_buffer = bytearray()
        try:
            while not self._stop_event.is_set():
                chunk = self._serial.read(self._serial.in_waiting or 1)
                if chunk:
                    rx_buffer.extend(chunk)

                while True:
                    payload = protocol.pop_binary_payload(rx_buffer)
                    if payload is None:
                        break
                    sample = protocol.parse_payload(payload)
                    if sample is not None:
                        self._handle_sample(sample)

                time.sleep(constants.READ_POLL_INTERVAL_S)
        except Exception as exc:
            self.status = "error"
            if self._recording:
                self._close_recording()
            if self._on_error is not None:
                self._on_error(exc)

    def _handle_sample(self, sample: protocol.Sample) -> None:
        if self._taring:
            self._tare_buffer.append(sample.forces)
            if len(self._tare_buffer) >= self.tare_sample_count:
                self._finish_taring()

        forces = [f - o for f, o in zip(sample.forces, self._tare_offsets)]

        cop1 = compute_board_cop(forces, 0)
        cop2 = compute_board_cop(forces, 4)
        offset1, offset2 = board_offsets(self.layout)
        combined = compute_combined_cop(cop1, cop2, offset1, offset2)

        total_weight = cop1.total_force + cop2.total_force
        if total_weight > constants.WEIGHT_THRESHOLD_KG:
            pct1 = max(0.0, min(100.0, (cop1.total_force / total_weight) * 100.0))
            pct2 = 100.0 - pct1
        else:
            pct1 = pct2 = 0.0

        enriched = EnrichedSample(
            time_ms=sample.time_ms,
            forces=forces,
            pulse=sample.pulse,
            cop1=cop1,
            cop2=cop2,
            combined=combined,
            pct_board1=pct1,
            pct_board2=pct2,
            layout=self.layout,
        )

        with self._lock:
            self._latest = enriched

        if self._recording and self._record_writer is not None:
            self._write_record_row(enriched)

        if self._on_sample is not None:
            self._on_sample(enriched)

    def _write_record_row(self, enriched: EnrichedSample) -> None:
        forces = enriched.forces
        self._record_writer.writerow({
            "time_ms": enriched.time_ms,
            "F1": forces[0], "F2": forces[1], "F3": forces[2], "F4": forces[3],
            "F5": forces[4], "F6": forces[5], "F7": forces[6], "F8": forces[7],
            "pulse": enriched.pulse,
            "layout": enriched.layout,
            "combined_cop_x": enriched.combined.cop_x,
            "combined_cop_y": enriched.combined.cop_y,
            "combined_weight": enriched.combined.total_force,
            "combined_valid": enriched.combined.valid,
            "pct_board1": enriched.pct_board1,
            "pct_board2": enriched.pct_board2,
        })
        self._record_file.flush()
