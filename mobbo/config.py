import json
from pathlib import Path


def _home_dir() -> Path:
    return Path.home()


def _config_path() -> Path:
    return _home_dir() / ".mobbo" / "config.json"


def _default_data_dir() -> Path:
    documents = _home_dir() / "Documents"
    base = documents if documents.is_dir() else _home_dir()
    return base / "mobbo-data"


def get_config() -> dict:
    path = _config_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        default = {"data_dir": str(_default_data_dir())}
        path.write_text(json.dumps(default, indent=2))
        return default
    return json.loads(path.read_text())


def configure(data_dir: str) -> dict:
    current = get_config()
    current["data_dir"] = data_dir
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, indent=2))
    return current
