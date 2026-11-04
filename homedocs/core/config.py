"""Settings you might want to tune, and where HomeDocs keeps its data."""

import os
import sys
from pathlib import Path

DB_NAME = "homedocs.db"


def data_dir() -> Path:
    """Where the database, models and settings live."""
    if os.environ.get("HOMEDOCS_HOME"):
        base = Path(os.environ["HOMEDOCS_HOME"]).expanduser()
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "homedocs"
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "homedocs"
    else:
        xdg = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
        base = Path(xdg) / "homedocs"
    base.mkdir(parents=True, exist_ok=True)
    return base


def db_file() -> Path:
    return data_dir() / DB_NAME
