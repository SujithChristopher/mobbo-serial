import mobbo


def test_public_api_exports():
    assert hasattr(mobbo, "Board")
    assert hasattr(mobbo, "EnrichedSample")
    assert hasattr(mobbo, "BoardCop")
    assert hasattr(mobbo, "list_ports")
    assert hasattr(mobbo, "configure")
    assert hasattr(mobbo, "get_config")
    assert hasattr(mobbo, "ConnectionError")
    assert hasattr(mobbo, "RecordingError")


def test_list_ports_returns_a_list(monkeypatch):
    class FakePortInfo:
        def __init__(self, device):
            self.device = device

    monkeypatch.setattr(
        mobbo.board.serial.tools.list_ports,
        "comports",
        lambda: [FakePortInfo("COM3"), FakePortInfo("COM7")],
    )

    assert mobbo.list_ports() == ["COM3", "COM7"]
