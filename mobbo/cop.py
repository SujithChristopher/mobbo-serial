import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

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


def _normalize_board_name(board_name: str | int | None) -> str | None:
    if board_name is None:
        return None

    normalized = str(board_name).strip().lower()
    if normalized in {"board_1", "board1", "cop1", "b1", "1"}:
        return "board_1"
    if normalized in {"board_2", "board2", "cop2", "b2", "2"}:
        return "board_2"
    return normalized or None


@lru_cache(maxsize=1)
def _load_correction_table() -> dict[str, tuple[tuple[float, float], tuple[float, float]]]:
    candidates = [
        Path.cwd() / "board_slope_offset_correction.csv",
        Path(__file__).resolve().parent.parent / "board_slope_offset_correction.csv",
        Path(__file__).resolve().parent / "board_slope_offset_correction.csv",
    ]

    for path in candidates:
        if not path.exists():
            continue

        corrections: dict[str, tuple[tuple[float, float], tuple[float, float]]] = {}
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                board = (row.get("board") or "").strip().lower()
                axis = (row.get("axis") or "").strip().lower()
                if not board or not axis:
                    continue
                try:
                    slope = float(row["slope"])
                    offset = float(row["offset"])
                except (KeyError, TypeError, ValueError):
                    continue

                values = corrections.setdefault(board, ((0.0, 0.0), (0.0, 0.0)))
                if axis == "x":
                    corrections[board] = ((slope, offset), values[1])
                elif axis == "y":
                    corrections[board] = (values[0], (slope, offset))

        if corrections:
            return corrections

    return {}


def compute_board_cop_with_correction(
    forces: list[float],
    start_index: int,
    board_name: str | int | None,
) -> BoardCop:
    base_cop = compute_board_cop(forces, start_index)
    if not base_cop.valid:
        return base_cop

    board_key = _normalize_board_name(board_name)
    if board_key is None:
        return base_cop

    corrections = _load_correction_table().get(board_key)
    if corrections is None:
        return base_cop

    (slope_x, intercept_x), (slope_y, intercept_y) = corrections
    corrected_x = base_cop.cop_x - (base_cop.cop_x * slope_x) + intercept_x
    corrected_y = base_cop.cop_y - (base_cop.cop_y * slope_y) + intercept_y
    return BoardCop(corrected_x, corrected_y, base_cop.total_force, base_cop.valid)


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
