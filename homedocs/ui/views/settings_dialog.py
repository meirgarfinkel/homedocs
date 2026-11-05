"""Settings: the folders searched (add, remove), what's left out of them (leave out a subfolder, take back in),
and the look."""

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ...core import library
from ...core.db import open_db
from ...core.settings import folders, left_out
from ...core.util import folder_prefix
from .. import theme, workers

PATH_ROLE = Qt.ItemDataRole.UserRole


class SettingsDialog(QDialog):
    """folders_changed(): a folder was added or removed, or something left out or taken back."""

    folders_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(620, 560)
        self._counts: dict[str, int] = {}
        lay = QVBoxLayout(self)

        lay.addWidget(QLabel("<b>Folders</b>"))
        note = QLabel("The folders HomeDocs reads and searches, with everything inside them.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.folder_list = QListWidget()
        self.folder_list.itemSelectionChanged.connect(self._enable)
        lay.addWidget(self.folder_list, 3)
        row = QHBoxLayout()
        self.add_button = QPushButton("Add folder...")
        self.add_button.clicked.connect(self._add)
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self._remove)
        row.addWidget(self.add_button)
        row.addWidget(self.remove_button)
        row.addStretch()
        lay.addLayout(row)

        lay.addSpacing(10)
        lay.addWidget(QLabel("<b>Left out</b>"))
        note = QLabel("Subfolders and files inside those folders that aren't read or searched.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.left_list = QListWidget()
        self.left_list.itemSelectionChanged.connect(self._enable)
        lay.addWidget(self.left_list, 2)
        row = QHBoxLayout()
        self.leave_button = QPushButton("Leave out a subfolder...")
        self.leave_button.clicked.connect(self._leave_out)
        self.back_button = QPushButton("Take back in")
        self.back_button.clicked.connect(self._take_back)
        row.addWidget(self.leave_button)
        row.addWidget(self.back_button)
        row.addStretch()
        lay.addLayout(row)

        lay.addSpacing(10)
        lay.addWidget(QLabel("<b>Look</b>"))
        self.look = QComboBox()
        for key, name in theme.MODES.items():
            self.look.addItem(name, key)
        self.look.setCurrentIndex(self.look.findData(theme.mode()))
        self.look.currentIndexChanged.connect(lambda _: theme.set_mode(self.look.currentData()))
        look = QHBoxLayout()
        look.addWidget(QLabel("Colours:"))
        look.addWidget(self.look)
        look.addStretch()
        lay.addLayout(look)
        for b in self.findChildren(QPushButton):
            b.setAutoDefault(False)  # (none of them is the main one)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.refresh()
        workers.run(_counts, self._got_counts, folders())

    # --- the lists ---------------------------------------------------------------------------------

    def refresh(self):
        keep = self._chosen(self.folder_list)
        self.folder_list.clear()
        for f in folders():
            n = self._counts.get(f)
            text = f + (f"   ·  {n:,} file{'s' if n != 1 else ''}" if n is not None else "")
            if not os.path.isdir(f):
                text += "   (not connected right now)"
            self._add_item(self.folder_list, text, f, keep, "folder")
        keep = self._chosen(self.left_list)
        self.left_list.clear()
        for p in left_out():
            self._add_item(self.left_list, p, p, keep, "folder" if os.path.isdir(p) else "doc")
        self._enable()

    @staticmethod
    def _add_item(lst, text, path, keep, icon):
        item = QListWidgetItem(theme.icon(icon, "muted"), text)
        item.setData(PATH_ROLE, path)
        item.setToolTip(path)
        lst.addItem(item)
        if path == keep:
            item.setSelected(True)

    @staticmethod
    def _chosen(lst) -> str | None:
        items = lst.selectedItems()
        return items[0].data(PATH_ROLE) if items else None

    def _enable(self):
        self.remove_button.setEnabled(self._chosen(self.folder_list) is not None)
        self.back_button.setEnabled(self._chosen(self.left_list) is not None)
        self.leave_button.setEnabled(bool(folders()))

    def _got_counts(self, counts, err):
        if err is None:
            self._counts = counts
            self.refresh()

    def _changed(self):
        self.refresh()
        self.folders_changed.emit()
        workers.run(_counts, self._got_counts, folders())

    # --- folders -----------------------------------------------------------------------------------

    def _add(self):
        start = folders()[-1] if folders() else os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(self, "Choose a folder to search", start)
        if not chosen:
            return
        why = library.add_folder(chosen)
        if why:
            QMessageBox.information(self, "Add folder", why)
            return
        self._changed()

    def _remove(self):
        folder = self._chosen(self.folder_list)
        if folder is None:
            return
        n = self._counts.get(folder)
        files = f"Its {n:,} file{'s' if n != 1 else ''} leave" if n else "Its files leave"
        answer = QMessageBox.question(
            self,
            "Remove folder",
            f"Stop searching {folder}?\n\n{files} the search. Nothing on disk changes.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            library.remove_folder(folder)
            self._changed()

    # --- left out ----------------------------------------------------------------------------------

    def _leave_out(self):
        start = self._chosen(self.folder_list) or folders()[0]
        chosen = QFileDialog.getExistingDirectory(self, "Choose a subfolder to leave out", start)
        if not chosen:
            return
        why = library.leave_out(chosen)
        if why:
            QMessageBox.information(self, "Leave out", why)
            return
        self._changed()

    def _take_back(self):
        path = self._chosen(self.left_list)
        if path is not None:
            library.take_back(path)
            self._changed()


def _counts(roots) -> dict[str, int]:
    """How many files each folder holds (that HomeDocs knows of)."""
    db = open_db()
    try:
        return {
            r: db.execute(
                "SELECT COUNT(*) FROM files WHERE substr(path, 1, ?) = ?", (len(folder_prefix(r)), folder_prefix(r))
            ).fetchone()[0]
            for r in roots
        }
    finally:
        db.close()
