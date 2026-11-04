"""The sidebar: the app's pages (Ask, Library), then the recent questions, each reopened with its answer
without asking the model again."""

import shiboken6
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHeaderView, QTreeWidget, QTreeWidgetItem

ASK = "ask"
LIBRARY = "library"
PAGES = {ASK: "Ask", LIBRARY: "Library"}

PLACE_ROLE = Qt.ItemDataRole.UserRole


QUESTION_ROLE = Qt.ItemDataRole.UserRole + 1


class Sidebar(QTreeWidget):
    chosen = Signal(str)  # ASK or LIBRARY
    reopen = Signal(int)  # a recent question's id

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setColumnCount(2)  # the name, and a muted count on the right
        self.setFrameShape(QTreeWidget.Shape.NoFrame)
        self.setHeaderHidden(True)
        self.header().setStretchLastSection(False)
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setMinimumWidth(220)
        self.currentItemChanged.connect(self._embolden)
        self._quiet = False
        for place, name in PAGES.items():
            item = QTreeWidgetItem([name, ""])
            item.setData(0, PLACE_ROLE, place)
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.addTopLevelItem(item)
        self._recent: list[QTreeWidgetItem] = []
        self.itemSelectionChanged.connect(self._on_pick)
        self.select(ASK)

    def set_recent(self, questions: list[tuple[int, str]]):
        """The recent questions, newest first: (id, question)."""
        for item in self._recent:
            self.takeTopLevelItem(self.indexOfTopLevelItem(item))
        self._recent = []
        if not questions:
            return
        head = QTreeWidgetItem(["Recent", ""])
        head.setFlags(Qt.ItemFlag.ItemIsEnabled)  # (a heading: not selectable)
        f = QFont(head.font(0))
        f.setWeight(QFont.Weight.Bold)
        f.setPointSizeF(f.pointSizeF() * 0.85)
        head.setFont(0, f)
        head.setForeground(0, self.palette().placeholderText())
        self._recent.append(head)
        for qid, question in questions:
            item = QTreeWidgetItem([question, ""])
            item.setData(0, QUESTION_ROLE, qid)
            item.setToolTip(0, question)
            self._recent.append(item)
        self.addTopLevelItems(self._recent)

    @staticmethod
    def _embolden(now, before):
        """The chosen page in bold."""
        for item, weight in ((before, QFont.Weight.Normal), (now, QFont.Weight.Bold)):
            if item is not None and shiboken6.isValid(item) and item.flags() & Qt.ItemFlag.ItemIsSelectable:
                f = QFont(item.font(0))
                f.setWeight(weight)
                item.setFont(0, f)

    def current_place(self):
        items = self.selectedItems()
        return items[0].data(0, PLACE_ROLE) if items else None

    def select(self, place: str):
        """Choose a page without saying so (no chosen signal)."""
        was, self._quiet = self._quiet, True
        for i in range(self.topLevelItemCount()):
            if self.topLevelItem(i).data(0, PLACE_ROLE) == place:
                self.setCurrentItem(self.topLevelItem(i))
        self._quiet = was

    def _on_pick(self):
        items = self.selectedItems()
        if not items or self._quiet:
            return
        qid = items[0].data(0, QUESTION_ROLE)
        if qid is not None:
            self.reopen.emit(qid)
        elif items[0].data(0, PLACE_ROLE):
            self.chosen.emit(items[0].data(0, PLACE_ROLE))

    def select_question(self, qid: int | None):
        """Show a recent question as chosen (or, None, none of them), without saying so."""
        was, self._quiet = self._quiet, True
        for item in self._recent:
            if item.data(0, QUESTION_ROLE) == qid:
                self.setCurrentItem(item)
        self._quiet = was
