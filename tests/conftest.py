"""Shared test setup: a safety net so no test touches real data, a data folder per test, and helpers
for the window."""

import os
import tempfile
import time
from types import SimpleNamespace

import pytest

# A safety net under every test: nothing reaches the real data folder (~/.local/share/homedocs), even
# a test that forgets the hd fixture or undoes its setting. Tests that need one get their own (hd).
_SAFE = tempfile.mkdtemp(prefix="homedocs-tests-")
os.environ["HOMEDOCS_HOME"] = os.path.join(_SAFE, "data")
os.environ["XDG_DATA_HOME"] = os.path.join(_SAFE, "share")


@pytest.fixture
def hd(tmp_path, monkeypatch):
    """homedocs with its data folder in tmp_path. (Fake models join here as they arrive.)"""
    monkeypatch.setenv("HOMEDOCS_HOME", str(tmp_path / "data"))
    from homedocs.core import config, db

    return SimpleNamespace(data_dir=config.data_dir, open_db=db.open_db, DatabaseVersionError=db.DatabaseVersionError)


# --- the app ---------------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def wait_until(app, cond, timeout=20.0):
    """Let the app run until cond() is true (fails the test if it isn't within timeout seconds)."""
    end = time.time() + timeout
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    assert cond()


def open_window(app, ready=lambda w: w.loaded, size=(1000, 700)):
    """The main window, shown, once ready(window) is true (ready=None: at once)."""
    from homedocs.ui.main_window import MainWindow

    w = MainWindow()
    w.resize(*size)
    w.show()
    if ready is not None:
        wait_until(app, lambda: ready(w))
    return w
