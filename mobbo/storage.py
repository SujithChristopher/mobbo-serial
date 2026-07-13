import csv
from datetime import datetime
from pathlib import Path
from typing import TextIO

from . import config

CSV_FIELDNAMES = [
    "time_ms", "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "pulse",
    "layout",
    "board1_cop_x", "board1_cop_y", "board1_weight", "board1_valid",
    "board2_cop_x", "board2_cop_y", "board2_weight", "board2_valid",
    "combined_cop_x", "combined_cop_y", "combined_weight", "combined_valid",
    "pct_board1", "pct_board2",
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
