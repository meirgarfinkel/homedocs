"""Small widgets the pages share."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QSizePolicy


class ElidedLabel(QLabel):
    """One line that ends in "..." when there's no room, instead of widening the window."""

    def __init__(self, text: str = "", parent=None, mode=Qt.TextElideMode.ElideMiddle):
        super().__init__(parent)
        self.full, self.mode = text, mode
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(80)

    def text(self) -> str:  # (the whole of it)
        return self.full

    def resizeEvent(self, e):  # noqa: N802 (Qt's name)
        super().setText(self.fontMetrics().elidedText(self.full, self.mode, self.width()))
        super().resizeEvent(e)

    def setText(self, text: str):  # noqa: N802 (Qt's name)
        self.full = text
        self.setToolTip(text)
        super().setText(self.fontMetrics().elidedText(text, self.mode, max(self.width(), 80)))
