import json

from mobbo import config


def test_get_config_creates_default_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_home_dir", lambda: tmp_path)
    (tmp_path / "Desktop").mkdir()

    result = config.get_config()

    config_path = tmp_path / ".mobbo" / "config.json"
    assert config_path.exists()
    assert result["data_dir"] == str(tmp_path / "Desktop" / "mobo_Data")


def test_get_config_falls_back_to_home_without_desktop(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_home_dir", lambda: tmp_path)
    # no Desktop dir created

    result = config.get_config()

    assert result["data_dir"] == str(tmp_path / "mobo_Data")


def test_get_config_migrates_old_default_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_home_dir", lambda: tmp_path)
    (tmp_path / "Desktop").mkdir()
    mobbo_dir = tmp_path / ".mobbo"
    mobbo_dir.mkdir()
    old_default = tmp_path / "Documents" / "mobbo-data"
    (mobbo_dir / "config.json").write_text(json.dumps({"data_dir": str(old_default)}))

    result = config.get_config()

    assert result["data_dir"] == str(tmp_path / "Desktop" / "mobo_Data")
    assert (tmp_path / "Desktop" / "mobo_Data").exists()


def test_get_config_reads_existing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_home_dir", lambda: tmp_path)
    mobbo_dir = tmp_path / ".mobbo"
    mobbo_dir.mkdir()
    (mobbo_dir / "config.json").write_text(json.dumps({"data_dir": "D:/custom"}))

    result = config.get_config()

    assert result["data_dir"] == "D:/custom"


def test_configure_persists_new_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_home_dir", lambda: tmp_path)

    result = config.configure(data_dir="D:/lab-data")

    assert result["data_dir"] == "D:/lab-data"
    reloaded = json.loads((tmp_path / ".mobbo" / "config.json").read_text())
    assert reloaded["data_dir"] == "D:/lab-data"
