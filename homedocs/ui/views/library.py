"""The Library page: every file in the folders being searched, with its status, passage count and, for one that
couldn't be read, why; and, file by file, open, show in folder, read again, leave out or take back in. (Which
folders are searched is chosen in Settings; the first one here, on first run.)"""

import datetime as dt
import os

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedLayout,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ...core import library
from ...core.db import open_db
from ...core.settings import folders, left_out
from ...core.util import folder_prefix
from .. import theme, workers
from ..desktop import open_file, show_in_folder

COLUMNS = ["Name", "Folder", "Type", "Passages", "Modified", "Status"]  # (the status, often long, last)
NAME, FOLDER, KIND, PASSAGES, MODIFIED, STATUS = range(len(COLUMNS))
GROUP_ROLE = Qt.ItemDataRole.UserRole + 1

# The filter buttons: label, and which rows each shows
GROUPS = {
    "all": "All",
    "indexed": "Read",
    "waiting": "Waiting",
    "failed": "Couldn't read",
    "offline": "Offline",
    "left_out": "Left out",
}


def group(r: library.FileRow) -> str:
    if r.status == "left_out":
        return "left_out"
    if r.offline:
        return "offline"
    return {"indexed": "indexed", "pending": "waiting"}.get(r.status, "failed")


def status_text(r: library.FileRow) -> str:
    if r.status == "left_out":
        return "Left out: not read or searched"
    if r.offline:
        return "Offline: its folder can't be reached (still searchable)"
    if r.status == "indexed":
        return "Read"
    if r.status == "pending":
        return "Waiting to be read"
    return r.error or "Couldn't read this file."


class FileTable(QAbstractTableModel):
    """The files, kept in the order shown. It sorts itself, in Python, in one go: Qt's own sorting asks for every
    row's value again and again, which took 5-10 s on the UI thread for 10,000 files."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[library.FileRow] = []
        self.order = (MODIFIED, Qt.SortOrder.DescendingOrder)

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = self._sorted(rows)
        self.endResetModel()

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        self.order = (column, order)
        self.layoutAboutToBeChanged.emit()
        before = self.persistentIndexList()
        kept = [(i, self.rows[i.row()]) for i in before]
        self.rows = self._sorted(self.rows)
        where = {id(r): n for n, r in enumerate(self.rows)}
        self.changePersistentIndexList(before, [self.index(where[id(r)], i.column()) for i, r in kept])
        self.layoutChanged.emit()

    def _sorted(self, rows):
        column, order = self.order
        if column < 0:
            return list(rows)
        if column == PASSAGES:
            key = lambda r: r.passages  # noqa: E731
        elif column == MODIFIED:
            key = lambda r: r.mtime  # noqa: E731
        else:
            key = lambda r: (self._text(r, column) or "").lower()  # noqa: E731
        return sorted(rows, key=key, reverse=order == Qt.SortOrder.DescendingOrder)

    def rowCount(self, parent=QModelIndex()):  # noqa: N802, B008 (Qt's names)
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):  # noqa: N802, B008
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.TextAlignmentRole:
            right = section == PASSAGES
            return int(
                (Qt.AlignmentFlag.AlignRight if right else Qt.AlignmentFlag.AlignLeft) | Qt.AlignmentFlag.AlignVCenter
            )
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        r, c = self.rows[index.row()], index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            return self._text(r, c)
        if role == GROUP_ROLE:
            return group(r)
        if role == Qt.ItemDataRole.ToolTipRole:
            return r.path if c in (NAME, FOLDER) else status_text(r) if c == STATUS else None
        if role == Qt.ItemDataRole.ForegroundRole and c == STATUS and group(r) == "failed":
            return theme.color("danger")
        if role == Qt.ItemDataRole.ForegroundRole and (
            c in (FOLDER, KIND, PASSAGES, MODIFIED) or r.status == "left_out"
        ):
            return theme.color("muted")
        if role == Qt.ItemDataRole.TextAlignmentRole and c == PASSAGES:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.DecorationRole and c == NAME:
            return theme.icon("folder" if r.kind == "Folder" else "doc", "muted")
        return None

    @staticmethod
    def _text(r: library.FileRow, c: int) -> str:
        if c == NAME:
            return r.name
        if c == FOLDER:
            base = os.path.basename(r.folder)
            return os.path.join(base, r.where) if r.where else base
        if c == KIND:
            return r.kind
        if c == STATUS:
            return status_text(r)
        if c == PASSAGES:
            return f"{r.passages:,}" if r.status == "indexed" else ""
        return dt.datetime.fromtimestamp(r.mtime).strftime("%b %d, %Y") if r.mtime else ""


class _Filter(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.group, self.words = "all", ""
        self.folder = None  # a folder_prefix(): only the files in it (and its folders), or None for all

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        self.sourceModel().sort(column, order)  # (it keeps the source's order: the source sorts itself)

    def set(self, group=None, words=None, folder=False):
        self.beginFilterChange()
        if folder is not False:
            self.folder = folder_prefix(folder) if folder else None
        self.group = self.group if group is None else group
        self.words = self.words if words is None else words.lower().strip()
        self.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    def filterAcceptsRow(self, row, parent):  # noqa: N802 (Qt's name)
        m = self.sourceModel()
        if self.folder and not m.rows[row].path.startswith(self.folder):
            return False
        if self.group != "all" and group(m.rows[row]) != self.group:
            return False
        return not self.words or self.words in m.rows[row].path.lower()


class LibraryPage(QWidget):
    """folders_changed(): a folder was added, or something left out or taken back (time to index); rescan():
    look through the folders now; count(str): what the page header says, like "1,204 files"."""

    folders_changed = Signal()
    rescan = Signal()
    count = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.loaded = False  # the table has been filled once
        self._stale = True  # files changed while the page was hidden: read them when it's shown
        self._latest = workers.Latest()
        self.stack = QStackedLayout(self)
        self.stack.addWidget(self._empty_state())
        self.stack.addWidget(self._full_state())
        theme.signals.changed.connect(self._restyle)
        self._restyle()
        self.reload()

    def _restyle(self):
        """The icons, after the colours changed."""
        self.rescan_button.setIcon(theme.icon("restart"))

    # --- building -------------------------------------------------------------------------------

    def _empty_state(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(24, 12, 24, 24)
        lay.addStretch(1)
        title = QLabel("Choose the folders to search")
        title.setObjectName("emptyTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text = QLabel(
            "HomeDocs reads the PDF, Word, Markdown and text files in them, so you can ask about what they say. "
            "Your files are never changed, and nothing leaves this computer."
        )
        text.setObjectName("hint")
        text.setWordWrap(True)
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text.setFixedWidth(460)  # (a fixed width, so the wrapped lines get their height)
        self.choose = QPushButton("Choose a folder")
        self.choose.setDefault(True)
        self.choose.clicked.connect(self.add_folder)
        lay.addWidget(title)
        lay.addSpacing(6)
        middle = QHBoxLayout()  # (centred by stretches: an alignment would lose the wrapped lines' height)
        middle.addStretch()
        middle.addWidget(text)
        middle.addStretch()
        lay.addLayout(middle)
        lay.addSpacing(16)
        lay.addWidget(self.choose, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addStretch(2)
        return w

    def _full_state(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(24, 8, 24, 16)
        lay.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.filters = QButtonGroup(self)
        self.filter_buttons = {}
        for key, label in GROUPS.items():
            b = QPushButton(label)
            b.setObjectName("filter")
            b.setCheckable(True)
            b.setChecked(key == "all")
            b.clicked.connect(lambda _=False, k=key: self.proxy.set(group=k))
            self.filters.addButton(b)
            self.filter_buttons[key] = b
            bar.addWidget(b)
        bar.addStretch()
        self.find = QLineEdit()
        self.find.setPlaceholderText("Filter by name")
        self.find.setClearButtonEnabled(True)
        self.find.setMaximumWidth(260)
        self.find.textChanged.connect(lambda t: self.proxy.set(words=t))
        bar.addWidget(self.find)
        self.rescan_button = QPushButton("Look for changes")
        self.rescan_button.setToolTip("Look through the folders now for new, changed and removed files")
        self.rescan_button.clicked.connect(self.rescan)
        bar.addWidget(self.rescan_button)
        lay.addLayout(bar)

        self.model = FileTable(self)
        self.proxy = _Filter(self)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setObjectName("files")
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(MODIFIED, Qt.SortOrder.DescendingOrder)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(34)
        head = self.table.horizontalHeader()
        head.setHighlightSections(False)
        head.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        head.setSectionResizeMode(STATUS, QHeaderView.ResizeMode.Stretch)
        for col, width in ((NAME, 240), (FOLDER, 170), (KIND, 96), (PASSAGES, 76), (MODIFIED, 124)):
            self.table.setColumnWidth(col, width)
        self.table.doubleClicked.connect(lambda i: open_file(self._path(i)))
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.table, 1)
        return w

    # --- folders (the first one: the rest are chosen in Settings) ---------------------------------

    def add_folder(self):
        start = folders()[-1] if folders() else os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(self, "Choose a folder to search", start)
        if chosen:
            self.add(chosen)

    def add(self, folder: str):
        why = library.add_folder(folder)
        if why:
            QMessageBox.information(self, "Add folder", why)
            return
        self.reload()
        self.folders_changed.emit()

    def leave_out(self, path: str):
        why = library.leave_out(path)
        if why:
            QMessageBox.information(self, "Leave out", why)
            return
        self.reload()
        self.folders_changed.emit()

    def take_back(self, path: str):
        library.take_back(path)
        self.reload()
        self.folders_changed.emit()

    def read_again(self, path: str):
        workers.run(_read_again, lambda _, err: (self.reload(), self.rescan.emit()), path)

    # --- the table -------------------------------------------------------------------------------

    def reload(self):
        """Read the files again (on a worker thread) and show them; while the page is hidden, when it's shown
        (indexing asks often, and the window has better things to do)."""
        roots = folders()
        self.stack.setCurrentIndex(1 if roots else 0)
        self._stale = not self.isVisible()
        if self._stale:
            return
        self._latest.run(_read_rows, lambda rows, err: self._show(roots, rows, err), roots, left_out())

    def _show(self, roots, rows, err):
        if err is not None:
            print(f"library: {err!r}")
            rows = []
        sel = self.table.currentIndex()
        keep = self._path(sel) if sel.isValid() else None
        self.model.set_rows(rows)
        if keep:
            for i in range(self.proxy.rowCount()):
                if self._path(self.proxy.index(i, 0)) == keep:
                    self.table.selectRow(i)
        self._recount()
        self.loaded = True

    def show_folder(self, folder: str | None):
        """Only the files in folder (and the folders in it), or, None, all of them."""
        self.proxy.set(folder=folder)
        self._recount()

    def _recount(self):
        """The filter buttons' counts and the page header's, for the folder shown."""
        prefix = self.proxy.folder
        rows = [r for r in self.model.rows if not prefix or r.path.startswith(prefix)]
        counts = dict.fromkeys(GROUPS, 0)
        for r in rows:
            counts[group(r)] += 1
        counts["all"] = len(rows) - counts["left_out"]
        for key, b in self.filter_buttons.items():
            b.setText(f"{GROUPS[key]}  {counts[key]:,}")
            b.setVisible(key in ("all", "indexed") or counts[key] > 0 or b.isChecked())
        n = counts["all"]
        self.count.emit(f"{n:,} file{'s' if n != 1 else ''}" if folders() else "")

    def showEvent(self, e):  # noqa: N802 (Qt's name)
        super().showEvent(e)
        if self._stale:
            self.reload()

    def _path(self, index) -> str:
        return self.model.rows[self.proxy.mapToSource(index).row()].path

    def _menu(self, pos):
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        r = self.model.rows[self.proxy.mapToSource(index).row()]
        path = r.path
        m = QMenu(self)
        if r.status == "left_out":
            m.addAction("Take back in", lambda: self.take_back(path))
            m.addAction("Show in folder", lambda: show_in_folder(path))
        else:
            m.addAction("Open", lambda: open_file(path))
            m.addAction("Show in folder", lambda: show_in_folder(path))
            m.addAction("Read again", lambda: self.read_again(path))
            m.addSeparator()
            m.addAction("Leave out this file", lambda: self.leave_out(path))
            if r.where:  # (in a subfolder: the searched folder itself is removed in Settings)
                m.addAction(
                    f"Leave out the folder {os.path.basename(os.path.dirname(path))}",
                    lambda: self.leave_out(os.path.dirname(path)),
                )
        m.exec(self.table.viewport().mapToGlobal(pos))


def _read_rows(roots, left):
    db = open_db()
    try:
        return library.files(db, roots, left)
    finally:
        db.close()


def _read_again(path):
    db = open_db()
    try:
        library.read_again(db, path)
    finally:
        db.close()
