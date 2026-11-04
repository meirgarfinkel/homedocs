"""Background indexing for the app: when it runs, and its progress in the status pill."""

import os
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QToolButton, QWidget

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
        """A pass, unless one started in the last RESCAN_GAP seconds (for coming back to the window)."""
        if folders() and time.time() - self._started > RESCAN_GAP:
            self.start()

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


def describe(s: dict) -> str:
    """What the indexer is doing, in a few words for the status pill."""
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


class IndexStatus(QWidget):
    """What indexing is doing, how far it got, and a button to stop it (in the status pill)."""

    def __init__(self, watcher: IndexWatcher, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.label = QLabel()
        self.label.setMaximumWidth(420)
        self.bar = QProgressBar()
        self.bar.setFixedSize(72, 4)
        self.bar.setTextVisible(False)
        self.stop = QToolButton()
        self.stop.setText("Stop")
        self.stop.setToolTip("Stop for now; it carries on where it left off next time")
        self.stop.clicked.connect(watcher.stop)
        for w in (self.label, self.bar, self.stop):
            lay.addWidget(w)
        watcher.state.connect(self.show_state)
        self.show_state({"running": False, "phase": "idle"})

    def show_state(self, s: dict):
        text = describe(s)
        room = self.label.maximumWidth()
        self.label.setText(self.label.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, room))
        self.label.setToolTip(text)
        self.label.setVisible(bool(text))
        busy = bool(s["running"])
        self.bar.setVisible(busy)
        self.stop.setVisible(busy)
        if busy:
            if s["phase"] in ("reading", "embedding") and s.get("todo"):
                self.bar.setRange(0, s["todo"])
                self.bar.setValue(s["done"])
            else:
                self.bar.setRange(0, 0)  # busy, amount unknown
