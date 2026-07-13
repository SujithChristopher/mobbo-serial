from .board import Board, EnrichedSample, list_ports
from .config import configure, get_config
from .cop import BoardCop
from .exceptions import ConnectionError, RecordingError
from . import board  # exposed for test monkeypatching (mobbo.board.serial...)

__all__ = [
    "Board",
    "EnrichedSample",
    "BoardCop",
    "list_ports",
    "configure",
    "get_config",
    "ConnectionError",
    "RecordingError",
]
