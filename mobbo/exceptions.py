class ConnectionError(Exception):
    """Raised by Board.connect() when the serial port can't be opened."""


class RecordingError(Exception):
    """Raised on invalid start_recording()/stop_recording() usage."""
