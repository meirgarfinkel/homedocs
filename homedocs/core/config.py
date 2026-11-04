"""Settings you might want to tune, and where HomeDocs keeps its data."""

import os
import sys
from pathlib import Path

DB_NAME = "homedocs.db"

# The kinds of file read, by extension
FILE_TYPES = {".pdf": "PDF", ".docx": "Word", ".md": "Markdown", ".markdown": "Markdown", ".txt": "Text"}
MAX_FILE_MB = 100  # bigger files are listed as skipped, not read

# Passages: counted in words, not a model's tokens, so changing models never means splitting again.
# 300 words is about 400 tokens of English, well inside the 512 the embedding models take.
PASSAGE_WORDS = 300
OVERLAP_WORDS = 50  # the end of each passage starts the next, so a sentence near the cut is in both
MIN_SECTION_WORDS = 50  # a heading starts a new passage once the current one has at least this many
MAX_PASSAGE_CHARS = 3000  # text without spaces (a long URL, a table dump) is cut at this length

RESCAN_GAP = 60  # seconds: coming back to the window looks for changed files at most this often


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
