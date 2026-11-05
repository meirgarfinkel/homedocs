"""Background indexing for the app: when it runs, and what it's doing in words (for the status line)."""

import os
import time

from PySide6.QtCore import QObject, QTimer, Signal

from ..core.config import RESCAN_GAP
from ..core.indexer import Indexer
from ..core.settings import folders

REFRESH_EVERY = 5  # seconds: while files are being read, the Library page catches up this often


class IndexWatcher(QObject):
    """Owns the indexer and tells the window what it's doing.

    state(dict): the indexer's snapshot, twice a second while it runs (and once when it stops).
    changed(): files were read, moved or removed: time to reload what's shown.
    """

    state = Signal(dict)
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.indexer = Indexer()
        self._closed = False
        self._changes = 0
        self._was_running = False
        self._refreshed = 0.0
        self._shown_done = 0
        self._started = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(500)

    def start(self):
        """A pass now (or, if one is running, start it over: e.g. a folder was added)."""
        if not self._closed:
            self._started = time.time()
            self.indexer.start()
            self._tick()

    def start_if_due(self):
        """A pass, unless one started in the last RESCAN_GAP seconds (for coming back to the window). One
        that's running carries on, and another follows it."""
        if folders() and time.time() - self._started > RESCAN_GAP and not self._closed:
            self._started = time.time()
            self.indexer.start(restart=False)
            self._tick()

    def stop(self):
        self.indexer.stop()

    def shutdown(self, wait: float = 3.0):
        """Stop for good, waiting a little for the current file to finish (the index is safe either way)."""
        self._closed = True
        self._timer.stop()
        self.indexer.stop_and_wait(wait)

    def running(self) -> bool:
        t = self.indexer.thread
        return t is not None and t.is_alive()

    def _tick(self):
        s = self.indexer.snapshot()
        running = s["running"] or self.running()
        if running or self._was_running:
            self.state.emit(dict(s, running=running))  # (the thread may be starting, or just finishing)
        # files show up in the Library while a long pass is still going, not only at its end
        if running and s["done"] > self._shown_done and time.time() - self._refreshed > REFRESH_EVERY:
            self._shown_done = s["done"]
            self._refresh()
        if s["changes"] != self._changes:
            self._changes = s["changes"]
            self._refresh()
        if not running:
            self._shown_done = 0
        self._was_running = running

    def _refresh(self):
        self._refreshed = time.time()
        self.changed.emit()


def brief(s: dict) -> str:
    """What the indexer is doing, in two or three words (the status line; describe() is the full wording)."""
    if s.get("error"):
        return "Indexing stopped"
    if s["phase"] == "elsewhere":
        return "Indexing in another window"
    if not s["running"]:
        return ""
    if s["phase"] == "embedding" and s["todo"]:
        return f"Finishing up · {100 * s['done'] // s['todo']}%"
    if s["phase"] == "reading" and s["todo"]:
        return f"Indexing {s['done']:,} of {s['todo']:,}"
    return "Checking for changes"


def describe(s: dict) -> str:
    """What the indexer is doing, in full (on hover over the status line)."""
    if s.get("error"):
        return s["error"]
    if s["phase"] == "elsewhere":
        return "Another HomeDocs window is reading your files."
    if not s["running"]:
        return ""
    if s["phase"] == "embedding" and s["todo"]:
        return f"Preparing search by meaning: {s['done']:,} of {s['todo']:,} passages"
    if s["phase"] == "reading" and s["todo"]:
        name = os.path.basename(s["file"]) if s.get("file") else ""
        return f"Reading files: {s['done']:,} of {s['todo']:,}" + (f" ({name})" if name else "")
    return "Looking for new and changed files..."
