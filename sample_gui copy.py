#!/usr/bin/env python3
"""PySide6 UI for live two-board force platform COP display.

Demo/example app for the `mobbo` library - all serial I/O, packet
parsing, COP math, tare, and session recording live in `mobbo.Board`.
This file only owns the Qt widgets and wires them to the library.

Two screens:
  1) LoginScreen  - enter subject name/number
  2) MonitorScreen - live combined-board COP view + right-side control panel
     with Record / Tare controls, fully dark-themed.
"""

import sys
import threading
import time

import mobbo
from mobbo.constants import BOARD_LENGTH_CM, BOARD_WIDTH_CM, LAYOUT_FRONT_BACK, LAYOUT_SIDE_BY_SIDE
from mobbo.cop import board_labels, board_offsets
from PySide6.QtCore import QObject, QSize, Qt, QRectF, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


DEFAULT_PORT = "COM10"
DEFAULT_PX_PER_CM = 6.0  # only used to size the canvas's initial sizeHint - actual
                         # painting scale is computed per-resize so the board fills
                         # whatever space the window (e.g. maximized/fullscreen) gives it
CANVAS_MARGIN = 30
UI_REFRESH_MS = 33
LAYOUT_OPTIONS = {
    "1 x 2 - Board 1 right foot": LAYOUT_SIDE_BY_SIDE,
    "2 x 1 - Board 1 front": LAYOUT_FRONT_BACK,
}

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


# ---------------------------------------------------------------------------
# mobbo.Board bridge - Board's callbacks fire on its background reader
# thread; Qt widgets may only be touched from the GUI thread, so callbacks
# just emit a signal and the actual widget updates happen in slots below.
# ---------------------------------------------------------------------------

class BoardSignals(QObject):
    sample_received = Signal(object)  # mobbo.EnrichedSample
    error_occurred = Signal(str)
    tare_done = Signal()


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

class CombinedBoardCanvas(QWidget):
    """Both boards drawn to a FIXED real-world scale (same physical size in
    both layouts), centered within the canvas. Shows W1/W2/Total weight as
    text at the top, each board's own COP as a small marker, and the
    combined COP as a blue marker (only above the weight threshold).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout_key = LAYOUT_SIDE_BY_SIDE
        self.cop1 = mobbo.BoardCop(0.0, 0.0, 0.0, False)
        self.cop2 = mobbo.BoardCop(0.0, 0.0, 0.0, False)
        self.combined = mobbo.BoardCop(0.0, 0.0, 0.0, False)
        self.setStyleSheet(f"background-color: {COLOR_PANEL};")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(360, 260)

    def sizeHint(self):
        # Same bounding box for BOTH configs so the board never changes
        # rendered size when you switch layouts. Only used to size the
        # window on first show - paintEvent computes the real scale from
        # whatever size the widget actually ends up with.
        world_w = BOARD_WIDTH_CM + BOARD_WIDTH_CM
        world_h = BOARD_LENGTH_CM * 2
        px_w = int(world_w * DEFAULT_PX_PER_CM) + 2 * CANVAS_MARGIN
        px_h = int(world_h * DEFAULT_PX_PER_CM) + 2 * CANVAS_MARGIN + 30  # +30 for header text
        return QSize(px_w, px_h)

    def set_layout_key(self, layout_key: str):
        self.layout_key = layout_key
        self.update()

    def set_data(self, cop1: mobbo.BoardCop, cop2: mobbo.BoardCop, combined: mobbo.BoardCop):
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

        offset1, offset2 = board_offsets(self.layout_key)
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
        world_w = max_x - min_x
        world_h = max_y - min_y

        # Fill whatever space this widget actually has (fixed size before,
        # now resizable) while preserving the boards' real-world aspect
        # ratio - "contain" scaling, so fullscreen/maximized windows make
        # the board bigger instead of leaving it pinned at a fixed pixel size.
        draw_w = max(self.width() - 2 * CANVAS_MARGIN, 10)
        draw_h = max(self.height() - header_h - 2 * CANVAS_MARGIN, 10)
        px_per_cm = min(draw_w / world_w, draw_h / world_h)

        cx_px = self.width() / 2.0
        cy_px = header_h + (self.height() - header_h) / 2.0

        def to_px(x, y):
            return (cx_px + (x - world_cx) * px_per_cm, cy_px - (y - world_cy) * px_per_cm)

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

            if cop.valid:
                px, py = to_px(bx + cop.cop_x, by + cop.cop_y)
                painter.setPen(QPen(QColor("#7c2d12"), 1))  # dark outline for contrast
                painter.setBrush(QBrush(QColor(COLOR_LCOP)))
                painter.drawEllipse(QRectF(px - 7, py - 7, 14, 14))

        draw_board(offset1, ["F1", "F2", "F3", "F4"], self.cop1)
        draw_board(offset2, ["F5", "F6", "F7", "F8"], self.cop2)

        if self.combined.valid:
            px, py = to_px(self.combined.cop_x, self.combined.cop_y)
            color = QColor(COLOR_GCOP)
            painter.setPen(QPen(color, 3))
            painter.drawLine(int(px - 12), int(py), int(px + 12), int(py))
            painter.drawLine(int(px), int(py - 12), int(px), int(py + 12))
            painter.setPen(QPen(QColor("#0f172a"), 2))
            painter.setBrush(QBrush(color))
            painter.drawEllipse(QRectF(px - 6, py - 6, 12, 12))
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
        # Reserve width up front so digit-count changes ("0.0 s" -> "123.4 s",
        # "0" -> "255") don't reflow this tile's neighbors in status_row -
        # that reflow was part of what read as "jitter" at high update rates.
        self.value_label.setMinimumWidth(90)
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
    session_started = Signal(str)  # subject_id

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
        self.error_label.setText("")
        self.session_started.emit(subject_id)


# ---------------------------------------------------------------------------
# Monitor screen
# ---------------------------------------------------------------------------

class MonitorScreen(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"background-color: {COLOR_BG};")

        self.subject_id = ""
        self.board: mobbo.Board | None = None
        self.signals = BoardSignals()
        self.signals.sample_received.connect(self._on_sample)
        self.signals.error_occurred.connect(self._on_error)
        self.signals.tare_done.connect(self._on_tare_done)

        self.latest_sample: mobbo.EnrichedSample | None = None
        self._pending_sample: mobbo.EnrichedSample | None = None
        self.recording = False
        self.record_start_time = None
        self._last_sample_time_ms = None
        self._sample_frequency_hz = None

        self.build_ui()

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._tick_timer)
        self.poll_timer.start(UI_REFRESH_MS)

    def set_session(self, subject_id: str):
        self.subject_id = subject_id
        self.session_tile.set_value(subject_id)

    # --- UI ----------------------------------------------------------------

    def build_ui(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setSpacing(30)

        # --- Left: board display, centered content, config picker above ----
        left = QVBoxLayout()
        left.setSpacing(14)

        title = QLabel("Two Board COP Monitor")
        title.setStyleSheet(f"color: {COLOR_TITLE}; font-size: 20pt; font-weight: 700; background: transparent;")
        left.addWidget(title)

        config_row = QHBoxLayout()
        config_label = QLabel("Board Configuration")
        config_label.setStyleSheet(f"color: {COLOR_TEXT}; font-size: 11pt; background: transparent;")
        config_row.addWidget(config_label)
        self.config_combo = QComboBox()
        self.config_combo.addItems(list(LAYOUT_OPTIONS.keys()))
        self.config_combo.setFixedWidth(260)
        self.config_combo.setStyleSheet(self._combo_style())
        self.config_combo.currentTextChanged.connect(self.on_config_changed)
        config_row.addWidget(self.config_combo)
        config_row.addStretch(1)
        left.addLayout(config_row)

        self.combined_canvas = CombinedBoardCanvas()
        left.addWidget(self.combined_canvas, 1)

        outer.addLayout(left, 1)

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
        self.frequency_tile = StatTile("Sample Hz")
        self.frequency_tile.set_value("--")
        status_row.addWidget(self.status_tile)
        status_row.addWidget(self.timer_tile)
        status_row.addWidget(self.frequency_tile)
        right.addLayout(status_row)

        self.b1_cop_tile = StatTile("B1 COP x, y")
        self.b2_cop_tile = StatTile("B2 COP x, y")
        self.combined_cop_tile = StatTile("Combined COP x, y")
        for tile in (self.b1_cop_tile, self.b2_cop_tile, self.combined_cop_tile):
            tile.value_label.setMinimumWidth(220)
            tile.set_value("x --, y --")
            right.addWidget(tile)

        self._set_status_display(connected=False, text="Disconnected")

        self.tare_button = _make_button("Tare")
        self.tare_button.clicked.connect(self.send_tare)
        right.addWidget(self.tare_button)

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

        self.balance_bar = FootBalanceBar()
        right.addWidget(self.balance_bar)

        right.addStretch(1)
        outer.addLayout(right, 0)

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
        layout_key = LAYOUT_OPTIONS[config_text]
        self.combined_canvas.set_layout_key(layout_key)
        if self.board is not None:
            self.board.layout = layout_key
        label1, label2 = board_labels(layout_key)
        if self.latest_sample is not None:
            self._update_display(self.latest_sample, label1, label2)
        else:
            self.balance_bar.set_values(0.0, 0.0, label1, label2)

    # --- Connection status display -----------------------------------------

    def _set_status_display(self, connected: bool, text: str):
        color = COLOR_GREEN if connected else COLOR_FADED
        self.status_tile.set_value(text, color=color)

    # --- Ports / connection ----------------------------------------------------

    def refresh_ports(self):
        ports = mobbo.list_ports()
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
        if self.board is not None:
            self.disconnect()
            return
        port = self.port_combo.currentText().strip() or DEFAULT_PORT
        if not port:
            QMessageBox.critical(self, "No port selected", "Choose a COM port first (click Refresh if the list is empty).")
            return

        layout_key = LAYOUT_OPTIONS[self.config_combo.currentText()]
        board = mobbo.Board(port=port, layout=layout_key)
        board.on_sample(lambda sample: self.signals.sample_received.emit(sample))
        board.on_error(lambda exc: self.signals.error_occurred.emit(str(exc)))

        self._set_status_display(connected=False, text=f"Opening {port}...")
        try:
            board.connect()
        except mobbo.ConnectionError as exc:
            self._set_status_display(connected=False, text="Disconnected")
            QMessageBox.critical(self, "Connection failed", str(exc))
            return

        self.board = board
        self._set_status_display(connected=True, text=port)
        self.connect_button.setText("Disconnect")
        self.port_combo.setEnabled(False)
        self.refresh_button.setEnabled(False)

    def disconnect(self):
        if self.board is not None:
            self.board.disconnect()
            self.board = None
        self.recording = False
        self.record_button.setText("Record")
        self.record_status_label.setText("Stopped")
        self.record_status_label.setStyleSheet(f"color: {COLOR_TEXT}; font-size: 10pt; font-weight: 600; background: transparent;")
        self.record_indicator.set_active(False)
        self._last_sample_time_ms = None
        self._sample_frequency_hz = None
        if hasattr(self, "frequency_tile"):
            self.frequency_tile.set_value("--")
        self._set_status_display(connected=False, text="Disconnected")
        self.connect_button.setText("Connect")
        self.port_combo.setEnabled(True)
        self.refresh_button.setEnabled(True)

    def send_tare(self):
        if self.board is None or self.board.status != "connected":
            QMessageBox.information(self, "Not connected", "Connect to a board before taring.")
            return
        self.tare_button.setText("Taring...")
        self.tare_button.setEnabled(False)

        board = self.board

        def run_tare():
            board.tare()
            self.signals.tare_done.emit()

        threading.Thread(target=run_tare, daemon=True).start()

    def _on_tare_done(self):
        self.tare_button.setText("Tare")
        self.tare_button.setEnabled(True)
        n = self.board.tare_sample_count if self.board is not None else 0
        QMessageBox.information(self, "Tare complete", f"Zeroed using the average of {n} samples per channel.")

    def _on_error(self, message: str):
        self._set_status_display(connected=False, text=f"Error: {message}")
        QMessageBox.critical(self, "Serial connection error", message)
        self.disconnect()

    def _tick_timer(self):
        # Samples can arrive from the board far faster than a screen can
        # usefully redraw (or a human can read numbers) - _on_sample only
        # stashes the latest one, and this timer (running at UI_REFRESH_MS,
        # ~30fps) is what actually pushes it into the widgets. Repainting
        # on every single incoming sample was the cause of the visible
        # jitter/flicker at high data rates.
        if self._pending_sample is not None:
            sample = self._pending_sample
            self._pending_sample = None
            label1, label2 = board_labels(sample.layout)
            self._update_display(sample, label1, label2)

        if self.recording and self.record_start_time is not None:
            elapsed = time.monotonic() - self.record_start_time
            self.timer_tile.set_value(f"{elapsed:.1f} s")

    # --- Data / recording -------------------------------------------------

    def _on_sample(self, sample: mobbo.EnrichedSample):
        self.latest_sample = sample
        self._pending_sample = sample

    def _format_cop(self, cop: mobbo.BoardCop) -> str:
        if not cop.valid:
            return "x --, y --"
        return f"x {cop.cop_x:.2f}, y {cop.cop_y:.2f}"

    def _update_display(self, sample: mobbo.EnrichedSample, label1: str, label2: str):
        self.combined_canvas.set_data(sample.cop1, sample.cop2, sample.combined)
        self.b1_cop_tile.set_value(self._format_cop(sample.cop1))
        self.b2_cop_tile.set_value(self._format_cop(sample.cop2))
        self.combined_cop_tile.set_value(self._format_cop(sample.combined))
        self.balance_bar.set_values(sample.pct_board1, sample.pct_board2, label1, label2)
        self._update_frequency(sample.time_ms)

    def _update_frequency(self, time_ms: float):
        if self._last_sample_time_ms is not None:
            dt_ms = time_ms - self._last_sample_time_ms
            if dt_ms > 0:
                hz = 1000.0 / dt_ms
                if self._sample_frequency_hz is None:
                    self._sample_frequency_hz = hz
                else:
                    self._sample_frequency_hz = (self._sample_frequency_hz * 0.85) + (hz * 0.15)
                self.frequency_tile.set_value(f"{self._sample_frequency_hz:.1f}")
        self._last_sample_time_ms = time_ms

    def toggle_recording(self):
        if self.board is None or self.board.status != "connected":
            QMessageBox.information(self, "Not connected", "Connect to a board before recording.")
            return

        if not self.recording:
            try:
                self.board.start_recording(self.subject_id or "subject")
            except (mobbo.RecordingError, OSError) as exc:
                QMessageBox.critical(self, "Could not start recording", str(exc))
                return
            self.recording = True
            self.record_start_time = time.monotonic()
            self.timer_tile.set_value("0.0 s")
            self.record_button.setText("Stop")
            self.record_status_label.setText("Recording")
            self.record_status_label.setStyleSheet(f"color: {COLOR_RED}; font-size: 10pt; font-weight: 600; background: transparent;")
        else:
            try:
                csv_path = self.board.stop_recording()
            except mobbo.RecordingError as exc:
                QMessageBox.critical(self, "Could not stop recording", str(exc))
                return
            self.recording = False
            self.record_button.setText("Record")
            self.record_status_label.setText("Stopped")
            self.record_status_label.setStyleSheet(f"color: {COLOR_TEXT}; font-size: 10pt; font-weight: 600; background: transparent;")
            QMessageBox.information(self, "Saved", f"Saved to:\n{csv_path}")
        self.record_indicator.set_active(self.recording)

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

    def on_session_started(self, subject_id: str):
        self.monitor_screen.set_session(subject_id)
        self.stack.setCurrentWidget(self.monitor_screen)

    def closeEvent(self, event):
        self.monitor_screen.disconnect()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
