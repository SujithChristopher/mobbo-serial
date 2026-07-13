#!/usr/bin/env python3
"""PySide6 UI for live two-board force platform COP display.

Two screens:
  1) LoginScreen  - enter subject name/number, creates a dated session folder
  2) MonitorScreen - live combined-board COP view + right-side control panel
     with Save CSV / Record / Tare controls, fully dark-themed.
"""

import csv
import multiprocessing
import os
import platform
import queue
import struct
import sys
import time
from dataclasses import dataclass
from datetime import datetime

import serial
import serial.tools.list_ports
from PySide6.QtCore import Qt, QTimer, QRectF, Signal
from PySide6.QtGui import QPainter, QPen, QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QComboBox,
    QMessageBox,
    QFrame,
)


BAUD_RATE = 921600
DEFAULT_PORT = "COM10"
HEADER_BYTES = (0xFF, 0xFF)
PAYLOAD_FLOATS = 10
PAYLOAD_SIZE = PAYLOAD_FLOATS * 4
BOARD_WIDTH_CM = 57.5
BOARD_LENGTH_CM = 42.5
PX_PER_CM = 6.0  # fixed scale - boards render the same physical size in both configs
CANVAS_MARGIN = 30
UI_REFRESH_MS = 33
CONNECT_TIMEOUT_S = 4.0
WEIGHT_THRESHOLD_KG = 2.0  # combined COP is only computed/plotted above this
CONFIG_SIDE_BY_SIDE = "1 x 2 - Board 1 right foot"
CONFIG_FRONT_BACK = "2 x 1 - Board 1 front"
TARE_SAMPLE_COUNT = 100  # samples averaged per channel to compute the tare (zero) offset

# --- Dark theme ------------------------------------------------------------
COLOR_BG = "#0b0e14"          # window / outer background
COLOR_PANEL = "#12161d"       # canvas / bar-chart backdrop panels
COLOR_TEXT = "#9ca3af"        # secondary label text
COLOR_TITLE = "#f1f5f9"       # headings / primary text
COLOR_MUTED = "#64748b"       # on-board muted labels (board face stays light)
COLOR_GRID = "#cbd5e1"        # on-board gridlines (board face stays light)
COLOR_BORDER = "#374151"      # control borders
COLOR_ACCENT = "#2563eb"
COLOR_ACCENT_HOVER = "#3b82f6"
COLOR_GREEN = "#22c55e"
COLOR_RED = "#ef4444"
COLOR_FADED = "#4b5563"
COLOR_LCOP = "#f97316"   # local (per-board) COP - high-contrast orange against the light board face
COLOR_GCOP = "#0ea5e9"   # global/combined COP - high-contrast cyan-blue, clearly distinct from LCOP


def get_base_sessions_dir() -> str:
    """Base folder for session data. Fixed to C: drive on Windows so a
    frozen .exe behaves the same no matter which machine or folder it's
    launched from. Falls back to the user's home directory on non-Windows
    systems (only relevant for development/testing)."""
    if platform.system() == "Windows":
        return "C:/LCOP_Sessions"
    return os.path.join(os.path.expanduser("~"), "LCOP_Sessions")


@dataclass
class Sample:
    time_ms: float
    forces: list[float]
    pulse: int


@dataclass
class BoardCop:
    cop_x: float
    cop_y: float
    total_force: float
    valid: bool


def pop_binary_payload(buffer: bytearray) -> bytes | None:
    while len(buffer) >= 4:
        if buffer[0] != HEADER_BYTES[0] or buffer[1] != HEADER_BYTES[1]:
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
    if len(payload) != PAYLOAD_SIZE:
        return None
    try:
        values = struct.unpack("<10f", payload)
    except struct.error:
        return None
    return Sample(values[0], list(values[1:9]), int(round(values[9])))


def compute_board_cop(forces: list[float], start_index: int) -> BoardCop:
    f1, f2, f3, f4 = forces[start_index:start_index + 4]
    total = f1 + f2 + f3 + f4
    if abs(total) < 1e-6:
        return BoardCop(0.0, 0.0, total, False)

    cop_x = (((f1 + f4) - (f2 + f3)) / total) * (BOARD_WIDTH_CM / 2.0)
    cop_y = (((f1 + f2) - (f3 + f4)) / total) * (BOARD_LENGTH_CM / 2.0)
    return BoardCop(cop_x, cop_y, total, True)


def board_offsets(config_text: str):
    """Board 1's center is always the origin (0, 0). Board 2 sits directly
    adjacent to it, in real board dimensions, per the selected layout.

    NOTE: Board 2's offset direction (negative X or Y) is an assumption -
    flip the sign in here if it's mirrored for your actual physical setup.
    """
    if config_text == CONFIG_FRONT_BACK:
        return (0.0, 0.0), (0.0, -BOARD_LENGTH_CM)
    return (0.0, 0.0), (-BOARD_WIDTH_CM, 0.0)


def board_labels(config_text: str):
    if config_text == CONFIG_FRONT_BACK:
        return "Front", "Back"
    return "Right foot", "Left foot"


def compute_combined_cop(cop1: BoardCop, cop2: BoardCop, offset1, offset2) -> BoardCop:
    """Combine both boards' COPs into one global COP, in Board 1's frame.
    Below WEIGHT_THRESHOLD_KG combined weight, the COP is not considered
    reliable and is reported as (0, 0) / invalid (not plotted)."""
    total_weight = cop1.total_force + cop2.total_force
    if total_weight <= WEIGHT_THRESHOLD_KG:
        return BoardCop(0.0, 0.0, total_weight, False)

    gx1 = offset1[0] + cop1.cop_x
    gy1 = offset1[1] + cop1.cop_y
    gx2 = offset2[0] + cop2.cop_x
    gy2 = offset2[1] + cop2.cop_y
    cop_x = (cop1.total_force * gx1 + cop2.total_force * gx2) / total_weight
    cop_y = (cop1.total_force * gy1 + cop2.total_force * gy2) / total_weight
    return BoardCop(cop_x, cop_y, total_weight, True)


def serial_worker(port: str, baud: int, out_queue, cmd_queue, stop_event):
    """Runs in its own OS process (not a thread) so a stuck blocking call
    (e.g. serial.Serial() hanging on Windows) can be killed outright with
    .terminate() instead of freezing the whole app."""
    rx_buffer = bytearray()
    ser = None
    try:
        ser = serial.Serial(port, baud, timeout=0, write_timeout=0)
        out_queue.put(("connected", port))
        try:
            ser.reset_input_buffer()
            ser.write(b"0")
            ser.flush()
            ser.reset_input_buffer()
        except serial.SerialTimeoutException:
            pass

        while not stop_event.is_set():
            try:
                while True:
                    cmd = cmd_queue.get_nowait()
                    ser.write(cmd)
                    ser.flush()
            except queue.Empty:
                pass

            chunk = ser.read(ser.in_waiting or 1)
            if chunk:
                rx_buffer.extend(chunk)

            while True:
                payload = pop_binary_payload(rx_buffer)
                if payload is None:
                    break
                sample = parse_payload(payload)
                if sample is not None:
                    out_queue.put(("sample", sample))

            time.sleep(0.001)
    except Exception as exc:
        out_queue.put(("error", str(exc)))
    finally:
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass
        out_queue.put(("disconnected", None))


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

class CombinedBoardCanvas(QWidget):
    """Both boards drawn to a FIXED real-world scale (same physical size in
    both layouts), centered within the canvas. Shows W1/W2/Total weight as
    text at the top, each board's own COP as a small marker, and the
    combined COP as a larger blue marker (only above the weight threshold).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.config_text = CONFIG_SIDE_BY_SIDE
        self.cop1 = BoardCop(0.0, 0.0, 0.0, False)
        self.cop2 = BoardCop(0.0, 0.0, 0.0, False)
        self.combined = BoardCop(0.0, 0.0, 0.0, False)
        self.setStyleSheet(f"background-color: {COLOR_PANEL};")
        self._apply_fixed_size()

    def _apply_fixed_size(self):
        # Same bounding box for BOTH configs so the board never changes
        # rendered size when you switch layouts.
        world_w = BOARD_WIDTH_CM + BOARD_WIDTH_CM
        world_h = BOARD_LENGTH_CM * 2
        px_w = int(world_w * PX_PER_CM) + 2 * CANVAS_MARGIN
        px_h = int(world_h * PX_PER_CM) + 2 * CANVAS_MARGIN + 30  # +30 for header text
        self.setFixedSize(px_w, px_h)

    def set_config(self, config_text: str):
        self.config_text = config_text
        self.update()

    def set_data(self, cop1: BoardCop, cop2: BoardCop, combined: BoardCop):
        self.cop1 = cop1
        self.cop2 = cop2
        self.combined = combined
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(COLOR_PANEL))

        header_h = 30
        painter.setFont(QFont("Segoe UI", 10, QFont.Bold))
        painter.setPen(QColor(COLOR_TITLE))
        w1 = self.cop1.total_force
        w2 = self.cop2.total_force
        total = w1 + w2
        painter.drawText(
            QRectF(0, 4, self.width(), 20), Qt.AlignCenter,
            f"W1: {w1:.2f} kg     W2: {w2:.2f} kg     Total: {total:.2f} kg",
        )

        offset1, offset2 = board_offsets(self.config_text)
        half_w = BOARD_WIDTH_CM / 2.0
        half_l = BOARD_LENGTH_CM / 2.0

        # Bounding box of both boards together, centered within the canvas
        # (below the header strip), independent of which config is active.
        xs = [offset1[0] - half_w, offset1[0] + half_w, offset2[0] - half_w, offset2[0] + half_w]
        ys = [offset1[1] - half_l, offset1[1] + half_l, offset2[1] - half_l, offset2[1] + half_l]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        world_cx = (min_x + max_x) / 2.0
        world_cy = (min_y + max_y) / 2.0

        cx_px = self.width() / 2.0
        cy_px = header_h + (self.height() - header_h) / 2.0

        def to_px(x, y):
            return (cx_px + (x - world_cx) * PX_PER_CM, cy_px - (y - world_cy) * PX_PER_CM)

        def draw_board(offset, force_labels, cop):
            bx, by = offset
            tl = to_px(bx - half_w, by + half_l)
            br = to_px(bx + half_w, by - half_l)
            rx, ry = min(tl[0], br[0]), min(tl[1], br[1])
            rw, rh = abs(br[0] - tl[0]), abs(br[1] - tl[1])

            painter.setPen(QPen(QColor("black"), 2))
            painter.setBrush(QBrush(QColor("#f8fafc")))
            painter.drawRect(QRectF(rx, ry, rw, rh))

            mx, my = to_px(bx, by)
            painter.setPen(QPen(QColor(COLOR_GRID), 1))
            painter.drawLine(int(mx), int(ry), int(mx), int(ry + rh))
            painter.drawLine(int(rx), int(my), int(rx + rw), int(my))

            painter.setFont(QFont("Segoe UI", 9, QFont.Bold))
            painter.setPen(QColor(COLOR_MUTED))
            painter.drawText(QRectF(rx + 4, ry + 4, 54, 16), Qt.AlignLeft, force_labels[0])
            painter.drawText(QRectF(rx + rw - 58, ry + 4, 54, 16), Qt.AlignRight, force_labels[1])
            painter.drawText(QRectF(rx + rw - 58, ry + rh - 20, 54, 16), Qt.AlignRight, force_labels[2])
            painter.drawText(QRectF(rx + 4, ry + rh - 20, 54, 16), Qt.AlignLeft, force_labels[3])

            if cop.valid and cop.total_force > WEIGHT_THRESHOLD_KG:
                px, py = to_px(bx + cop.cop_x, by + cop.cop_y)
                painter.setPen(QPen(QColor("#7c2d12"), 1))  # dark outline for contrast
                painter.setBrush(QBrush(QColor(COLOR_LCOP)))
                painter.drawEllipse(QRectF(px - 7, py - 7, 14, 14))

        draw_board(offset1, ["F1", "F2", "F3", "F4"], self.cop1)
        draw_board(offset2, ["F5", "F6", "F7", "F8"], self.cop2)

        if self.combined.valid:
            px, py = to_px(self.combined.cop_x, self.combined.cop_y)
            color = QColor(COLOR_GCOP)
            painter.setPen(QPen(color, 4))
            painter.drawLine(int(px - 18), int(py), int(px + 18), int(py))
            painter.drawLine(int(px), int(py - 18), int(px), int(py + 18))
            painter.setPen(QPen(QColor("#0f172a"), 2))
            painter.setBrush(QBrush(color))
            painter.drawEllipse(QRectF(px - 9, py - 9, 18, 18))
        painter.end()


class FootBalanceBar(QWidget):
    """Compact, light-blue, low-alpha bar chart for the control panel,
    showing each board's % share of the combined weight."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(260)
        self.setFixedHeight(150)
        self.setStyleSheet(f"background-color: {COLOR_PANEL};")
        self.pct1 = 0.0
        self.pct2 = 0.0
        self.label1 = "Board 1"
        self.label2 = "Board 2"

    def set_values(self, pct1: float, pct2: float, label1: str, label2: str):
        self.pct1 = pct1
        self.pct2 = pct2
        self.label1 = label1
        self.label2 = label2
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(COLOR_PANEL))

        margin_x = 26
        margin_top = 28
        margin_bottom = 36
        chart_w = self.width() - 2 * margin_x
        chart_h = self.height() - margin_top - margin_bottom
        bar_w = chart_w * 0.28
        gap = chart_w * 0.16
        base_y = margin_top + chart_h

        fill_color = QColor(135, 206, 250, 110)
        border_color = QColor(59, 130, 246, 210)

        bars = [(self.label1, self.pct1), (self.label2, self.pct2)]
        total_w = bar_w * 2 + gap
        start_x = margin_x + (chart_w - total_w) / 2.0

        painter.setPen(QPen(QColor(COLOR_BORDER), 1))
        painter.drawLine(int(margin_x), int(base_y), int(self.width() - margin_x), int(base_y))

        for i, (label, pct) in enumerate(bars):
            x = start_x + i * (bar_w + gap)
            bar_h = chart_h * max(0.0, min(100.0, pct)) / 100.0
            y = base_y - bar_h

            painter.setPen(QPen(border_color, 2))
            painter.setBrush(QBrush(fill_color))
            painter.drawRect(QRectF(x, y, bar_w, bar_h))

            painter.setFont(QFont("Segoe UI", 13, QFont.Bold))
            painter.setPen(QColor(COLOR_TITLE))
            painter.drawText(QRectF(x - 20, y - 26, bar_w + 40, 22), Qt.AlignCenter, f"{pct:.0f}%")

            painter.setFont(QFont("Segoe UI", 9, QFont.Bold))
            painter.setPen(QColor(COLOR_TEXT))
            painter.drawText(QRectF(x - 20, base_y + 8, bar_w + 40, 20), Qt.AlignCenter, label)

        painter.end()


class RecordIndicator(QWidget):
    """Small circular REC indicator - filled red while recording, hollow
    gray outline while stopped."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self.active = False

    def set_active(self, active: bool):
        self.active = active
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self.active:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(COLOR_RED)))
        else:
            painter.setPen(QPen(QColor(COLOR_FADED), 2))
            painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QRectF(2, 2, 12, 12))
        painter.end()


class StatTile(QWidget):
    def __init__(self, label_text: str, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        label = QLabel(label_text)
        label.setStyleSheet(f"color: {COLOR_TEXT}; font-size: 10pt; background: transparent;")
        self.value_label = QLabel("--")
        self.value_label.setStyleSheet(f"color: {COLOR_TITLE}; font-size: 12pt; font-weight: 700; background: transparent;")
        layout.addWidget(label)
        layout.addWidget(self.value_label)

    def set_value(self, text: str, color: str | None = None):
        self.value_label.setText(text)
        c = color or COLOR_TITLE
        self.value_label.setStyleSheet(f"color: {c}; font-size: 12pt; font-weight: 700; background: transparent;")


def _make_button(text: str, primary: bool = False) -> QPushButton:
    button = QPushButton(text)
    if primary:
        button.setStyleSheet(
            f"QPushButton {{ background-color: {COLOR_ACCENT}; color: white; font-weight: 700; "
            f"padding: 8px 14px; border-radius: 4px; border: none; }}"
            f"QPushButton:hover {{ background-color: {COLOR_ACCENT_HOVER}; }}"
            f"QPushButton:disabled {{ background-color: #374151; color: #6b7280; }}"
        )
    else:
        button.setStyleSheet(
            f"QPushButton {{ background-color: #1a1f28; color: {COLOR_TITLE}; font-weight: 600; "
            f"padding: 8px 14px; border: 1px solid {COLOR_BORDER}; border-radius: 4px; }}"
            f"QPushButton:hover {{ background-color: #232a36; }}"
            f"QPushButton:disabled {{ color: #4b5563; }}"
        )
    return button


# ---------------------------------------------------------------------------
# Login screen
# ---------------------------------------------------------------------------

class LoginScreen(QWidget):
    session_started = Signal(str, str)  # subject_id, session_dir

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"background-color: {COLOR_BG};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(60, 60, 60, 60)
        layout.setSpacing(18)
        layout.addStretch(1)

        title = QLabel("Two Board COP Monitor")
        title.setStyleSheet(f"color: {COLOR_TITLE}; font-size: 22pt; font-weight: 700; background: transparent;")
        layout.addWidget(title, alignment=Qt.AlignHCenter)

        subtitle = QLabel("Enter subject name or number to start a session")
        subtitle.setStyleSheet(f"color: {COLOR_TEXT}; font-size: 11pt; background: transparent;")
        layout.addWidget(subtitle, alignment=Qt.AlignHCenter)

        self.subject_edit = QLineEdit()
        self.subject_edit.setPlaceholderText("Subject name or number")
        self.subject_edit.setFixedWidth(320)
        self.subject_edit.setStyleSheet(
            f"QLineEdit {{ background-color: {COLOR_PANEL}; color: {COLOR_TITLE}; padding: 8px 10px; "
            f"border: 1px solid {COLOR_BORDER}; border-radius: 4px; font-size: 12pt; }}"
        )
        self.subject_edit.returnPressed.connect(self.start_session)
        layout.addWidget(self.subject_edit, alignment=Qt.AlignHCenter)

        self.login_button = _make_button("Start Session", primary=True)
        self.login_button.setFixedWidth(200)
        self.login_button.clicked.connect(self.start_session)
        layout.addWidget(self.login_button, alignment=Qt.AlignHCenter)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet(f"color: {COLOR_RED}; font-size: 10pt; background: transparent;")
        layout.addWidget(self.error_label, alignment=Qt.AlignHCenter)

        layout.addStretch(2)

        bottom_row = QHBoxLayout()
        bottom_row.addStretch(1)
        credit_label = QLabel("by BioRehab Group")
        credit_label.setStyleSheet(f"color: {COLOR_MUTED}; font-size: 9pt; background: transparent;")
        bottom_row.addWidget(credit_label)
        layout.addLayout(bottom_row)

    def start_session(self):
        subject_id = self.subject_edit.text().strip()
        if not subject_id:
            self.error_label.setText("Enter a subject name or number first.")
            return
        safe_id = "".join(c for c in subject_id if c.isalnum() or c in ("-", "_")) or "subject"

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_dir = get_base_sessions_dir()
        session_dir = os.path.join(base_dir, f"{safe_id}_{timestamp}")
        try:
            os.makedirs(session_dir, exist_ok=True)
        except OSError as exc:
            self.error_label.setText(f"Could not create session folder: {exc}")
            return

        self.error_label.setText("")
        self.session_started.emit(subject_id, session_dir)


# ---------------------------------------------------------------------------
# Monitor screen
# ---------------------------------------------------------------------------

class MonitorScreen(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"background-color: {COLOR_BG};")

        self.subject_id = ""
        self.session_dir = ""
        self.sample_queue = multiprocessing.Queue()
        self.command_queue = None
        self.stop_event = None
        self.reader = None
        self.connected = False
        self.connect_attempt_time = None
        self.latest_sample = None
        self.recording = False
        self.record_start_time = None
        self.record_buffer = []
        self.tare_offsets = [0.0] * 8
        self.taring_active = False
        self.tare_buffer = []

        self.build_ui()

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.process_queue)
        self.poll_timer.start(UI_REFRESH_MS)

    def set_session(self, subject_id: str, session_dir: str):
        self.subject_id = subject_id
        self.session_dir = session_dir
        self.session_tile.set_value(f"{subject_id}")
        self.record_buffer.clear()

    # --- UI ----------------------------------------------------------------

    def build_ui(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setSpacing(30)

        # --- Left: board display, centered content, config picker above ----
        left = QVBoxLayout()
        left.setSpacing(14)
        left.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        title = QLabel("Two Board COP Monitor")
        title.setStyleSheet(f"color: {COLOR_TITLE}; font-size: 20pt; font-weight: 700; background: transparent;")
        left.addWidget(title)

        config_row = QHBoxLayout()
        config_label = QLabel("Board Configuration")
        config_label.setStyleSheet(f"color: {COLOR_TEXT}; font-size: 11pt; background: transparent;")
        config_row.addWidget(config_label)
        self.config_combo = QComboBox()
        self.config_combo.addItems([CONFIG_SIDE_BY_SIDE, CONFIG_FRONT_BACK])
        self.config_combo.setFixedWidth(260)
        self.config_combo.setStyleSheet(self._combo_style())
        self.config_combo.currentTextChanged.connect(self.on_config_changed)
        config_row.addWidget(self.config_combo)
        config_row.addStretch(1)
        left.addLayout(config_row)

        self.combined_canvas = CombinedBoardCanvas()
        left.addWidget(self.combined_canvas, alignment=Qt.AlignLeft | Qt.AlignTop)

        left.addStretch(1)
        outer.addLayout(left, 0)

        divider = QFrame()
        divider.setFrameShape(QFrame.VLine)
        divider.setStyleSheet(f"color: {COLOR_BORDER};")
        outer.addWidget(divider)

        # --- Right: control panel --------------------------------------------
        right = QVBoxLayout()
        right.setSpacing(16)
        right.setAlignment(Qt.AlignTop)

        self.session_tile = StatTile("Subject")
        right.addWidget(self.session_tile)

        port_label = QLabel("COM Port")
        port_label.setStyleSheet(f"color: {COLOR_TEXT}; font-size: 11pt; background: transparent;")
        right.addWidget(port_label)

        port_row = QHBoxLayout()
        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.setInsertPolicy(QComboBox.NoInsert)
        self.port_combo.setCompleter(None)
        self.port_combo.setFixedWidth(150)
        self.port_combo.setStyleSheet(self._combo_style())
        port_row.addWidget(self.port_combo)
        self.refresh_button = _make_button("Refresh")
        self.refresh_button.clicked.connect(self.refresh_ports)
        port_row.addWidget(self.refresh_button)
        right.addLayout(port_row)

        self.connect_button = _make_button("Connect", primary=True)
        self.connect_button.clicked.connect(self.toggle_connection)
        right.addWidget(self.connect_button)

        status_row = QHBoxLayout()
        self.status_tile = StatTile("Status")
        self.timer_tile = StatTile("Timer")
        self.timer_tile.set_value("0.0 s")
        self.pulse_tile = StatTile("Pulse")
        self.pulse_tile.set_value("0")
        status_row.addWidget(self.status_tile)
        status_row.addWidget(self.timer_tile)
        status_row.addWidget(self.pulse_tile)
        right.addLayout(status_row)
        self._set_status_display(connected=False, text="Disconnected")

        self.tare_button = _make_button("Tare")
        self.tare_button.clicked.connect(self.send_tare)
        right.addWidget(self.tare_button)

        self.save_button = _make_button("Save CSV")
        self.save_button.clicked.connect(self.save_csv)
        right.addWidget(self.save_button)

        self.record_button = _make_button("Record", primary=True)
        self.record_button.clicked.connect(self.toggle_recording)
        right.addWidget(self.record_button)

        record_status_row = QHBoxLayout()
        record_status_row.setSpacing(8)
        self.record_indicator = RecordIndicator()
        record_status_row.addWidget(self.record_indicator)
        self.record_status_label = QLabel("Stopped")
        self.record_status_label.setStyleSheet(f"color: {COLOR_TEXT}; font-size: 10pt; font-weight: 600; background: transparent;")
        record_status_row.addWidget(self.record_status_label)
        record_status_row.addStretch(1)
        right.addLayout(record_status_row)

        # Bar chart now lives in the control panel, per request.
        self.balance_bar = FootBalanceBar()
        right.addWidget(self.balance_bar)

        right.addStretch(1)
        outer.addLayout(right, 0)
        outer.addStretch(1)

        self.on_config_changed(self.config_combo.currentText())
        self.refresh_ports()

    def _combo_style(self) -> str:
        return (
            f"QComboBox {{ background-color: {COLOR_PANEL}; color: {COLOR_TITLE}; padding: 4px 6px; "
            f"border: 1px solid {COLOR_BORDER}; border-radius: 4px; }}"
            f"QComboBox:disabled {{ background-color: #0f1319; color: #4b5563; }}"
            f"QComboBox QAbstractItemView {{ background-color: {COLOR_PANEL}; color: {COLOR_TITLE}; "
            f"selection-background-color: {COLOR_ACCENT}; selection-color: white; }}"
        )

    # --- Config / layout -----------------------------------------------------

    def on_config_changed(self, config_text: str):
        self.combined_canvas.set_config(config_text)
        label1, label2 = board_labels(config_text)
        if self.latest_sample is not None:
            self.update_sample(self.latest_sample)
        else:
            self.balance_bar.set_values(0.0, 0.0, label1, label2)

    # --- Connection status display -----------------------------------------

    def _set_status_display(self, connected: bool, text: str):
        color = COLOR_GREEN if connected else COLOR_FADED
        self.status_tile.set_value(text, color=color)

    # --- Ports / connection ----------------------------------------------------

    def refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        current = self.port_combo.currentText().strip()
        self.port_combo.clear()
        self.port_combo.addItems(ports)
        if current and current in ports:
            self.port_combo.setCurrentText(current)
        elif DEFAULT_PORT in ports:
            self.port_combo.setCurrentText(DEFAULT_PORT)
        elif ports:
            self.port_combo.setCurrentText(ports[0])
        else:
            self.port_combo.setCurrentText(DEFAULT_PORT)

    def toggle_connection(self):
        if self.connected or self.reader is not None:
            self.disconnect()
            return
        port = self.port_combo.currentText().strip() or DEFAULT_PORT
        if not port:
            QMessageBox.critical(self, "No port selected", "Choose a COM port first (click Refresh if the list is empty).")
            return

        self.stop_event = multiprocessing.Event()
        self.command_queue = multiprocessing.Queue()
        self.reader = multiprocessing.Process(
            target=serial_worker,
            args=(port, BAUD_RATE, self.sample_queue, self.command_queue, self.stop_event),
            daemon=True,
        )
        self.reader.start()
        self.connect_attempt_time = time.monotonic()
        self._set_status_display(connected=False, text=f"Opening {port}...")
        self.connect_button.setText("Disconnect")
        self.port_combo.setEnabled(False)
        self.refresh_button.setEnabled(False)

    def disconnect(self):
        if self.stop_event is not None:
            self.stop_event.set()
        if self.reader is not None:
            self.reader.join(timeout=0.5)
            if self.reader.is_alive():
                self.reader.terminate()
                self.reader.join(timeout=0.5)
        self.reader = None
        self.command_queue = None
        self.connected = False
        self.connect_attempt_time = None
        self._set_status_display(connected=False, text="Disconnected")
        self.connect_button.setText("Connect")
        self.port_combo.setEnabled(True)
        self.refresh_button.setEnabled(True)

    def send_tare(self):
        if not self.connected:
            QMessageBox.information(self, "Not connected", "Connect to a board before taring.")
            return
        if self.taring_active:
            return
        self.taring_active = True
        self.tare_buffer = []
        self.tare_button.setText("Taring...")
        self.tare_button.setEnabled(False)

    def _finish_taring(self):
        channel_sums = [0.0] * 8
        for forces in self.tare_buffer:
            for i in range(8):
                channel_sums[i] += forces[i]
        n = len(self.tare_buffer)
        self.tare_offsets = [s / n for s in channel_sums]
        self.tare_buffer = []
        self.taring_active = False
        self.tare_button.setText("Tare")
        self.tare_button.setEnabled(True)
        QMessageBox.information(
            self, "Tare complete",
            f"Zeroed using the average of {n} samples per channel.",
        )

    def process_queue(self):
        try:
            while True:
                kind, value = self.sample_queue.get_nowait()
                if kind == "connected":
                    self.connected = True
                    self.connect_attempt_time = None
                    self._set_status_display(connected=True, text=f"{value}")
                elif kind == "sample":
                    self.latest_sample = value
                    self.update_sample(value)
                elif kind == "error":
                    self._set_status_display(connected=False, text=f"Error: {value}")
                    QMessageBox.critical(self, "Serial connection error", value)
                    self.disconnect()
                elif kind == "disconnected":
                    if not self.connected:
                        self.reader = None
                        self.connect_button.setText("Connect")
                        self.port_combo.setEnabled(True)
                        self.refresh_button.setEnabled(True)
        except queue.Empty:
            pass

        if self.connect_attempt_time is not None and not self.connected:
            if time.monotonic() - self.connect_attempt_time > CONNECT_TIMEOUT_S:
                self.connect_attempt_time = None
                self._set_status_display(connected=False, text="Timed out")
                QMessageBox.critical(
                    self,
                    "Connection timed out",
                    f"No response from {self.port_combo.currentText()} within {CONNECT_TIMEOUT_S:.0f}s.\n"
                    "Check the port is correct and the board is powered on.",
                )
                self.disconnect()

        # Timer only runs while actively recording (starts on Record press).
        if self.recording and self.record_start_time is not None:
            elapsed = time.monotonic() - self.record_start_time
            self.timer_tile.set_value(f"{elapsed:.1f} s")

    # --- Data / recording -------------------------------------------------

    def update_sample(self, sample: Sample):
        if self.taring_active:
            self.tare_buffer.append(sample.forces)
            if len(self.tare_buffer) >= TARE_SAMPLE_COUNT:
                self._finish_taring()

        forces = [f - o for f, o in zip(sample.forces, self.tare_offsets)]

        cop1 = compute_board_cop(forces, 0)
        cop2 = compute_board_cop(forces, 4)
        config_text = self.config_combo.currentText()
        offset1, offset2 = board_offsets(config_text)
        combined = compute_combined_cop(cop1, cop2, offset1, offset2)
        self.combined_canvas.set_data(cop1, cop2, combined)

        total_weight = cop1.total_force + cop2.total_force
        if total_weight > WEIGHT_THRESHOLD_KG:
            pct1 = max(0.0, min(100.0, (cop1.total_force / total_weight) * 100.0))
            pct2 = 100.0 - pct1
        else:
            pct1 = pct2 = 0.0
        label1, label2 = board_labels(config_text)
        self.balance_bar.set_values(pct1, pct2, label1, label2)
        self.pulse_tile.set_value(str(sample.pulse))

        if self.recording:
            self.record_buffer.append({
                "time_ms": sample.time_ms,
                "F1": forces[0], "F2": forces[1],
                "F3": forces[2], "F4": forces[3],
                "F5": forces[4], "F6": forces[5],
                "F7": forces[6], "F8": forces[7],
                "pulse": sample.pulse,
                "config": config_text,
                "combined_cop_x": combined.cop_x,
                "combined_cop_y": combined.cop_y,
                "combined_weight": combined.total_force,
                "combined_valid": combined.valid,
                "pct_board1": pct1,
                "pct_board2": pct2,
            })

    def toggle_recording(self):
        self.recording = not self.recording
        if self.recording:
            self.record_start_time = time.monotonic()
            self.timer_tile.set_value("0.0 s")
            self.record_button.setText("Stop")
            self.record_status_label.setText("Recording")
            self.record_status_label.setStyleSheet(f"color: {COLOR_RED}; font-size: 10pt; font-weight: 600; background: transparent;")
        else:
            self.record_button.setText("Record")
            self.record_status_label.setText("Stopped")
            self.record_status_label.setStyleSheet(f"color: {COLOR_TEXT}; font-size: 10pt; font-weight: 600; background: transparent;")
        self.record_indicator.set_active(self.recording)

    def save_csv(self):
        if not self.record_buffer:
            QMessageBox.information(self, "Nothing to save", "No samples recorded yet.")
            return
        if not self.session_dir:
            QMessageBox.warning(self, "No session", "No session folder is set - please log in again.")
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{self.subject_id or 'subject'}_{timestamp}.csv"
        filepath = os.path.join(self.session_dir, filename)

        fieldnames = list(self.record_buffer[0].keys())
        try:
            with open(filepath, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(self.record_buffer)
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", f"Could not write CSV: {exc}")
            return

        QMessageBox.information(self, "Saved", f"Saved {len(self.record_buffer)} samples to:\n{filepath}")
        self.record_buffer.clear()

    def closeEvent(self, event):
        self.disconnect()
        event.accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Two Board COP Monitor")
        self.resize(1150, 760)
        self.setMinimumSize(1000, 680)
        self.setStyleSheet(f"background-color: {COLOR_BG};")

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.login_screen = LoginScreen()
        self.monitor_screen = MonitorScreen()
        self.stack.addWidget(self.login_screen)
        self.stack.addWidget(self.monitor_screen)

        self.login_screen.session_started.connect(self.on_session_started)

    def on_session_started(self, subject_id: str, session_dir: str):
        self.monitor_screen.set_session(subject_id, session_dir)
        self.stack.setCurrentWidget(self.monitor_screen)

    def closeEvent(self, event):
        self.monitor_screen.disconnect()
        event.accept()


if __name__ == "__main__":
    multiprocessing.freeze_support()  # required for multiprocessing in a frozen Windows .exe
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())