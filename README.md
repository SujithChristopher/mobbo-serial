# mobbo

GUI-free Python library for the two-force-board COP (center of pressure)
serial device. Connect over a COM port, get live per-board and combined
COP samples, tare, and record sessions to CSV.

## Install

```bash
pip install mobbo-serial
# or: uv add mobbo-serial
```

`sample_gui.py` (a PySide6 demo app built on this library) needs the optional
`gui` extra:

```bash
pip install mobbo-serial[gui]
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
uv sync --extra gui  # --extra gui only needed to run sample_gui.py
uv run pytest -v
```

## Releasing

Publishing to PyPI is automated: bump `version` in `pyproject.toml`, commit,
tag it (`git tag vX.Y.Z && git push origin vX.Y.Z`), then publish a
[GitHub Release](https://github.com/SujithChristopher/mobbo-serial/releases)
for that tag - the `publish.yml` workflow builds and uploads it via PyPI
Trusted Publishing.
