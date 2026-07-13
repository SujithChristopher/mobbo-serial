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
