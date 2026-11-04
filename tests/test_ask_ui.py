"""The Ask page: asking shows the best passages with the question's words highlighted; clicking one opens it
in the Info panel; a note says what's missing (folders, the models) and offers to fix it."""

import pytest
from conftest import open_window, wait_until

from homedocs.core import library


@pytest.fixture
def folder(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    (root / "car.md").write_text("# Car insurance\n\nThe deductible is five hundred dollars per claim.\n")
    (root / "wifi.md").write_text("# Wifi\n\nThe router password is on the sticker.\n")
    return root


def ready(w):
    """Loaded, models in, every passage embedded."""
    return w.loaded and w.models.loaded and not w.watcher.running() and w.searcher.index is not None


def test_asking_shows_the_best_passages(hd, app, folder):
    library.add_folder(folder)
    w = open_window(app)
    try:
        wait_until(app, lambda: ready(w) and len(w.searcher.index) == 2)
        w.ask_box.setText("what is my deductible?")
        w.ask_box.returnPressed.emit()
        page = w.ask_page
        wait_until(app, lambda: page.cards)
        first = page.cards[0]
        assert first.title.text() == "car.md" and first.where.text() == "Car insurance"
        assert "background-color" in first.body.text() and ">deductible<" in first.body.text()
        assert page.status.text().startswith("2 passages from 2 files") or page.status.text().startswith("1 passage")
        assert not page.note.isVisible()
        first.clicked.emit(first.result)
        assert w.info_dock.isVisible() and w.info.title.text() == "car.md"
        assert w.info.path.text().endswith("car.md") and w.info.open_button.isVisible()
    finally:
        w.close()


def test_nothing_found_says_so(hd, app, folder, monkeypatch):
    from homedocs.ui import models_ui

    monkeypatch.setattr(models_ui.ModelLoader, "start", lambda self: None)  # keyword search only
    library.add_folder(folder)
    w = open_window(app)
    try:
        wait_until(app, lambda: not w.watcher.running())
        w.ask("zebra crossings")
        wait_until(app, lambda: w.ask_page.status.text() != "Searching...")
        assert w.ask_page.status.text().startswith("Nothing in your files matches that")
        assert w.ask_page.cards == []
    finally:
        w.close()


def test_without_folders_the_note_offers_to_choose_one(hd, app):
    w = open_window(app)
    try:
        w.show_page("ask")
        assert w.ask_page.note.isVisible() and w.ask_page.note_button.text() == "Choose a folder"
    finally:
        w.close()


def test_a_failed_download_offers_to_try_again(hd, app, folder, monkeypatch):
    from homedocs.core import models
    from homedocs.ui import models_ui

    library.add_folder(folder)
    monkeypatch.setattr(models.Model, "ready", lambda self: False)

    def no_connection(*a, **k):
        raise models.DownloadError("Couldn't download the models: no connection.")

    monkeypatch.setattr(models_ui.models, "download", no_connection)
    w = open_window(app)
    try:
        page = w.ask_page
        wait_until(app, lambda: page.note_button.text() == "Try again")
        assert "no connection" in page.note_text.text() and "exact words" in page.note_text.text()
        w.ask("router password")  # keyword search still works
        wait_until(app, lambda: page.cards)
        assert page.cards[0].title.text() == "wifi.md" and "keyword only" in page.status.text()
        monkeypatch.setattr(models.Model, "ready", lambda self: True)
        page.note_button.click()
        wait_until(app, lambda: w.models.loaded)
        assert not page.note.isVisible()
    finally:
        w.close()
