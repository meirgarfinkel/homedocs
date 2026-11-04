"""The main window: the toolbar (with the question box), the sidebar, the Ask and Library pages, the info
panel at the side, and the status pill over the page's corner."""

import time

from PySide6.QtCore import QEvent, QObject, QRect, QRectF, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QSizePolicy,
    QStackedWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..core.config import data_dir
from ..core.search import Searcher
from ..core.settings import folders
from . import theme, workers
from .indexing import IndexStatus, IndexWatcher
from .menu import MenuButton, add_row
from .models_ui import ModelLoader
from .status_pill import StatusPill
from .updates import Updater
from .views.ask import AskPage
from .views.library import LibraryPage
from .views.passage_panel import PassagePanel
from .views.settings_dialog import SettingsDialog
from .views.sidebar import ASK, LIBRARY, PAGES, Sidebar
from .views.update_bar import UpdateBar


class _Corner(QWidget):
    """A rounded top corner of the page, where it meets the toolbar and a side panel: the panels'
    colour painted outside a quarter circle, so the page sits in a well inside one band."""

    RADIUS = 16

    def __init__(self, page: QWidget, right: bool):
        super().__init__(page)
        self.right = right
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFixedSize(self.RADIUS, self.RADIUS)
        page.installEventFilter(self)
        self._place()

    def eventFilter(self, obj, e):  # noqa: N802 (Qt's name)
        if e.type() == QEvent.Type.Resize:
            self._place()
        return False

    def _place(self):
        self.move(self.parentWidget().width() - self.RADIUS if self.right else 0, 0)
        self.raise_()

    def paintEvent(self, _):  # noqa: N802 (Qt's name)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.RADIUS
        outside, rounded = QPainterPath(), QPainterPath()
        outside.addRect(QRectF(0, 0, r, r))
        x = -r if self.right else 0
        rounded.addEllipse(QRectF(x, 0, 2 * r, 2 * r))
        p.fillPath(outside.subtracted(rounded), theme.color("side"))


class _CornerSwitch(QObject):
    """Shows a corner only while its side panel is open."""

    def __init__(self, corner: _Corner, dock: QDockWidget):
        super().__init__(corner)
        self.corner, self.dock = corner, dock
        dock.installEventFilter(self)
        corner.setVisible(dock.isVisible())

    def eventFilter(self, obj, e):  # noqa: N802 (Qt's name)
        if e.type() in (QEvent.Type.Show, QEvent.Type.Hide):
            self.corner.setVisible(self.dock.isVisible())
            self.corner.raise_()
        return False


def _spacer(width: int, stretch: bool = False) -> QWidget:
    """Space in the toolbar: fixed, or taking what's left."""
    w = QWidget()
    if stretch:
        w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    else:
        w.setFixedWidth(width)
    return w


class AskBox(QLineEdit):
    """The question box: a pill with a drawn icon at the left, and (with text in) an × at the right."""

    escaped = Signal()
    icon = clear_icon = None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.textChanged.connect(lambda _: self.update())
        self._over_clear = False

    def _clear_rect(self) -> QRect:
        return QRect(self.width() - 34, (self.height() - 24) // 2, 24, 24)

    def paintEvent(self, e):  # noqa: N802 (Qt's name)
        super().paintEvent(e)
        p = QPainter(self)
        if self.icon is not None:
            self.icon.paint(p, QRect(14, (self.height() - 18) // 2, 18, 18))
        if self.text() and self.clear_icon is not None:
            if self._over_clear:
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(theme.color("hoverStrong"))
                p.drawEllipse(self._clear_rect())
            self.clear_icon.paint(p, self._clear_rect().adjusted(4, 4, -4, -4))
        p.end()

    def mousePressEvent(self, e):  # noqa: N802 (Qt's name)
        if self.text() and self._clear_rect().contains(e.position().toPoint()):
            self.clear()
            self.setFocus()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):  # noqa: N802 (Qt's name)
        over = bool(self.text()) and self._clear_rect().contains(e.position().toPoint())
        self.setCursor(Qt.CursorShape.PointingHandCursor if over else Qt.CursorShape.IBeamCursor)
        if over != self._over_clear:
            self._over_clear = over
            self.update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):  # noqa: N802 (Qt's name)
        if self._over_clear:
            self._over_clear = False
            self.update()
        super().leaveEvent(e)

    def keyPressEvent(self, e):  # noqa: N802 (Qt's name)
        if e.key() == Qt.Key.Key_Escape:
            self.escaped.emit()
        else:
            super().keyPressEvent(e)


class MainWindow(QMainWindow):
    closing = Signal()  # the window is closing (the app quits after it)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("HomeDocs")
        # window layout and sizes, kept with the rest of HomeDocs' data
        self.settings = QSettings(str(data_dir() / "window.ini"), QSettings.Format.IniFormat)
        self.loaded = False  # everything the window shows at start has been read once

        self.ask_page = AskPage()
        self.library_page = LibraryPage()
        self.library_page.count.connect(self._show_count)
        self.watcher = IndexWatcher(self)
        self.pages = {ASK: self.ask_page, LIBRARY: self.library_page}
        self.stack = QStackedWidget()
        for w in self.pages.values():
            self.stack.addWidget(w)
        self.update_bar = UpdateBar()
        self.page_header = QWidget()
        self.page_header.setObjectName("pageHeader")
        head = QHBoxLayout(self.page_header)
        head.setContentsMargins(24, 18, 24, 4)
        self.page_title = QLabel()
        self.page_title.setObjectName("pageTitle")
        self.count_label = QLabel()
        self.count_label.setObjectName("pageCount")
        head.addWidget(self.page_title)
        head.addStretch()
        head.addWidget(self.count_label)
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.update_bar)
        lay.addWidget(self.page_header)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.sidebar = Sidebar()
        self.sidebar_dock = self._dock("Sidebar", "sidebar", self.sidebar, Qt.DockWidgetArea.LeftDockWidgetArea)
        self.info = PassagePanel()
        self.info_dock = self._dock("Info", "info", self.info, Qt.DockWidgetArea.RightDockWidgetArea)
        self.info_dock.hide()  # opens when a source is clicked

        self._build_toolbar()
        self.updater = Updater(self)
        self._build_menu()
        self.background = IndexStatus(self.watcher)
        # messages and background work in a small box over the page's corner: nothing moves when it shows
        self.status_pill = StatusPill(central, self.statusBar(), self.background)
        self.watcher.state.connect(self.status_pill.refresh)
        self.watcher.changed.connect(self.library_page.reload)
        self.watcher.changed.connect(self._vectors_changed)
        self.watcher.state.connect(self._index_state)
        self.searcher = Searcher()
        self._asking = workers.Latest()
        self.models = ModelLoader(self.watcher.indexer, self.searcher, self)
        self.models.progress.connect(self._model_progress)
        self.models.ready.connect(self._models_ready)
        self.models.failed.connect(self._models_failed)
        self.ask_page.opened.connect(self.open_passage)
        self.ask_page.choose_folders.connect(lambda: (self.show_page(LIBRARY), self.library_page.add_folder()))
        self.ask_page.retry.connect(self._retry_models)
        self.library_page.folders_changed.connect(self._update_note)
        self._model_problem = None
        self._reloaded = 0.0
        self._indexing = False
        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.timeout.connect(self._vectors_changed)
        self.library_page.folders_changed.connect(self.watcher.start)
        self.library_page.rescan.connect(self.watcher.start)
        QApplication.instance().applicationStateChanged.connect(self._app_state)
        self.corners = [_Corner(central, right) for right in (False, True)]
        for corner, dock in zip(self.corners, (self.sidebar_dock, self.info_dock), strict=True):
            _CornerSwitch(corner, dock)
        self.status_pill.raise_()
        theme.signals.changed.connect(self._restyle)
        self._restyle()
        self.sidebar.chosen.connect(self.show_page)

        geometry = self.settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(1280, 820)
        state = self.settings.value("state")
        if state is not None:
            self.restoreState(state)
        self.show_page(ASK if folders() else LIBRARY)  # (the first time: choose folders)
        QTimer.singleShot(0, self._ready)  # once the event loop has run: the window is up
        self._update_timer = QTimer(self)  # a newer HomeDocs: get it, then offer a restart
        self._update_timer.setSingleShot(True)
        self._update_timer.timeout.connect(self.updater.check_quietly)
        self._update_timer.start(8000)

    def _ready(self):
        self.loaded = True
        self._update_note()
        self.models.start()  # downloaded the first time, then loaded
        self.watcher.start_if_due()  # look for new and changed files

    def _app_state(self, state):
        if state == Qt.ApplicationState.ApplicationActive and self.loaded:
            self.watcher.start_if_due()  # back from another app, where files may have changed

    def _show_count(self, text: str):
        if self.stack.currentWidget() is self.library_page:
            self.count_label.setText(text)

    def _dock(self, title, name, widget, area) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(name)
        dock.setWidget(widget)
        dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable)
        dock.setTitleBarWidget(QWidget())  # no title bar: the toolbar's buttons show and hide it
        self.addDockWidget(area, dock)
        return dock

    def _restyle(self):
        """The icons, after the colours changed."""
        self.sidebar_action.setIcon(theme.icon("sidebar"))
        self.info_action.setIcon(theme.icon("info"))
        self.ask_box.icon = theme.icon("ask")
        self.ask_box.clear_icon = theme.icon("clear", "muted")
        self.ask_box.update()
        self.brand.setPixmap(theme.brand_mark())
        for corner in getattr(self, "corners", ()):
            corner.update()

    def _build_toolbar(self):
        bar = QToolBar("Main", self)
        bar.setObjectName("main")
        bar.setMovable(False)
        bar.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
        bar.setIconSize(QSize(20, 20))
        bar.setFixedHeight(60)
        self.addToolBar(bar)
        bar.addWidget(_spacer(6))
        self.brand = QLabel()
        bar.addWidget(self.brand)
        name = QLabel("HomeDocs")
        name.setObjectName("brand")
        bar.addWidget(name)
        bar.addWidget(_spacer(14))
        self.sidebar_action = self.sidebar_dock.toggleViewAction()
        self.sidebar_action.setText("Sidebar")
        bar.addAction(self.sidebar_action)
        bar.addWidget(_spacer(0, stretch=True))
        self.ask_box = AskBox()
        self.ask_box.setObjectName("search")
        self.ask_box.setPlaceholderText("Ask your documents")
        self.ask_box.setMinimumWidth(380)
        self.ask_box.setMaximumWidth(620)
        self.ask_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.ask_box.escaped.connect(self.ask_box.clearFocus)
        self.ask_box.returnPressed.connect(self.ask)
        bar.addWidget(self.ask_box)
        bar.addWidget(_spacer(0, stretch=True))
        find = QAction("Ask", self)
        find.setShortcut(QKeySequence.StandardKey.Find)
        find.triggered.connect(lambda: (self.ask_box.setFocus(), self.ask_box.selectAll()))
        self.addAction(find)
        info = self.info_action = self.info_dock.toggleViewAction()
        info.setText("Info")
        info.setShortcut(QKeySequence("Ctrl+I"))
        bar.addAction(info)
        button = bar.widgetForAction(info)  # words beside the icon
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setProperty("label", True)
        self.more_button = MenuButton()
        self.more_button.setAccessibleName("Menu")
        self.more_menu = QMenu(self.more_button)
        self.more_menu.setObjectName("mainMenu")
        self.more_menu.setMinimumWidth(340)
        self.more_button.setMenu(self.more_menu)
        bar.addWidget(self.more_button)

    def add_action(self, text, slot, keys=()) -> QAction:
        a = QAction(text, self)
        a.setShortcuts([QKeySequence(k) for k in keys])
        a.triggered.connect(slot)
        self.addAction(a)  # the shortcut works wherever the focus is in the window
        return a

    def _build_menu(self):
        """The toolbar's menu: one list, no submenus (every item's shortcut works anywhere in the window)."""
        m = self.more_menu
        add_row(
            m,
            self.add_action("Library", lambda: self.show_page(LIBRARY), ["Ctrl+L"]),
            "doc",
            "Every file HomeDocs has read, and any it couldn't",
        )
        m.addSeparator()
        add_row(m, self.add_action("Settings...", self.open_settings, ["Ctrl+,"]), "settings")
        add_row(m, self.add_action("Check for updates", self.updater.check), "update")
        add_row(m, self.add_action("Quit", self.close, [QKeySequence.StandardKey.Quit]), "quit")

    def show_page(self, place: str):
        self.stack.setCurrentWidget(self.pages[place])
        self.page_title.setText(PAGES[place])
        self.count_label.setText("")
        if place == LIBRARY:
            self.library_page.reload()
        self.sidebar.select(place)

    def bring_forward(self):
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def open_settings(self):
        SettingsDialog(self).exec()

    # --- asking ------------------------------------------------------------------------------------

    def ask(self, question: str | None = None):
        q = (question if question is not None else self.ask_box.text()).strip()
        if not q:
            return
        self.show_page(ASK)
        self.ask_page.searching(q)
        self._asking.run(self.searcher.search, lambda found, err: self._answered(q, found, err), q)

    def _answered(self, question, found, err):
        if err is not None:
            print(f"search: {err!r}")
            self.ask_page.show_error(f"Something went wrong while searching: {err}")
            return
        self.ask_page.show_found(question, found)

    def open_passage(self, result):
        self.info.show_result(result)
        self.info_dock.show()

    VECTOR_RELOAD_GAP = 20  # seconds: while passages are being embedded, search catches up this often

    def _index_state(self, s):
        if self._indexing and not s["running"]:
            self._vectors_changed(done=True)  # the pass is over: search sees all of it now
        self._indexing = s["running"]

    def _vectors_changed(self, done=False):
        """Indexing changed what's embedded: reload the vectors for search, at most every VECTOR_RELOAD_GAP
        seconds while it's busy (all of them are read each time), and right away when it's done."""
        if not self.models.loaded:
            return
        if not done and self.watcher.running():
            if self._reload_timer.isActive():
                return
            wait = self.VECTOR_RELOAD_GAP - (time.monotonic() - self._reloaded)
            if wait > 0:
                self._reload_timer.start(int(wait * 1000))
                return
        self._reload_timer.stop()
        self._reloaded = time.monotonic()
        workers.run(self.searcher.refresh, lambda _, err: err and print(f"vectors: {err!r}"))

    # --- the search models -----------------------------------------------------------------------

    def _model_progress(self, done, total):
        mb = f"{done / 1e6:.0f} of {total / 1e6:.0f} MB"
        self.statusBar().showMessage(f"Downloading the search models: {mb}")
        self._model_problem = None
        self._update_note(downloading=mb)

    def _models_ready(self):
        self.statusBar().clearMessage()
        self._model_problem = None
        self._update_note()
        self.watcher.start()  # passages get their vectors

    def _models_failed(self, message):
        self.statusBar().clearMessage()
        self._model_problem = message
        self._update_note()

    def _retry_models(self):
        self._model_problem = None
        self._update_note(downloading="starting")
        self.models.start()

    def _update_note(self, downloading: str | None = None):
        """The Ask page's note: what's missing for searching (folders, the models), or nothing."""
        if not folders():
            self.ask_page.show_note("Choose the folders to search first.", "Choose a folder", "folders")
        elif self._model_problem:
            self.ask_page.show_note(
                f"{self._model_problem} Until then, search finds exact words only.", "Try again", "retry"
            )
        elif downloading or self.models.busy:
            self.ask_page.show_note(
                "Getting search by meaning ready"
                + (f" (downloading {downloading})" if downloading and downloading != "starting" else "")
                + ". Until then, search finds exact words only."
            )
        else:
            self.ask_page.show_note("")

    def closeEvent(self, e):  # noqa: N802 (Qt's name)
        self.closing.emit()
        self._update_timer.stop()  # (a closed window never checks)
        self.models.stop()
        self.watcher.shutdown()
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("state", self.saveState())
        self.settings.sync()  # written now: the app quits without waiting
        self.hide()
        super().closeEvent(e)
