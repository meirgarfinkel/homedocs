"""Settings: the look (folders to search are added here once indexing arrives)."""

from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QVBoxLayout

from .. import theme


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(480, 200)
        lay = QVBoxLayout(self)
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
        lay.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
