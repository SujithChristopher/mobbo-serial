import json
from pathlib import Path


def _home_dir() -> Path:
    return Path.home()


def _config_path() -> Path:
    return _home_dir() / ".mobbo" / "config.json"


def _default_data_dir() -> Path:
    desktop = _home_dir() / "Desktop"
    base = desktop if desktop.is_dir() else _home_dir()
    return base / "mobo_Data"


def _legacy_default_data_dirs() -> set[Path]:
    home = _home_dir()
    return {
        home / "mobbo-data",
        home / "Documents" / "mobbo-data",
    }


def _ensure_data_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def get_config() -> dict:
    path = _config_path()
    default_data_dir = _default_data_dir()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        _ensure_data_dir(default_data_dir)
        default = {"data_dir": str(default_data_dir)}
        path.write_text(json.dumps(default, indent=2))
        return default

    current = json.loads(path.read_text())
    if not current.get("data_dir"):
        current["data_dir"] = str(default_data_dir)
        path.write_text(json.dumps(current, indent=2))
        _ensure_data_dir(default_data_dir)
        return current

    data_dir = Path(current["data_dir"])
    if data_dir in _legacy_default_data_dirs():
        current["data_dir"] = str(default_data_dir)
        path.write_text(json.dumps(current, indent=2))
        _ensure_data_dir(default_data_dir)
        return current

    if data_dir == default_data_dir:
        _ensure_data_dir(data_dir)
    return current


def configure(data_dir: str) -> dict:
    current = get_config()
    current["data_dir"] = data_dir
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, indent=2))
    _ensure_data_dir(Path(data_dir))
    return current
