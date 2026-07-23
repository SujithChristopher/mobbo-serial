# MoBBo Serial Library Usage

`mobbo-serial` is a Python library for reading a two-board MoBBo force platform over a serial COM port. It handles serial packet reading, tare, per-board COP, combined COP, and CSV recording without requiring the GUI.

## Installation

```bash
pip install mobbo-serial
```

For local development from this repository:

```bash
pip install -e .
```

The demo GUI requires the optional GUI dependencies:

```bash
pip install mobbo-serial[gui]
```

## Quick Start

```python
import time
import mobbo

board = mobbo.Board(port="COM10", layout="side_by_side")


def handle_sample(sample: mobbo.EnrichedSample):
    print(
        sample.time_ms,
        sample.cop1.cop_x,
        sample.cop1.cop_y,
        sample.cop2.cop_x,
        sample.cop2.cop_y,
        sample.combined.cop_x,
        sample.combined.cop_y,
    )


board.on_sample(handle_sample)
board.connect()

try:
    board.tare()
    time.sleep(10)
finally:
    board.disconnect()
```

## Listing Serial Ports

Use `mobbo.list_ports()` to see the COM ports detected by `pyserial`:

```python
import mobbo

print(mobbo.list_ports())
```

On Windows, ports are usually named like `COM3` or `COM10`.

## Creating a Board

```python
board = mobbo.Board(
    port="COM10",
    layout="side_by_side",
    baud=921600,
    tare_sample_count=100,
)
```

Parameters:

- `port`: Serial port name, for example `COM10`.
- `layout`: Board arrangement. Use `"side_by_side"` or `"front_back"`.
- `baud`: Serial baud rate. The default is `921600`.
- `tare_sample_count`: Number of samples averaged during `tare()`. The default is `100`.

## Connecting and Disconnecting

```python
import mobbo

board = mobbo.Board(port="COM10")

try:
    board.connect()
    print(board.status)  # "connected"
finally:
    board.disconnect()
```

`connect()` starts a background reader thread. Always call `disconnect()` when finished so the COM port and any open CSV file are closed cleanly.

Connection failures raise `mobbo.ConnectionError`:

```python
try:
    board.connect()
except mobbo.ConnectionError as exc:
    print(f"Could not connect: {exc}")
```

## Receiving Live Samples

Register a callback with `on_sample()` before or after connecting:

```python
def on_sample(sample):
    print(sample.combined.cop_x, sample.combined.cop_y)

board.on_sample(on_sample)
```

The callback receives an `EnrichedSample` object.

Fields on `EnrichedSample`:

- `time_ms`: Device timestamp. Current CSV export names this column `time(micros)`.
- `forces`: Tare-corrected force values `[f1, f2, f3, f4, f5, f6, f7, f8]`.
- `cop1`: COP result for board 1.
- `cop2`: COP result for board 2.
- `combined`: Combined COP result across both boards.
- `pct_board1`: Board 1 load percentage, from `0.0` to `100.0`.
- `pct_board2`: Board 2 load percentage, from `0.0` to `100.0`.
- `layout`: The layout string used by the board.

Fields on each `BoardCop`:

- `cop_x`: COP x position in centimeters.
- `cop_y`: COP y position in centimeters.
- `total_force`: Total force/weight for that board or combined result.
- `valid`: Whether the combined COP result is valid. Per-board low-weight COP values are kept at `(0, 0)`.

## Taring

Call `tare()` after connecting and once the boards are in the desired zero-load state:

```python
board.connect()
board.tare()
```

`tare()` blocks until `tare_sample_count` samples have been collected. The average of those samples is subtracted from future force values.

## Recording CSV Files

Start and stop recording with `start_recording()` and `stop_recording()`:

```python
session_dir = board.start_recording("subject1")
print(f"Recording in {session_dir}")

# collect data...

csv_path = board.stop_recording()
print(f"Saved CSV: {csv_path}")
```

Recording rules:

- `start_recording(subject_id)` creates a new timestamped session folder and returns that folder path.
- `stop_recording()` closes the CSV and returns the CSV file path.
- Calling `start_recording()` while already recording raises `mobbo.RecordingError`.
- Calling `stop_recording()` when not recording raises `mobbo.RecordingError`.
- `disconnect()` automatically closes an active recording.

Subject IDs are sanitized for filenames. Only letters, numbers, `-`, and `_` are kept. Empty IDs become `subject`.

## CSV Output

CSV files are saved as:

```text
<data_dir>/<subject>_<YYYYMMDD_HHMMSS>/<subject>_<YYYYMMDD_HHMMSS>.csv
```

Current CSV columns:

```text
time(micros), f1, f2, f3, f4, f5, f6, f7, f8,
layout,
board1_cop_x, board1_cop_y, board1_weight,
board2_cop_x, board2_cop_y, board2_weight,
combined_cop_x, combined_cop_y, combined_weight
```

The force columns are tare-corrected values. The COP columns are in centimeters.

## Data Directory Configuration

By default, data is stored in:

```text
<Desktop>/mobo_Data
```

If the desktop folder is not available, the user home folder is used instead.

You can change the output folder:

```python
import mobbo

mobbo.configure(data_dir="D:/MoBBoData")
print(mobbo.get_config())
```

The config file is stored at:

```text
~/.mobbo/config.json
```

## Layouts

Use one of these layout strings:

```python
mobbo.Board(port="COM10", layout="side_by_side")
mobbo.Board(port="COM10", layout="front_back")
```

Layout behavior:

- `side_by_side`: Board 1 is treated as the right-foot board and board 2 as the left-foot board.
- `front_back`: Board 1 is treated as the front board and board 2 as the back board.

The combined COP calculation applies the board offsets based on this layout.

## COP Threshold Behavior

Per-board COP uses a simple low-weight rule:

- If board 1 total weight is greater than `1.0 kg`, board 1 COP is computed.
- If board 1 total weight is `1.0 kg` or lower, board 1 COP is reported as `(0, 0)`.
- Board 2 follows the same rule using board 2 total weight.

Combined COP uses the total board weights and layout offsets.

## Error Callback

Use `on_error()` to handle background serial-reader errors:

```python
def on_error(exc):
    print(f"Serial error: {exc}")

board.on_error(on_error)
```

If an error occurs while recording, the library closes the recording file before calling the error callback.

## Complete Recording Example

```python
import time
import mobbo

mobbo.configure(data_dir="D:/MoBBoData")

board = mobbo.Board(port="COM10", layout="side_by_side")


def on_sample(sample):
    print(
        f"W1={sample.cop1.total_force:.2f} "
        f"W2={sample.cop2.total_force:.2f} "
        f"COP=({sample.combined.cop_x:.2f}, {sample.combined.cop_y:.2f})"
    )


def on_error(exc):
    print(f"Error: {exc}")


board.on_sample(on_sample)
board.on_error(on_error)

try:
    board.connect()
    board.tare()

    session_dir = board.start_recording("subject1")
    print(f"Recording to {session_dir}")

    time.sleep(30)

    csv_path = board.stop_recording()
    print(f"Saved {csv_path}")
finally:
    board.disconnect()
```

## Public API Summary

Common imports:

```python
from mobbo import Board, EnrichedSample, BoardCop
from mobbo import list_ports, configure, get_config
from mobbo import ConnectionError, RecordingError
```

Main methods:

- `Board.connect()`
- `Board.disconnect()`
- `Board.tare()`
- `Board.start_recording(subject_id)`
- `Board.stop_recording()`
- `Board.on_sample(callback)`
- `Board.on_error(callback)`
- `Board.latest`
- `mobbo.list_ports()`
- `mobbo.configure(data_dir=...)`
- `mobbo.get_config()`