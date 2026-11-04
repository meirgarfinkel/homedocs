"""Starting the desktop app.

Only one runs at a time: opening it again brings the open window forward. Started from the
applications menu (no terminal), its output goes to app.log in the data folder.
`--self-test` checks the parts a build can lose (the font, Qt's plugins, the window) and exits 0 if
they all work.
"""

import atexit
import getpass
import hashlib
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from .. import __version__
from ..core.config import data_dir
from . import workers

ICON = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / "homedocs-icon.png"


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    self_test = "--self-test" in argv
    # from the applications menu there's no terminal (on Windows, no output at all): keep a log
    if sys.stdout is None or (not self_test and not sys.stdout.isatty()):
        log = open(data_dir() / "app.log", "a", buffering=1, encoding="utf-8")  # noqa: SIM115 (open while the app runs)
        sys.stdout = sys.stderr = log
        print(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} HomeDocs {__version__}")

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(argv)
    app.setApplicationName("HomeDocs")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("homedocs")
    app.setDesktopFileName("homedocs")
    if ICON.is_file():
        app.setWindowIcon(QIcon(str(ICON)))
    from . import theme

    theme.apply(app)

    if self_test:
        return run_self_test(app)
    server = single_instance()
    if server is None:
        return 0  # it was already open: that window has been brought forward
    add_menu_entry()

    from .main_window import MainWindow

    win = MainWindow()
    server.newConnection.connect(lambda: _listen(server.nextPendingConnection(), win))
    win.closing.connect(server.close)  # a launch from now on opens a new window, not this closing one
    win.show()
    code = app.exec()
    _exit(code)


def _exit(code):
    """Leave without waiting long for background work (indexing and the database are safe to stop at
    any point)."""
    workers.wait_idle(10)
    atexit._run_exitfuncs()  # tidy-ups registered with atexit (os._exit skips them)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


# --- one at a time -----------------------------------------------------------------------------------


def _server_name() -> str:
    where = hashlib.sha1(str(data_dir()).encode()).hexdigest()[:10]  # one per data folder
    return f"homedocs-{getpass.getuser()}-{where}"


def single_instance():
    """A local server other launches can find, or None if HomeDocs is already open (it's then
    told to show itself). A copy that doesn't answer is closing (or stuck): it's waited for, and asked
    again, for up to 30 seconds."""
    name = _server_name()
    end = time.time() + 30
    while time.time() < end:
        said = _ask_open_copy(name)
        if said == "answered":
            return None
        if said == "none":
            break
    server = QLocalServer()
    if not server.listen(name):
        QLocalServer.removeServer(name)  # left over from one that crashed (or from one stuck)
        server.listen(name)
    return server


def _ask_open_copy(name) -> str:
    """Ask an open copy to show itself: "answered", "silent" or "none" (no copy open)."""
    probe = QLocalSocket()
    probe.connectToServer(name)
    if not probe.waitForConnected(500):
        return "none"
    if sys.platform == "win32":  # let the open window come to the front (else it only flashes)
        import ctypes

        ctypes.windll.user32.AllowSetForegroundWindow(-1)
    probe.write(b"\0")  # "show yourself"; the open window answers once it has it
    probe.waitForBytesWritten(1000)
    answered = probe.waitForReadyRead(3000)
    probe.abort()
    return "answered" if answered else "silent"


def _listen(sock, win):
    """Another launch: show the window."""
    handled = []

    def read():
        if b"\0" in bytes(sock.readAll()) and not handled:
            handled.append(True)
            sock.write(b"ok")  # it can go now
            sock.flush()
            win.bring_forward()

    def gone():
        read()
        sock.deleteLater()

    sock.readyRead.connect(read)
    sock.disconnected.connect(gone)
    if sock.bytesAvailable():
        read()
    if sock.state() == QLocalSocket.LocalSocketState.UnconnectedState:  # it hung up already
        gone()


def add_menu_entry():
    """Running as an AppImage: put HomeDocs in the applications menu (once, or after it moved)."""
    appimage = os.environ.get("APPIMAGE")
    if not appimage or not ICON.is_file():
        return
    try:
        share = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
        icon = data_dir() / "homedocs-icon.png"
        icon.write_bytes(ICON.read_bytes())
        entry = share / "applications" / "homedocs.desktop"
        text = (
            "[Desktop Entry]\nType=Application\nName=HomeDocs\n"
            "Comment=Ask questions about your documents, offline\n"
            f'Exec="{appimage}"\nIcon={icon}\nTerminal=false\n'
            "Categories=Office;Utility;\nKeywords=documents;search;questions;pdf;\n"
        )
        if not entry.exists() or entry.read_text() != text:
            entry.parent.mkdir(parents=True, exist_ok=True)
            entry.write_text(text)
    except OSError as e:
        print(f"Couldn't add a menu entry: {e!r}", flush=True)


# --- self-test ---------------------------------------------------------------------------------------


def run_self_test(app) -> int:
    def ok(what):
        print(f"SELF-TEST: {what}", flush=True)

    try:
        from . import theme

        if app.font().family() != theme.FAMILY:
            raise RuntimeError(f"the app's font is missing from this build ({theme.FONT_DIR})")
        ok("the app's font loads.")
        from ..core.db import open_db

        db = open_db()
        db.execute("SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH 'test'").fetchone()
        db.close()
        ok("the database opens, with its keyword index (SQLite's FTS5).")

        import io

        import pypdfium2 as pdfium

        made, out = pdfium.PdfDocument.new(), io.BytesIO()
        made.new_page(200, 200)
        made.save(out)
        made.close()
        pdf = pdfium.PdfDocument(out.getvalue())
        pdf[0].get_textpage().get_text_bounded()
        pdf.close()
        ok("PDFs can be read (PDFium loads).")

        from .main_window import MainWindow

        win = MainWindow()
        win.show()
        end = time.time() + 20
        while time.time() < end and not win.loaded:
            app.processEvents()
            time.sleep(0.02)
        if not win.loaded:
            raise RuntimeError("the window didn't finish loading")
        if win.grab().isNull():
            raise RuntimeError("the window couldn't be drawn")
        QTimer.singleShot(0, win.close)
        app.processEvents()
    except Exception as e:
        print(f"SELF-TEST FAILED: {e!r}", flush=True)
        return 1
    print("SELF-TEST OK: the window opened.", flush=True)
    return 0
