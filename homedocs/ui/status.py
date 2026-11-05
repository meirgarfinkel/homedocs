"""The status line at the bottom of the sidebar: one short line (what indexing is doing, or a message like
"Copied"), a thin progress bar under it, and a ✕ to stop indexing. The full wording is on hover. It's never
over the page. Messages still go through the window's QStatusBar (which stays hidden): this shows them."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QToolButton, QVBoxLayout, QWidget

from . import theme
from .indexing import brief, describe


class StatusLine(QWidget):
    """changed(str): the short line now shown ("" when there's nothing to say)."""

    changed = Signal(str)

    def __init__(self, watcher, status_bar, parent=None):
        super().__init__(parent)
        self.setObjectName("statusLine")
        self.status_bar = status_bar
        self._state = {"running": False, "phase": "idle"}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 6, 10, 12)
        lay.setSpacing(5)
        row = QHBoxLayout()
        row.setSpacing(4)
        self.label = QLabel()
        self.stop = QToolButton()
        self.stop.setObjectName("statusStop")
        self.stop.setToolTip("Stop indexing for now; it carries on where it left off next time")
        self.stop.setAccessibleName("Stop indexing")
        self.stop.clicked.connect(watcher.stop)
        row.addWidget(self.label, 1)
        row.addWidget(self.stop)
        lay.addLayout(row)
        self.bar = QProgressBar()
        self.bar.setFixedHeight(3)
        self.bar.setTextVisible(False)
        lay.addWidget(self.bar)
        self.text = ""
        watcher.state.connect(self.show_state)
        status_bar.hide()
        status_bar.messageChanged.connect(lambda _: self.refresh())
        theme.signals.changed.connect(self._restyle)
        self._restyle()
        self.refresh()

    def _restyle(self):
        self.stop.setIcon(theme.icon("clear", "muted"))

    def show_state(self, s: dict):
        self._state = s
        busy = bool(s["running"])
        if busy and s["phase"] in ("reading", "embedding") and s.get("todo"):
            self.bar.setRange(0, s["todo"])
            self.bar.setValue(s["done"])
        elif busy:
            self.bar.setRange(0, 0)  # busy, amount unknown
        self.refresh()

    def refresh(self):
        message = self.status_bar.currentMessage()
        busy = bool(self._state["running"])
        short, full = (message, message) if message else (brief(self._state), describe(self._state))
        room = max(60, self.label.width() or 130)
        self.label.setText(self.label.fontMetrics().elidedText(short, Qt.TextElideMode.ElideRight, room))
        self.label.setToolTip(full)
        self.bar.setVisible(busy)
        self.stop.setVisible(busy)
        self.setVisible(bool(short) or busy)
        if short != self.text:
            self.text = short
            self.changed.emit(short)

    def resizeEvent(self, e):  # noqa: N802 (Qt's name)
        super().resizeEvent(e)
        self.refresh()
