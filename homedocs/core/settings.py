"""The user's settings (which folders to search, the theme), kept in settings.json in the data folder."""

import json
from pathlib import Path

from .config import data_dir


def settings_path() -> Path:
    return data_dir() / "settings.json"


def load_settings() -> dict:
    try:
        s = json.loads(settings_path().read_text(encoding="utf-8"))
        return s if isinstance(s, dict) else {"folders": []}
    except Exception:
        return {"folders": []}


def save_settings(s: dict):
    # Written beside the real file and swapped in, so a crash never leaves half a file
    tmp = settings_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=2), encoding="utf-8")
    tmp.replace(settings_path())


def folders() -> list[str]:
    return list(load_settings().get("folders", []))
