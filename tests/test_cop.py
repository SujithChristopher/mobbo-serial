import pytest

from mobbo import constants
from mobbo.cop import (
    BoardCop,
    board_labels,
    board_offsets,
    compute_board_cop,
    compute_board_cop_with_correction,
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


def test_compute_board_cop_with_correction_subtracts_slope_and_adds_offset():
    forces = [2.0, 0.0, 0.0, 0.0]
    result = compute_board_cop_with_correction(forces, 0, "board_1")
    assert result.valid is True
    assert result.total_force == 2.0
    assert result.cop_x == pytest.approx(28.75 - (28.75 * 0.05722694168934991) + -0.10578463619500285)
    assert result.cop_y == pytest.approx(21.25 - (21.25 * 0.08899981213628787) + -0.2805844185583479)


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
