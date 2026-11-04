"""The Library page: the folders being searched (add, remove, look again), and every file found in them
with its status, passage count and, for one that couldn't be read, why."""

import datetime as dt
import os

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core import library
from ...core.db import open_db
from ...core.settings import folders
from .. import theme, workers
from ..desktop import open_file, show_in_folder

COLUMNS = ["Name", "Folder", "Type", "Passages", "Modified", "Status"]  # (the status, often long, last)
NAME, FOLDER, KIND, PASSAGES, MODIFIED, STATUS = range(len(COLUMNS))
SORT_ROLE = Qt.ItemDataRole.UserRole
GROUP_ROLE = Qt.ItemDataRole.UserRole + 1

# The filter buttons: label, and which rows each shows
GROUPS = {"all": "All", "indexed": "Read", "waiting": "Waiting", "failed": "Couldn't read", "offline": "Offline"}


def group(r: library.FileRow) -> str:
    if r.offline:
        return "offline"
    return {"indexed": "indexed", "pending": "waiting"}.get(r.status, "failed")


def status_text(r: library.FileRow) -> str:
    if r.offline:
        return "Offline: its folder can't be reached (still searchable)"
    if r.status == "indexed":
        return "Read"
    if r.status == "pending":
        return "Waiting to be read"
    return r.error or "Couldn't read this file."


class FileTable(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[library.FileRow] = []

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

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
        if role == SORT_ROLE:
            return {PASSAGES: r.passages, MODIFIED: r.mtime}.get(c, (self._text(r, c) or "").lower())
        if role == GROUP_ROLE:
            return group(r)
        if role == Qt.ItemDataRole.ToolTipRole:
            return r.path if c in (NAME, FOLDER) else status_text(r) if c == STATUS else None
        if role == Qt.ItemDataRole.ForegroundRole and c == STATUS and group(r) == "failed":
            return theme.color("danger")
        if role == Qt.ItemDataRole.ForegroundRole and c in (FOLDER, KIND, PASSAGES, MODIFIED):
            return theme.color("muted")
        if role == Qt.ItemDataRole.TextAlignmentRole and c == PASSAGES:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.DecorationRole and c == NAME:
            return theme.icon("doc", "muted")
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
        return dt.datetime.fromtimestamp(r.mtime).strftime("%b %d, %Y")


class _Filter(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.group, self.words = "all", ""
        self.setSortRole(SORT_ROLE)

    def set(self, group=None, words=None):
        self.beginFilterChange()
        self.group = self.group if group is None else group
        self.words = self.words if words is None else words.lower().strip()
        self.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    def filterAcceptsRow(self, row, parent):  # noqa: N802 (Qt's name)
        m = self.sourceModel()
        if self.group != "all" and m.index(row, 0, parent).data(GROUP_ROLE) != self.group:
            return False
        return not self.words or self.words in m.rows[row].path.lower()


class _ElidedLabel(QLabel):
    """One line that ends in "..." when there's no room, instead of widening the window."""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.full = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(80)

    def text(self) -> str:  # (the whole of it)
        return self.full

    def resizeEvent(self, e):  # noqa: N802 (Qt's name)
        super().setText(self.fontMetrics().elidedText(self.full, Qt.TextElideMode.ElideMiddle, self.width()))
        super().resizeEvent(e)


class _FolderRow(QFrame):
    """A folder being searched: its path, how many files, and an × to stop searching it."""

    removed = Signal(str)

    def __init__(self, folder: str, count: int, parent=None):
        super().__init__(parent)
        self.setObjectName("folderRow")
        self.folder = folder
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 6, 6, 6)
        lay.setSpacing(10)
        self.icon = QLabel()
        self.path = _ElidedLabel(folder)
        self.path.setObjectName("folderPath")
        self.path.setToolTip(folder)
        self.count = QLabel(f"{count:,} file{'s' if count != 1 else ''}")
        self.count.setObjectName("hint")
        self.remove = QToolButton()
        self.remove.setToolTip("Stop searching this folder (its files aren't touched)")
        self.remove.setAccessibleName(f"Remove {folder}")
        self.remove.clicked.connect(lambda: self.removed.emit(self.folder))
        lay.addWidget(self.icon)
        lay.addWidget(self.path, 1)
        lay.addWidget(self.count)
        lay.addWidget(self.remove)
        self.restyle()

    def restyle(self):
        self.icon.setPixmap(theme.icon("folder", "muted").pixmap(18, 18))
        self.remove.setIcon(theme.icon("clear", "muted"))


class LibraryPage(QWidget):
    """folders_changed(): a folder was added or removed (time to index); count(str): what the page header
    says, like "1,204 files"."""

    folders_changed = Signal()
    rescan = Signal()
    count = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.loaded = False  # the table has been filled once
        self._latest = workers.Latest()
        self.stack = QStackedLayout(self)
        self.stack.addWidget(self._empty_state())
        self.stack.addWidget(self._full_state())
        theme.signals.changed.connect(self._restyle)
        self._restyle()
        self.reload()

    def _restyle(self):
        """The icons, after the colours changed."""
        self.add_button.setIcon(theme.icon("folder"))
        self.rescan_button.setIcon(theme.icon("restart"))
        for i in range(self.folder_list.count()):
            row = self.folder_list.itemAt(i).widget()
            if row is not None:
                row.restyle()

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
        self.folder_list = QVBoxLayout()
        self.folder_list.setSpacing(4)
        lay.addLayout(self.folder_list)
        buttons = QHBoxLayout()
        self.add_button = QPushButton("Add folder")
        self.add_button.clicked.connect(self.add_folder)
        self.rescan_button = QPushButton("Look for changes")
        self.rescan_button.setToolTip("Look through the folders now for new, changed and removed files")
        self.rescan_button.clicked.connect(self.rescan)
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.rescan_button)
        buttons.addStretch()
        lay.addLayout(buttons)
        lay.addSpacing(6)

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
        for col, width in ((NAME, 240), (FOLDER, 170), (KIND, 96), (PASSAGES, 76), (MODIFIED, 110)):
            self.table.setColumnWidth(col, width)
        self.table.doubleClicked.connect(lambda i: open_file(self._path(i)))
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.table, 1)
        return w

    # --- folders ---------------------------------------------------------------------------------

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

    def remove(self, folder: str):
        library.remove_folder(folder)
        self.reload()
        self.folders_changed.emit()

    # --- the table -------------------------------------------------------------------------------

    def reload(self):
        """Read the files again (on a worker thread) and show them."""
        roots = folders()
        self.stack.setCurrentIndex(1 if roots else 0)
        self._latest.run(_read_rows, lambda rows, err: self._show(roots, rows, err), roots)

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
        while self.folder_list.count():
            item = self.folder_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for f in roots:
            row = _FolderRow(f, sum(1 for r in rows if r.folder == f))
            row.removed.connect(self.remove)
            self.folder_list.addWidget(row)
        counts = dict.fromkeys(GROUPS, 0)
        for r in rows:
            counts[group(r)] += 1
        counts["all"] = len(rows)
        for key, b in self.filter_buttons.items():
            b.setText(f"{GROUPS[key]}  {counts[key]:,}")
            b.setVisible(key in ("all", "indexed") or counts[key] > 0 or b.isChecked())
        self.count.emit(f"{len(rows):,} file{'s' if len(rows) != 1 else ''}" if roots else "")
        self.loaded = True

    def _path(self, index) -> str:
        return self.model.rows[self.proxy.mapToSource(index).row()].path

    def _menu(self, pos):
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        path = self._path(index)
        m = QMenu(self)
        m.addAction("Open", lambda: open_file(path))
        m.addAction("Show in folder", lambda: show_in_folder(path))
        m.exec(self.table.viewport().mapToGlobal(pos))


def _read_rows(roots):
    db = open_db()
    try:
        return library.files(db, roots)
    finally:
        db.close()
