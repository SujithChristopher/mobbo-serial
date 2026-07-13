# mobbo Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `mobbo` Python library — a GUI-free port of `ref.py`'s serial connection, packet parsing, COP math, tare, and session recording — as an installable, testable package.

**Architecture:** Small single-purpose modules (`constants`, `exceptions`, `protocol`, `cop`, `config`, `storage`) composed by one stateful `Board` class that owns the serial connection and a background reader thread. Pure-logic modules (`protocol`, `cop`) have no I/O and are unit tested with synthetic data; `board.py` is tested with a fake `serial.Serial` monkeypatched in.

**Tech Stack:** Python 3.13, `pyserial`, `pytest`, `uv` for env/dependency management, `hatchling` as the build backend.

## Global Constraints

- Distribution name `mobbo-serial` (existing `pyproject.toml`), import name `mobbo`.
- `requires-python = ">=3.13"` (existing `pyproject.toml`).
- `pyserial>=3.5` is the only runtime dependency — no Qt, no multiprocessing.
- Config bootstrap file is always at `~/.mobbo/config.json`, independent of the (configurable) `data_dir` it stores.
- Default `data_dir` is `<Documents>/mobbo-data`, falling back to `~/mobbo-data` if a `Documents` folder can't be resolved.
- Recording streams rows to disk as samples arrive — no in-memory buffering, no separate `save()` call.
- Spec reference: `docs/superpowers/specs/2026-07-13-mobbo-serial-design.md`.

---

### Task 1: Project scaffolding

**Files:**
- Modify: `pyproject.toml`
- Create: `mobbo/__init__.py` (empty placeholder for now)
- Create: `tests/__init__.py` (empty)

**Interfaces:**
- Produces: an installed, importable `mobbo` package and a working `uv run pytest` command for every later task.

- [ ] **Step 1: Add pytest as a dev dependency**

Run: `uv add --dev pytest`
Expected: `pyproject.toml` gains a `[dependency-groups]` `dev = ["pytest>=..."]` entry; `uv.lock` updates.

- [ ] **Step 2: Create the package and test directories**

Create `mobbo/__init__.py`:
```python
```
(empty file for now — filled in Task 10)

Create `tests/__init__.py`:
```python
```
(empty file, marks `tests/` as a package so relative imports of `tests.helpers` work)

- [ ] **Step 3: Add the build-system config so `mobbo` is installable**

Edit `pyproject.toml`, add after the existing `[project]` table:
```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["mobbo"]
```

- [ ] **Step 4: Sync and verify the package imports**

Run: `uv sync`
Expected: completes without error, installs `mobbo` itself in editable mode.

Run: `uv run python -c "import mobbo; print('ok')"`
Expected: prints `ok`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock mobbo/__init__.py tests/__init__.py
git commit -m "chore: scaffold mobbo package with hatchling build and pytest"
```

---

### Task 2: constants and exceptions

**Files:**
- Create: `mobbo/constants.py`
- Create: `mobbo/exceptions.py`
- Test: `tests/test_constants_exceptions.py`

**Interfaces:**
- Produces: `constants.BAUD_RATE`, `constants.HEADER_BYTES`, `constants.PAYLOAD_FLOATS`, `constants.PAYLOAD_SIZE`, `constants.BOARD_WIDTH_CM`, `constants.BOARD_LENGTH_CM`, `constants.WEIGHT_THRESHOLD_KG`, `constants.TARE_SAMPLE_COUNT`, `constants.LAYOUT_SIDE_BY_SIDE`, `constants.LAYOUT_FRONT_BACK`; `exceptions.ConnectionError`, `exceptions.RecordingError` (both `Exception` subclasses).

- [ ] **Step 1: Write the failing test**

Create `tests/test_constants_exceptions.py`:
```python
from mobbo import constants, exceptions


def test_protocol_constants_match_device_spec():
    assert constants.BAUD_RATE == 921600
    assert constants.HEADER_BYTES == (0xFF, 0xFF)
    assert constants.PAYLOAD_FLOATS == 10
    assert constants.PAYLOAD_SIZE == 40


def test_physical_constants():
    assert constants.BOARD_WIDTH_CM == 57.5
    assert constants.BOARD_LENGTH_CM == 42.5
    assert constants.WEIGHT_THRESHOLD_KG == 2.0
    assert constants.TARE_SAMPLE_COUNT == 100


def test_layout_constants_are_distinct_strings():
    assert constants.LAYOUT_SIDE_BY_SIDE == "side_by_side"
    assert constants.LAYOUT_FRONT_BACK == "front_back"
    assert constants.LAYOUT_SIDE_BY_SIDE != constants.LAYOUT_FRONT_BACK


def test_exceptions_are_exception_subclasses():
    assert issubclass(exceptions.ConnectionError, Exception)
    assert issubclass(exceptions.RecordingError, Exception)
    assert exceptions.ConnectionError is not exceptions.RecordingError
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_constants_exceptions.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mobbo.constants'`

- [ ] **Step 3: Write `mobbo/constants.py`**

```python
BAUD_RATE = 921600
HEADER_BYTES = (0xFF, 0xFF)
PAYLOAD_FLOATS = 10
PAYLOAD_SIZE = PAYLOAD_FLOATS * 4

BOARD_WIDTH_CM = 57.5
BOARD_LENGTH_CM = 42.5
WEIGHT_THRESHOLD_KG = 2.0
TARE_SAMPLE_COUNT = 100

LAYOUT_SIDE_BY_SIDE = "side_by_side"
LAYOUT_FRONT_BACK = "front_back"

READ_POLL_INTERVAL_S = 0.001
```

- [ ] **Step 4: Write `mobbo/exceptions.py`**

```python
class ConnectionError(Exception):
    """Raised by Board.connect() when the serial port can't be opened."""


class RecordingError(Exception):
    """Raised on invalid start_recording()/stop_recording() usage."""
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_constants_exceptions.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add mobbo/constants.py mobbo/exceptions.py tests/test_constants_exceptions.py
git commit -m "feat: add mobbo constants and exception types"
```

---

### Task 3: protocol layer (packet framing + parsing)

**Files:**
- Create: `mobbo/protocol.py`
- Create: `tests/helpers.py`
- Test: `tests/test_protocol.py`

**Interfaces:**
- Consumes: `constants.HEADER_BYTES`, `constants.PAYLOAD_SIZE`.
- Produces: `protocol.Sample` (dataclass: `time_ms: float`, `forces: list[float]`, `pulse: int`), `protocol.pop_binary_payload(buffer: bytearray) -> bytes | None`, `protocol.parse_payload(payload: bytes) -> Sample | None`. Also `tests/helpers.build_packet(values: tuple[float, ...]) -> bytes`, reused by later test files.

- [ ] **Step 1: Write the packet-building test helper**

Create `tests/helpers.py`:
```python
import struct


def build_packet(values: tuple) -> bytes:
    """Build one valid framed packet (header + len + payload + checksum)
    from 10 float values, matching mobbo.protocol's wire format."""
    payload = struct.pack("<10f", *values)
    packet_len = len(payload) + 1
    checksum = (0xFE + packet_len + sum(payload)) & 0xFF
    return bytes([0xFF, 0xFF, packet_len]) + payload + bytes([checksum])
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_protocol.py`:
```python
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_protocol.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mobbo.protocol'`

- [ ] **Step 4: Write `mobbo/protocol.py`**

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_protocol.py -v`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add mobbo/protocol.py tests/helpers.py tests/test_protocol.py
git commit -m "feat: add mobbo protocol layer (packet framing and parsing)"
```

---

### Task 4: COP math

**Files:**
- Create: `mobbo/cop.py`
- Test: `tests/test_cop.py`

**Interfaces:**
- Consumes: `constants.BOARD_WIDTH_CM`, `constants.BOARD_LENGTH_CM`, `constants.WEIGHT_THRESHOLD_KG`, `constants.LAYOUT_FRONT_BACK`, `constants.LAYOUT_SIDE_BY_SIDE`.
- Produces: `cop.BoardCop` (dataclass: `cop_x: float`, `cop_y: float`, `total_force: float`, `valid: bool`), `cop.compute_board_cop(forces: list[float], start_index: int) -> BoardCop`, `cop.compute_combined_cop(cop1: BoardCop, cop2: BoardCop, offset1: tuple[float, float], offset2: tuple[float, float]) -> BoardCop`, `cop.board_offsets(layout: str) -> tuple[tuple[float, float], tuple[float, float]]`, `cop.board_labels(layout: str) -> tuple[str, str]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cop.py`:
```python
import pytest

from mobbo import constants
from mobbo.cop import (
    BoardCop,
    board_labels,
    board_offsets,
    compute_board_cop,
    compute_combined_cop,
)


def test_compute_board_cop_centered_load():
    forces = [1.0, 1.0, 1.0, 1.0]
    result = compute_board_cop(forces, 0)
    assert result.valid is True
    assert result.total_force == 4.0
    assert result.cop_x == pytest.approx(0.0)
    assert result.cop_y == pytest.approx(0.0)


def test_compute_board_cop_offset_load():
    # all weight on sensor 1 (front-right corner, per ref.py convention)
    forces = [2.0, 0.0, 0.0, 0.0]
    result = compute_board_cop(forces, 0)
    assert result.total_force == 2.0
    assert result.cop_x == pytest.approx(28.75)   # BOARD_WIDTH_CM / 2
    assert result.cop_y == pytest.approx(21.25)   # BOARD_LENGTH_CM / 2


def test_compute_board_cop_zero_total_is_invalid():
    result = compute_board_cop([0.0, 0.0, 0.0, 0.0], 0)
    assert result.valid is False
    assert result.cop_x == 0.0
    assert result.cop_y == 0.0


def test_compute_board_cop_uses_start_index_for_second_board():
    forces = [0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0]
    result = compute_board_cop(forces, 4)
    assert result.total_force == 4.0
    assert result.valid is True


def test_board_offsets_side_by_side():
    offset1, offset2 = board_offsets(constants.LAYOUT_SIDE_BY_SIDE)
    assert offset1 == (0.0, 0.0)
    assert offset2 == (-constants.BOARD_WIDTH_CM, 0.0)


def test_board_offsets_front_back():
    offset1, offset2 = board_offsets(constants.LAYOUT_FRONT_BACK)
    assert offset1 == (0.0, 0.0)
    assert offset2 == (0.0, -constants.BOARD_LENGTH_CM)


def test_board_labels_side_by_side():
    assert board_labels(constants.LAYOUT_SIDE_BY_SIDE) == ("Right foot", "Left foot")


def test_board_labels_front_back():
    assert board_labels(constants.LAYOUT_FRONT_BACK) == ("Front", "Back")


def test_compute_combined_cop_below_threshold_is_invalid():
    cop1 = BoardCop(0.0, 0.0, 1.0, True)
    cop2 = BoardCop(0.0, 0.0, 0.5, True)
    result = compute_combined_cop(cop1, cop2, (0.0, 0.0), (-57.5, 0.0))
    assert result.valid is False
    assert result.total_force == 1.5


def test_compute_combined_cop_weighted_average():
    # cop1 all-weight at board1 center, cop2 empty, side-by-side layout
    cop1 = BoardCop(0.0, 0.0, 3.0, True)
    cop2 = BoardCop(0.0, 0.0, 0.0, False)
    offset1, offset2 = (0.0, 0.0), (-57.5, 0.0)
    result = compute_combined_cop(cop1, cop2, offset1, offset2)
    assert result.valid is True
    assert result.total_force == 3.0
    assert result.cop_x == pytest.approx(0.0)
    assert result.cop_y == pytest.approx(0.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cop.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mobbo.cop'`

- [ ] **Step 3: Write `mobbo/cop.py`**

```python
from dataclasses import dataclass

from . import constants


@dataclass
class BoardCop:
    cop_x: float
    cop_y: float
    total_force: float
    valid: bool


def compute_board_cop(forces: list[float], start_index: int) -> BoardCop:
    f1, f2, f3, f4 = forces[start_index:start_index + 4]
    total = f1 + f2 + f3 + f4
    if abs(total) < 1e-6:
        return BoardCop(0.0, 0.0, total, False)

    cop_x = (((f1 + f4) - (f2 + f3)) / total) * (constants.BOARD_WIDTH_CM / 2.0)
    cop_y = (((f1 + f2) - (f3 + f4)) / total) * (constants.BOARD_LENGTH_CM / 2.0)
    return BoardCop(cop_x, cop_y, total, True)


def board_offsets(layout: str) -> tuple[tuple[float, float], tuple[float, float]]:
    if layout == constants.LAYOUT_FRONT_BACK:
        return (0.0, 0.0), (0.0, -constants.BOARD_LENGTH_CM)
    return (0.0, 0.0), (-constants.BOARD_WIDTH_CM, 0.0)


def board_labels(layout: str) -> tuple[str, str]:
    if layout == constants.LAYOUT_FRONT_BACK:
        return "Front", "Back"
    return "Right foot", "Left foot"


def compute_combined_cop(
    cop1: BoardCop,
    cop2: BoardCop,
    offset1: tuple[float, float],
    offset2: tuple[float, float],
) -> BoardCop:
    total_weight = cop1.total_force + cop2.total_force
    if total_weight <= constants.WEIGHT_THRESHOLD_KG:
        return BoardCop(0.0, 0.0, total_weight, False)

    gx1 = offset1[0] + cop1.cop_x
    gy1 = offset1[1] + cop1.cop_y
    gx2 = offset2[0] + cop2.cop_x
    gy2 = offset2[1] + cop2.cop_y
    cop_x = (cop1.total_force * gx1 + cop2.total_force * gx2) / total_weight
    cop_y = (cop1.total_force * gy1 + cop2.total_force * gy2) / total_weight
    return BoardCop(cop_x, cop_y, total_weight, True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cop.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add mobbo/cop.py tests/test_cop.py
git commit -m "feat: add mobbo COP math (per-board and combined)"
```

---

### Task 5: config (~/.mobbo/config.json)

**Files:**
- Create: `mobbo/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `config._home_dir() -> Path` (seam for tests), `config.get_config() -> dict`, `config.configure(data_dir: str) -> dict`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config.py`:
```python
import json

from mobbo import config


def test_get_config_creates_default_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_home_dir", lambda: tmp_path)
    (tmp_path / "Documents").mkdir()

    result = config.get_config()

    config_path = tmp_path / ".mobbo" / "config.json"
    assert config_path.exists()
    assert result["data_dir"] == str(tmp_path / "Documents" / "mobbo-data")


def test_get_config_falls_back_to_home_without_documents(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_home_dir", lambda: tmp_path)
    # no Documents dir created

    result = config.get_config()

    assert result["data_dir"] == str(tmp_path / "mobbo-data")


def test_get_config_reads_existing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_home_dir", lambda: tmp_path)
    mobbo_dir = tmp_path / ".mobbo"
    mobbo_dir.mkdir()
    (mobbo_dir / "config.json").write_text(json.dumps({"data_dir": "D:/custom"}))

    result = config.get_config()

    assert result["data_dir"] == "D:/custom"


def test_configure_persists_new_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_home_dir", lambda: tmp_path)

    result = config.configure(data_dir="D:/lab-data")

    assert result["data_dir"] == "D:/lab-data"
    reloaded = json.loads((tmp_path / ".mobbo" / "config.json").read_text())
    assert reloaded["data_dir"] == "D:/lab-data"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mobbo.config'`

- [ ] **Step 3: Write `mobbo/config.py`**

```python
import json
from pathlib import Path


def _home_dir() -> Path:
    return Path.home()


def _config_path() -> Path:
    return _home_dir() / ".mobbo" / "config.json"


def _default_data_dir() -> Path:
    documents = _home_dir() / "Documents"
    base = documents if documents.is_dir() else _home_dir()
    return base / "mobbo-data"


def get_config() -> dict:
    path = _config_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        default = {"data_dir": str(_default_data_dir())}
        path.write_text(json.dumps(default, indent=2))
        return default
    return json.loads(path.read_text())


def configure(data_dir: str) -> dict:
    current = get_config()
    current["data_dir"] = data_dir
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, indent=2))
    return current
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add mobbo/config.py tests/test_config.py
git commit -m "feat: add mobbo config bootstrap at ~/.mobbo/config.json"
```

---

### Task 6: storage (session directories + streaming CSV writer)

**Files:**
- Create: `mobbo/storage.py`
- Test: `tests/test_storage.py`

**Interfaces:**
- Consumes: `config.get_config() -> dict` (reads `data_dir`).
- Produces: `storage.CSV_FIELDNAMES: list[str]`, `storage.sanitize_subject_id(subject_id: str) -> str`, `storage.create_session_dir(subject_id: str) -> Path`, `storage.session_csv_path(session_dir: Path) -> Path`, `storage.open_csv_writer(path: Path) -> tuple[TextIO, csv.DictWriter]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_storage.py`:
```python
import csv

from mobbo import config, storage


def test_sanitize_subject_id_strips_unsafe_chars():
    assert storage.sanitize_subject_id("john doe #1!") == "johndoe1"


def test_sanitize_subject_id_falls_back_when_empty():
    assert storage.sanitize_subject_id("###") == "subject"


def test_create_session_dir_uses_configured_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_home_dir", lambda: tmp_path)
    (tmp_path / "Documents").mkdir()

    session_dir = storage.create_session_dir("subject1")

    assert session_dir.exists()
    assert session_dir.parent == tmp_path / "Documents" / "mobbo-data"
    assert session_dir.name.startswith("subject1_")


def test_session_csv_path_matches_session_dir_name(tmp_path):
    session_dir = tmp_path / "subject1_20260713_120000"
    session_dir.mkdir()

    csv_path = storage.session_csv_path(session_dir)

    assert csv_path == session_dir / "subject1_20260713_120000.csv"


def test_open_csv_writer_writes_header(tmp_path):
    path = tmp_path / "out.csv"

    file, writer = storage.open_csv_writer(path)
    file.close()

    with open(path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
    assert header == storage.CSV_FIELDNAMES
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_storage.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mobbo.storage'`

- [ ] **Step 3: Write `mobbo/storage.py`**

```python
import csv
from datetime import datetime
from pathlib import Path
from typing import TextIO

from . import config

CSV_FIELDNAMES = [
    "time_ms", "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "pulse",
    "layout", "combined_cop_x", "combined_cop_y", "combined_weight",
    "combined_valid", "pct_board1", "pct_board2",
]


def sanitize_subject_id(subject_id: str) -> str:
    safe = "".join(c for c in subject_id if c.isalnum() or c in ("-", "_"))
    return safe or "subject"


def create_session_dir(subject_id: str) -> Path:
    safe_id = sanitize_subject_id(subject_id)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    data_dir = Path(config.get_config()["data_dir"])
    session_dir = data_dir / f"{safe_id}_{timestamp}"
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def session_csv_path(session_dir: Path) -> Path:
    return session_dir / f"{session_dir.name}.csv"


def open_csv_writer(path: Path) -> tuple[TextIO, "csv.DictWriter"]:
    file = open(path, "w", newline="")
    writer = csv.DictWriter(file, fieldnames=CSV_FIELDNAMES)
    writer.writeheader()
    return file, writer
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_storage.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add mobbo/storage.py tests/test_storage.py
git commit -m "feat: add mobbo session storage and streaming CSV writer"
```

---

### Task 7: Board core — connect, disconnect, background reader, status

**Files:**
- Create: `mobbo/board.py`
- Create: `tests/fake_serial.py`
- Test: `tests/test_board_connection.py`

**Interfaces:**
- Consumes: `protocol.pop_binary_payload`, `protocol.parse_payload`, `protocol.Sample`, `cop.compute_board_cop`, `cop.compute_combined_cop`, `cop.board_offsets`, `constants.*`, `exceptions.ConnectionError`.
- Produces: `board.EnrichedSample` (dataclass: `time_ms: float`, `forces: list[float]`, `pulse: int`, `cop1: BoardCop`, `cop2: BoardCop`, `combined: BoardCop`, `pct_board1: float`, `pct_board2: float`, `layout: str`), `board.Board` class with `__init__(self, port: str, layout: str = constants.LAYOUT_SIDE_BY_SIDE, baud: int = constants.BAUD_RATE, tare_sample_count: int = constants.TARE_SAMPLE_COUNT)`, `.connect()`, `.disconnect()`, `.status` (str property: `"disconnected" | "connected" | "error"`), `.latest` (property, `EnrichedSample | None`), `.on_sample(callback)`, `.on_error(callback)`. (`.tare()` added in Task 8, `.start_recording()`/`.stop_recording()` added in Task 9 — this task's tests must not call them.)

- [ ] **Step 1: Write the fake serial port test double**

Create `tests/fake_serial.py`:
```python
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
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_board_connection.py`:
```python
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
        assert b.latest.forces == [1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0]
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_board_connection.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mobbo.board'`

- [ ] **Step 4: Write `mobbo/board.py` (core connect/disconnect/read loop only)**

```python
import threading
import time
from dataclasses import dataclass

import serial

from . import constants, protocol
from .cop import BoardCop, board_offsets, compute_board_cop, compute_combined_cop
from .exceptions import ConnectionError as MobboConnectionError


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

    def _close_recording(self) -> None:
        if self._record_file is not None:
            self._record_file.flush()
            self._record_file.close()
        self._record_file = None
        self._record_writer = None
        self._recording = False

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
        raise NotImplementedError  # implemented in Task 9
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_board_connection.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add mobbo/board.py tests/fake_serial.py tests/test_board_connection.py
git commit -m "feat: add mobbo Board core (connect, disconnect, background reader)"
```

---

### Task 8: Board tare()

**Files:**
- Modify: `mobbo/board.py`
- Test: `tests/test_board_tare.py`

**Interfaces:**
- Consumes: `Board._taring`, `Board._tare_buffer`, `Board._tare_offsets`, `Board._tare_done_event`, `Board.tare_sample_count` (all from Task 7).
- Produces: `Board.tare() -> None` (blocking).

- [ ] **Step 1: Write the failing test**

Create `tests/test_board_tare.py`:
```python
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
    # 3 identical packets: forces = [2,2,2,2, 0,0,0,0], pulse=1
    packet = build_packet((0.0, 2.0, 2.0, 2.0, 2.0, 0.0, 0.0, 0.0, 0.0, 1.0))
    fake = FakeSerial(packet * 3)
    monkeypatch.setattr(board_module.serial, "Serial", lambda *a, **k: fake)

    b = board_module.Board(port="COMX", tare_sample_count=3)
    b.connect()
    try:
        assert _wait_until(lambda: b.latest is not None)

        def run_tare():
            b.tare()

        t = threading.Thread(target=run_tare)
        t.start()
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_board_tare.py -v`
Expected: FAIL with `AttributeError: 'Board' object has no attribute 'tare'`

- [ ] **Step 3: Add `tare()` and taring hook to `mobbo/board.py`**

Add this method to the `Board` class (near `connect`/`disconnect`):
```python
    def tare(self) -> None:
        self._tare_buffer = []
        self._tare_done_event.clear()
        self._taring = True
        self._tare_done_event.wait()

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
```

Modify `_handle_sample` — insert taring collection as the first lines (before `forces = [f - o ...]`):
```python
    def _handle_sample(self, sample: protocol.Sample) -> None:
        if self._taring:
            self._tare_buffer.append(sample.forces)
            if len(self._tare_buffer) >= self.tare_sample_count:
                self._finish_taring()

        forces = [f - o for f, o in zip(sample.forces, self._tare_offsets)]
        ...
```
(keep the rest of the existing method body unchanged)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_board_tare.py -v`
Expected: 1 passed

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `uv run pytest -v`
Expected: all previous tests plus this one pass

- [ ] **Step 6: Commit**

```bash
git add mobbo/board.py tests/test_board_tare.py
git commit -m "feat: add mobbo Board.tare() zero-offset calibration"
```

---

### Task 9: Board recording (start_recording / stop_recording, streaming CSV)

**Files:**
- Modify: `mobbo/board.py`
- Test: `tests/test_board_recording.py`

**Interfaces:**
- Consumes: `storage.create_session_dir`, `storage.session_csv_path`, `storage.open_csv_writer`, `storage.CSV_FIELDNAMES`, `exceptions.RecordingError`.
- Produces: `Board.start_recording(subject_id: str) -> Path`, `Board.stop_recording() -> Path`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_board_recording.py`:
```python
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
        assert rows[0]["F1"] == "1.0"
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_board_recording.py -v`
Expected: FAIL with `AttributeError: 'Board' object has no attribute 'start_recording'`

- [ ] **Step 3: Add recording methods to `mobbo/board.py`**

Add the import at the top of the file:
```python
from pathlib import Path

from . import storage
from .exceptions import RecordingError
```
(combine with the existing `from .exceptions import ConnectionError as MobboConnectionError` line into one `from .exceptions import ...` import)

Add these methods to the `Board` class:
```python
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
```

Replace the `_write_record_row` placeholder (currently `raise NotImplementedError`) with:
```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_board_recording.py -v`
Expected: 3 passed

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `uv run pytest -v`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add mobbo/board.py tests/test_board_recording.py
git commit -m "feat: add mobbo Board recording (streaming CSV, no separate save())"
```

---

### Task 10: Public API — list_ports() and __init__.py

**Files:**
- Modify: `mobbo/board.py`
- Modify: `mobbo/__init__.py`
- Test: `tests/test_public_api.py`

**Interfaces:**
- Produces: `mobbo.Board`, `mobbo.EnrichedSample`, `mobbo.BoardCop`, `mobbo.list_ports() -> list[str]`, `mobbo.configure`, `mobbo.get_config`, `mobbo.ConnectionError`, `mobbo.RecordingError`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_public_api.py`:
```python
import mobbo


def test_public_api_exports():
    assert hasattr(mobbo, "Board")
    assert hasattr(mobbo, "EnrichedSample")
    assert hasattr(mobbo, "BoardCop")
    assert hasattr(mobbo, "list_ports")
    assert hasattr(mobbo, "configure")
    assert hasattr(mobbo, "get_config")
    assert hasattr(mobbo, "ConnectionError")
    assert hasattr(mobbo, "RecordingError")


def test_list_ports_returns_a_list(monkeypatch):
    class FakePortInfo:
        def __init__(self, device):
            self.device = device

    monkeypatch.setattr(
        mobbo.board.serial.tools.list_ports,
        "comports",
        lambda: [FakePortInfo("COM3"), FakePortInfo("COM7")],
    )

    assert mobbo.list_ports() == ["COM3", "COM7"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_public_api.py -v`
Expected: FAIL with `AttributeError: module 'mobbo' has no attribute 'Board'`

- [ ] **Step 3: Add `list_ports()` to `mobbo/board.py`**

Add near the top of the file, with the other imports:
```python
import serial.tools.list_ports
```

Add this module-level function (outside the `Board` class, anywhere after the imports):
```python
def list_ports() -> list[str]:
    return [p.device for p in serial.tools.list_ports.comports()]
```

- [ ] **Step 4: Write `mobbo/__init__.py`**

```python
from .board import Board, EnrichedSample, list_ports
from .config import configure, get_config
from .cop import BoardCop
from .exceptions import ConnectionError, RecordingError
from . import board  # exposed for test monkeypatching (mobbo.board.serial...)

__all__ = [
    "Board",
    "EnrichedSample",
    "BoardCop",
    "list_ports",
    "configure",
    "get_config",
    "ConnectionError",
    "RecordingError",
]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_public_api.py -v`
Expected: 2 passed

- [ ] **Step 6: Run the full suite to check for regressions**

Run: `uv run pytest -v`
Expected: all tests pass (protocol, cop, config, storage, board connection/tare/recording, public API)

- [ ] **Step 7: Commit**

```bash
git add mobbo/board.py mobbo/__init__.py tests/test_public_api.py
git commit -m "feat: expose mobbo public API (Board, list_ports, configure, get_config)"
```

---

### Task 11: README usage docs

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: the full public API from Task 10.

- [ ] **Step 1: Write `README.md`**

```markdown
# mobbo

GUI-free Python library for the two-force-board COP (center of pressure)
serial device. Connect over a COM port, get live per-board and combined
COP samples, tare, and record sessions to CSV.

## Install

```bash
uv add mobbo-serial
```

## Usage

```python
import mobbo

# optional: relocate where session data is saved (defaults to
# <Documents>/mobbo-data, config lives at ~/.mobbo/config.json)
mobbo.configure(data_dir="D:/lab-data")

board = mobbo.Board(port="COM10", layout="side_by_side")
board.on_sample(lambda s: print(s.combined.cop_x, s.combined.cop_y))
board.connect()

board.tare()  # blocks briefly, zeroes current load as baseline

session_dir = board.start_recording("subject1")
# ... let it run ...
csv_path = board.stop_recording()
print(f"saved to {csv_path}")

board.disconnect()
```

`mobbo.list_ports()` lists available COM ports.

## Development

```bash
uv sync
uv run pytest -v
```
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add mobbo README with usage example"
```

---

## Self-Review Notes

- **Spec coverage:** every module in the spec's package layout (`constants`, `protocol`, `cop`, `config`, `storage`, `board`, `exceptions`, `__init__`) has a task. `Board`'s API surface (`connect`, `disconnect`, `tare`, `on_sample`, `on_error`, `start_recording`, `stop_recording`, `latest`, `status`) is fully covered across Tasks 7-9. Config bootstrap location, streaming-not-buffered recording, and the `layout`/`config.py` naming fix are all reflected in the code.
- **Deviation from spec, called out explicitly:** the spec's `Board.__init__` signature used `**overrides` for all constants; this plan narrows that to a single explicit `tare_sample_count` parameter (needed for fast tests) and leaves `BOARD_WIDTH_CM`/`BOARD_LENGTH_CM`/`WEIGHT_THRESHOLD_KG` as module-level constants only, since `cop.py`'s pure functions read them directly and per-instance overrides would need to be threaded through every call — unnecessary until a real second physical board size shows up (YAGNI).
- **Type consistency:** `EnrichedSample.layout`, `storage.CSV_FIELDNAMES`'s `"layout"` column, and `Board.layout` all agree. `tare_sample_count` is spelled identically in `Board.__init__` and in the tare test.
