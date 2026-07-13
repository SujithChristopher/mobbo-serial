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
