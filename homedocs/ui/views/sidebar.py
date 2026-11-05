"""The sidebar: the app's pages (Ask, and Library with the folders searched under it, as in HomePix: each folder
with how many files it holds, opened and closed by the arrow at its right), then the recent questions, each
reopened with its answer without asking the model again.

A big library has thousands of folders, so a folder's rows are made only when it's opened."""

import os

import shiboken6
from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QTreeWidget, QTreeWidgetItem

from .. import theme

ASK = "ask"
LIBRARY = "library"
PAGES = {ASK: "Ask", LIBRARY: "Library"}
ICONS = {ASK: "search", LIBRARY: "doc"}
WIDTH = 180  # the top bar centres the app's name over it

PLACE_ROLE = Qt.ItemDataRole.UserRole
QUESTION_ROLE = Qt.ItemDataRole.UserRole + 1
FOLDER_ROLE = Qt.ItemDataRole.UserRole + 2  # a folder's path
COUNT_ROLE = Qt.ItemDataRole.UserRole + 3  # the number at the right ("1,204"), or none
PLACEHOLDER = "…"  # the one child a closed folder with folders inside has, until it's opened

CHEVRON = 20  # room at the right of a row for the open/close arrow (so counts line up)
RIGHT = 4  # space after it


class RowDelegate(QStyledItemDelegate):
    """A row with, at its right, the muted file count and, for a folder with folders inside, the arrow that opens
    and closes it (from HomePix). The name gets the room that's left, so a short count doesn't squeeze it."""

    @staticmethod
    def _reserve(index, font) -> int:
        count = index.data(COUNT_ROLE) or ""
        room = QFontMetrics(font).horizontalAdvance(count) + 6 if count else 0
        return room + (CHEVRON + RIGHT if index.model().hasChildren(index) else 0)

    def initStyleOption(self, option, index):  # noqa: N802 (Qt's name)
        super().initStyleOption(option, index)
        reserve = self._reserve(index, theme.font(12))
        if reserve and option.text:
            room = option.rect.width() - reserve - 16 - (22 if not option.icon.isNull() else 0)
            option.text = QFontMetrics(option.font).elidedText(option.text, Qt.TextElideMode.ElideRight, room)

    def paint(self, p, option, index):
        super().paint(p, option, index)  # the row: its background, icon and name
        count = index.data(COUNT_ROLE)
        has_arrow = index.model().hasChildren(index)
        if not count and not has_arrow:
            return
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        chosen = option.state & QStyle.StateFlag.State_Selected
        right = option.rect.right() - RIGHT - (CHEVRON if has_arrow else 4)
        if count:
            p.setPen(theme.color("sideLabel"))
            p.setFont(theme.font(12))
            p.drawText(
                QRectF(option.rect.left(), option.rect.top(), right - option.rect.left(), option.rect.height()),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                count,
            )
        if has_arrow:
            arrow = QRectF(option.rect.right() - RIGHT - CHEVRON, option.rect.top(), CHEVRON, option.rect.height())
            tree = self.parent()
            if tree.hover_arrow is not None and tree.indexFromItem(tree.hover_arrow) == index:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(theme.color("hoverStrong"))
                p.drawEllipse(arrow.center(), 10, 10)
            pen = QPen(theme.color("sideStrong" if chosen else "sideText"), 1.5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            c, w = arrow.center(), 3.5
            h = -1.75 if tree.isExpanded(index) else 1.75  # ▴ open, ▾ closed
            path = QPainterPath(QPointF(c.x() - w, c.y() - h))
            path.lineTo(c.x(), c.y() + h)
            path.lineTo(c.x() + w, c.y() - h)
            p.drawPath(path)
        p.restore()


class Sidebar(QTreeWidget):
    chosen = Signal(str)  # ASK or LIBRARY
    folder_chosen = Signal(str)  # a folder's path
    reopen = Signal(int)  # a recent question's id
    folder_menu = Signal(str, QPoint)  # right-click on a folder: its path, and where (global)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setColumnCount(1)
        self.setItemDelegate(RowDelegate(self))
        self.setFrameShape(QTreeWidget.Shape.NoFrame)
        self.setHeaderHidden(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setRootIsDecorated(False)  # no arrows at the left: they're at the right (CountDelegate)
        self.setIndentation(12)
        self.setExpandsOnDoubleClick(False)  # (double-click chooses it, like a click)
        self.setMouseTracking(True)  # (the hand over an arrow)
        self.hover_arrow = None  # the row whose arrow the mouse is over
        self.setUniformRowHeights(True)
        self.setFixedWidth(WIDTH)
        self.setIconSize(QSize(16, 16))
        self.currentItemChanged.connect(self._embolden)
        self.itemExpanded.connect(self._fill)
        self._quiet = False
        self._tree: dict = {}  # folder_tree(): what's shown under Library
        self._nodes: dict = {}  # path -> its node, for the rows made so far
        for place, name in PAGES.items():
            item = QTreeWidgetItem([name])
            item.setData(0, PLACE_ROLE, place)
            self.addTopLevelItem(item)
        self.library = self._place_item(LIBRARY)
        self._recent: list[QTreeWidgetItem] = []
        self.itemSelectionChanged.connect(self._on_pick)
        self.select(ASK)

    # --- the folders ---------------------------------------------------------------------------------

    def set_folders(self, tree: dict):
        """The folders under Library (a library.folder_tree()). Keeps what was open and what was chosen."""
        opened = {it.data(0, FOLDER_ROLE) for it in self._items(self.library) if it.isExpanded()}
        library_open = self.library.isExpanded()
        chosen = self.current_folder()
        was, self._quiet = self._quiet, True
        self.hover_arrow = None
        self._tree, self._nodes = tree, {}
        self.library.takeChildren()
        total = sum(node["total"] for node in tree.values())
        self.library.setData(
            0, COUNT_ROLE, f"{total:,}" if total else ""
        )  # (the folders' counts: theirs is this again)
        for root, node in tree.items():
            item = self._add(self.library, node)
            item.setData(0, COUNT_ROLE, "")
            if not os.path.isdir(root):
                item.setText(0, f"{node['name']} (not connected)")
                item.setToolTip(0, f"{root}\nCan't be reached right now: is its drive plugged in?")
        self.library.setExpanded(library_open)
        self._reopen(self.library, opened)
        if chosen and not self.select_folder(chosen):
            self.select(LIBRARY)
        self._quiet = was

    def _add(self, parent, node) -> QTreeWidgetItem:
        item = QTreeWidgetItem([node["name"]])
        item.setData(0, COUNT_ROLE, f"{node['total']:,}" if node["total"] else "")
        item.setData(0, FOLDER_ROLE, node["path"])
        self._nodes[node["path"]] = node
        item.setToolTip(0, node["path"])
        if node["children"]:
            item.addChild(QTreeWidgetItem([PLACEHOLDER]))  # (the arrow; its rows are made when it's opened)
        parent.addChild(item)
        return item

    def _fill(self, item):
        """A folder was opened: make its folders' rows, if they aren't made yet."""
        node = self._nodes.get(item.data(0, FOLDER_ROLE))
        if not node or item.childCount() != 1 or item.child(0).text(0) != PLACEHOLDER:
            return
        item.takeChildren()
        for name in sorted(node["children"], key=str.lower):
            self._add(item, node["children"][name])

    def _reopen(self, parent, opened):
        for i in range(parent.childCount()):
            it = parent.child(i)
            if it.data(0, FOLDER_ROLE) in opened:
                it.setExpanded(True)  # (fills it)
                self._reopen(it, opened)

    def select_folder(self, path: str) -> bool:
        """Choose a folder without saying so, opening the folders it's in. False if it isn't listed."""
        root = next((r for r in self._tree if path == r or path.startswith(os.path.join(r, ""))), None)
        if root is None:
            return False
        was, self._quiet = self._quiet, True
        item, found = self.library, None
        self.library.setExpanded(True)
        for i in range(item.childCount()):
            if item.child(i).data(0, FOLDER_ROLE) == root:
                found = item.child(i)
        parts = os.path.relpath(path, root).split(os.sep) if path != root else []
        for part in parts:
            if found is None:
                break
            found.setExpanded(True)
            want = os.path.join(found.data(0, FOLDER_ROLE), part)
            found = next(
                (found.child(i) for i in range(found.childCount()) if found.child(i).data(0, FOLDER_ROLE) == want),
                None,
            )
        if found is not None:
            self.setCurrentItem(found)
        self._quiet = was
        return found is not None

    def current_folder(self) -> str | None:
        items = self.selectedItems()
        return items[0].data(0, FOLDER_ROLE) if items else None

    def _items(self, parent):
        stack = [parent.child(i) for i in range(parent.childCount())]
        while stack:
            it = stack.pop()
            yield it
            stack.extend(it.child(i) for i in range(it.childCount()))

    # --- the recent questions ------------------------------------------------------------------------

    def set_recent(self, questions: list[tuple[int, str]]):
        """The recent questions, newest first: (id, question)."""
        for item in self._recent:
            self.takeTopLevelItem(self.indexOfTopLevelItem(item))
        self._recent = []
        if not questions:
            return
        head = QTreeWidgetItem(["Recent questions"])
        head.setFlags(Qt.ItemFlag.ItemIsEnabled)  # (a heading: not selectable)
        f = QFont(head.font(0))
        f.setWeight(QFont.Weight.Medium)
        f.setPointSizeF(f.pointSizeF() * 12 / 14)
        head.setFont(0, f)
        head.setForeground(0, theme.color("sideLabel"))
        self._recent.append(head)
        for qid, question in questions:
            item = QTreeWidgetItem([question])
            item.setData(0, QUESTION_ROLE, qid)
            item.setToolTip(0, question)
            self._recent.append(item)
        self.addTopLevelItems(self._recent)

    # --- choosing ------------------------------------------------------------------------------------

    def restyle(self):
        """The pages' icons and the heading's colour, after the colours changed."""
        for i in range(self.topLevelItemCount()):
            item = self.topLevelItem(i)
            place = item.data(0, PLACE_ROLE)
            if place in ICONS:
                item.setIcon(0, theme.icon(ICONS[place], "accent"))
        if self._recent:
            self._recent[0].setForeground(0, theme.color("sideLabel"))

    @staticmethod
    def _embolden(now, before):
        """The chosen place a little heavier."""
        for item, weight in ((before, QFont.Weight.Normal), (now, QFont.Weight.Medium)):
            if item is not None and shiboken6.isValid(item) and item.flags() & Qt.ItemFlag.ItemIsSelectable:
                f = QFont(item.font(0))
                f.setWeight(weight)
                item.setFont(0, f)

    def _place_item(self, place):
        for i in range(self.topLevelItemCount()):
            if self.topLevelItem(i).data(0, PLACE_ROLE) == place:
                return self.topLevelItem(i)
        return None

    def current_place(self):
        items = self.selectedItems()
        if not items:
            return None
        return LIBRARY if items[0].data(0, FOLDER_ROLE) else items[0].data(0, PLACE_ROLE)

    def select(self, place: str):
        """Choose a page without saying so (no chosen signal)."""
        was, self._quiet = self._quiet, True
        item = self._place_item(place)
        if item is not None:
            self.setCurrentItem(item)
        self._quiet = was

    def _on_pick(self):
        items = self.selectedItems()
        if not items or self._quiet:
            return
        qid = items[0].data(0, QUESTION_ROLE)
        folder = items[0].data(0, FOLDER_ROLE)
        if qid is not None:
            self.reopen.emit(qid)
        elif folder:
            self.folder_chosen.emit(folder)
        elif items[0].data(0, PLACE_ROLE):
            self.chosen.emit(items[0].data(0, PLACE_ROLE))

    def select_question(self, qid: int | None):
        """Show a recent question as chosen (or, None, none of them), without saying so."""
        was, self._quiet = self._quiet, True
        for item in self._recent:
            if item.data(0, QUESTION_ROLE) == qid:
                self.setCurrentItem(item)
        self._quiet = was

    # --- the arrows (from HomePix) -------------------------------------------------------------------

    def _arrow_hit(self, pos) -> QTreeWidgetItem | None:
        """The row whose open/close arrow is at pos, if any."""
        item = self.itemAt(pos)
        if item is None or item.childCount() == 0:
            return None
        return item if pos.x() >= self.viewport().width() - RIGHT - CHEVRON - 6 else None

    def mousePressEvent(self, e):  # noqa: N802 (Qt's name)
        pos = e.position().toPoint()
        item = self._arrow_hit(pos)
        if item is not None and e.button() == Qt.MouseButton.LeftButton:
            item.setExpanded(not item.isExpanded())
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):  # noqa: N802 (Qt's name)
        hit = self._arrow_hit(e.position().toPoint())
        self.viewport().setCursor(Qt.CursorShape.PointingHandCursor if hit else Qt.CursorShape.ArrowCursor)
        if hit is not self.hover_arrow:
            self.hover_arrow = hit
            self.viewport().update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):  # noqa: N802 (Qt's name)
        if self.hover_arrow is not None:
            self.hover_arrow = None
            self.viewport().update()
        super().leaveEvent(e)

    def contextMenuEvent(self, e):  # noqa: N802 (Qt's name)
        item = self.itemAt(self.viewport().mapFromGlobal(e.globalPos()))
        path = item.data(0, FOLDER_ROLE) if item else None
        if path:
            self.folder_menu.emit(path, e.globalPos())
