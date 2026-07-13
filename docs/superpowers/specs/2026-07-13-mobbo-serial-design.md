# mobbo — design spec

Date: 2026-07-13
Status: approved, pending implementation plan

## Purpose

Extract the serial-communication, packet-parsing, center-of-pressure (COP) math,
and session-recording logic out of `ref.py` (a one-off PySide6 GUI for a
two-force-board setup) into a standalone, GUI-free Python library called
`mobbo`. Any Python program should be able to `import mobbo`, connect to the
board over a COM port, get live COP samples, and record sessions to disk —
without pulling in Qt or any UI framework.

Distribution name: `mobbo-serial` (matches existing `pyproject.toml`).
Import name: `mobbo`.

## Non-goals

- No GUI, no plotting, no Qt dependency.
- No support for arbitrary/generic serial devices — this library is specific
  to the two-force-board protocol used in `ref.py` (0xFF 0xFF header, 10
  little-endian floats per packet, checksum framing).
- No multiprocessing. `ref.py` used a separate OS process for GUI-responsiveness
  reasons that don't apply to a library; a background thread is enough.

## Package layout

```
mobbo/
  __init__.py    public API surface: Board, list_ports, configure, get_config
  constants.py   protocol + physical constants (baud, header bytes, board
                 dimensions, weight threshold, tare sample count) — used as
                 defaults, overridable via Board(...) kwargs
  protocol.py    wire-format only: pop_binary_payload(), parse_payload(),
                 Sample dataclass (raw fields: time_ms, forces, pulse)
  cop.py         pure math: BoardCop dataclass, compute_board_cop(),
                 compute_combined_cop(), board_offsets(), board_labels()
  config.py      ~/.mobbo/config.json bootstrap, read, write
  storage.py     session directory naming, streaming CSV writer
  board.py       Board class — owns the serial connection, background
                 reader thread, tare state, and recording state; composes
                 protocol.py + cop.py + storage.py
  exceptions.py  ConnectionError, RecordingError (small, explicit error types)
```

Each module has one job and depends only on modules above it in this list
(`board.py` is the only module that ties the others together). `protocol.py`
and `cop.py` have zero I/O — they're pure functions/dataclasses ported
directly from `ref.py`, which makes them trivially unit-testable without any
hardware or filesystem.

## Configuration (`config.py`)

Fixed bootstrap location: `~/.mobbo/config.json` (independent of the
configurable data directory, so there's no chicken-and-egg problem).

Schema:
```json
{
  "data_dir": "C:/Users/<user>/Documents/mobbo-data"
}
```

- On first access, if `~/.mobbo/config.json` doesn't exist, it's created with
  `data_dir` defaulted to `<Documents>/mobbo-data` (resolved via a
  cross-platform "Documents" lookup, falling back to `~/mobbo-data` if
  Documents can't be resolved, e.g. on Linux).
- `mobbo.get_config() -> dict` reads and returns the current config.
- `mobbo.configure(data_dir: str)` updates and persists `data_dir`.
- `storage.py` calls `config.get_config()["data_dir"]` to resolve where new
  sessions get created; nothing else in the library reads the config file
  directly.

## Protocol layer (`protocol.py`)

Ported as-is from `ref.py` (`pop_binary_payload`, `parse_payload`), since
that framing/checksum logic is already correct and unit-tested-in-practice.
`Sample` here stays minimal — just what's on the wire:

```python
@dataclass
class Sample:
    time_ms: float
    forces: list[float]   # 8 raw channel readings, pre-tare
    pulse: int
```

## COP math (`cop.py`)

Ported as-is from `ref.py`: `BoardCop` dataclass, `compute_board_cop`,
`compute_combined_cop`, `board_offsets`, `board_labels`. No changes to the
math — only removing the Qt-specific docstring framing.

## Board class (`board.py`)

The single stateful, public entry point.

```python
class Board:
    def __init__(self, port: str, layout: str = "side_by_side",
                 baud: int = BAUD_RATE, **overrides): ...

    def connect(self) -> None: ...       # blocking open + starts reader thread
    def disconnect(self) -> None: ...
    def tare(self) -> None: ...          # blocking, averages TARE_SAMPLE_COUNT samples

    def on_sample(self, callback: Callable[[EnrichedSample], None]) -> None: ...
    def on_error(self, callback: Callable[[Exception], None]) -> None: ...

    def start_recording(self, subject_id: str) -> Path: ...  # returns session dir
    def stop_recording(self) -> Path: ...                    # returns csv filepath

    @property
    def latest(self) -> EnrichedSample | None: ...
    @property
    def status(self) -> str: ...         # "disconnected" | "connected" | "error"
```

`layout` accepts `"side_by_side"` or `"front_back"` (maps to `ref.py`'s
`CONFIG_SIDE_BY_SIDE` / `CONFIG_FRONT_BACK`).

**Reader thread lifecycle:** `connect()` opens the serial port synchronously
in the caller's thread (so failures raise immediately as
`mobbo.ConnectionError`), then spawns a daemon background thread that loops:
read bytes → `protocol.pop_binary_payload` → `protocol.parse_payload` →
apply tare offsets → `cop.compute_board_cop` ×2 → `cop.compute_combined_cop`
→ build `EnrichedSample` → update `self._latest` under a lock → write a CSV
row if recording → invoke the `on_sample` callback if registered.

`EnrichedSample` (defined in `board.py`, since it's a composition of
protocol + cop outputs, not a wire-format concern):
```python
@dataclass
class EnrichedSample:
    time_ms: float
    forces: list[float]        # post-tare
    pulse: int
    cop1: BoardCop
    cop2: BoardCop
    combined: BoardCop
    pct_board1: float
    pct_board2: float
    layout: str
```

**Tare:** `tare()` sets an internal flag, blocks the calling thread (via a
`threading.Event`) until the reader thread has collected
`TARE_SAMPLE_COUNT` raw samples and averaged them into `self._tare_offsets`,
then returns. Matches `ref.py`'s tare behavior, just synchronous instead of
GUI-message-driven.

**Recording:** `start_recording(subject_id)` calls into `storage.py` to
create `<data_dir>/<subject_id>_<timestamp>/<subject_id>_<timestamp>.csv`,
opens it for writing, writes the header immediately (fieldnames are fixed —
same set `ref.py` used: time_ms, F1-F8, pulse, layout, combined_cop_x/y,
combined_weight, combined_valid, pct_board1, pct_board2), and flips
`self._recording = True`. The reader thread then writes one row per sample
directly to the open file handle — no in-memory buffering, so a crash mid-session
loses at most the last unflushed row, not the whole session.
`stop_recording()` flips the flag off, flushes and closes the file, returns
its path. There is no separate `save()` — recording IS saving.

## Storage layer (`storage.py`)

```python
def create_session_dir(subject_id: str) -> Path: ...
def session_csv_path(session_dir: Path, subject_id: str) -> Path: ...
def open_csv_writer(path: Path, fieldnames: list[str]) -> csv.DictWriter: ...
```

Sanitizes `subject_id` the same way `ref.py` does (alnum + `-`/`_` only,
falls back to `"subject"` if empty after sanitizing).

## Errors (`exceptions.py`)

- `mobbo.ConnectionError` — raised by `Board.connect()` on open failure.
- `mobbo.RecordingError` — raised by `start_recording()`/`stop_recording()`
  on misuse (e.g. starting twice without stopping, stopping when not
  recording) or filesystem failure.

Reader-thread runtime errors (e.g. device unplugged mid-session) don't raise
into the caller's thread (there's no caller thread waiting). Instead: the
thread sets `Board.status = "error"`, closes any open recording file
cleanly, invokes the `on_error` callback if one is registered, and exits.

## Testing

- `protocol.py`, `cop.py`: pure unit tests, synthetic byte streams and known
  force values, no hardware or filesystem needed.
- `config.py`, `storage.py`: unit tests against a temp directory
  (monkeypatch the home/data dir).
- `board.py`: integration-style tests with `serial.Serial` monkeypatched to
  a fake serial object that yields canned byte chunks, verifying the full
  connect → sample → record → stop_recording flow and that the written CSV
  matches expectations.

## Open items for the implementation plan

None — this spec is self-contained. The implementation plan should sequence
module creation bottom-up (`constants` → `protocol`/`cop` → `config`/`storage`
→ `board` → `__init__`) with tests alongside each module.
