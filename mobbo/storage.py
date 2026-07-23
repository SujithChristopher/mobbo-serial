import csv
from datetime import datetime
from pathlib import Path
from typing import TextIO

from . import config

CSV_FIELDNAMES = [
    "time(micros)", "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8",
    "layout",
    "board1_cop_x", "board1_cop_y", "board1_weight",
    "board2_cop_x", "board2_cop_y", "board2_weight",
    "combined_cop_x", "combined_cop_y", "combined_weight",
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
