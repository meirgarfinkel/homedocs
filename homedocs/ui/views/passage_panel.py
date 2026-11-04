"""The Info panel's passage view: a result in full, with its file, its place in the file, the question's words
highlighted, and buttons to open the file or show it in its folder."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from ...core.search import Result
from ..desktop import open_file, show_in_folder
from .ask import marked


class PassagePanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("infoBody")
        self.setMinimumWidth(300)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 16)
        lay.setSpacing(8)
        self.hint = QLabel("Click a passage on the Ask page to read it here.")
        self.hint.setObjectName("hint")
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)
        self.title = QLabel()
        self.title.setObjectName("panelTitle")
        self.title.setWordWrap(True)
        self.where = QLabel()
        self.where.setObjectName("hint")
        self.where.setWordWrap(True)
        self.path = QLabel()
        self.path.setObjectName("panelPath")
        self.path.setWordWrap(True)
        self.path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        buttons = QHBoxLayout()
        self.open_button = QPushButton("Open file")
        self.open_button.setDefault(True)
        self.open_button.clicked.connect(lambda: self.result and open_file(self.result.path))
        self.folder_button = QPushButton("Show in folder")
        self.folder_button.clicked.connect(lambda: self.result and show_in_folder(self.result.path))
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.folder_button)
        buttons.addStretch()
        self.text = QLabel()
        self.text.setObjectName("passageText")
        self.text.setWordWrap(True)
        self.text.setTextFormat(Qt.TextFormat.RichText)
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.text.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        scroll = QScrollArea()
        scroll.setObjectName("info")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.text)
        self.details = [self.title, self.where, self.path, scroll]
        for w in (self.title, self.where, self.path):
            lay.addWidget(w)
        lay.addLayout(buttons)
        lay.addWidget(scroll, 1)
        self.buttons = [self.open_button, self.folder_button]
        self.result = None
        self.show_result(None)

    def show_result(self, r: Result | None):
        self.result = r
        self.hint.setVisible(r is None)
        for w in self.details + self.buttons:
            w.setVisible(r is not None)
        if r is None:
            return
        self.title.setText(r.name)
        self.where.setText(r.where() + ("  ·  Offline: its folder can't be reached" if r.offline else ""))
        self.path.setText(r.path)
        self.text.setText(marked(r.text, r.spans, paragraphs=True))
        self.open_button.setEnabled(not r.offline)
        self.folder_button.setEnabled(not r.offline)
