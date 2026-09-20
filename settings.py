"""Armazenamento de preferências do usuário em JSON."""
import json
import os
from pathlib import Path

SETTINGS_PATH = Path(os.environ.get("APPDATA", Path.home())) / "PDF Tools" / "settings.json"

_data = None

def _load():
    global _data
    if _data is None:
        try:
            _data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if not isinstance(_data, dict):
                _data = {}
        except (OSError, ValueError):
            _data = {}
    return _data

def get(key, default=None):
    return _load().get(key, default)

def set(key, value):
    _load()[key] = value
    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(_data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(SETTINGS_PATH)  # escrita atômica
    except OSError:
        pass
