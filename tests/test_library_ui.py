"""The Library page: choosing the first folder, the files appearing as they're read with their status,
the filters, leaving out and taking back; Settings' folders; and the status line under the sidebar."""

import os

import pytest
from conftest import open_window, wait_until
from samples import make_pdf

from homedocs.ui.views import library as page


@pytest.fixture
def folder(tmp_path):
    root = tmp_path / "docs"
    (root / "taxes").mkdir(parents=True)
    make_pdf(root / "taxes" / "2025 return.pdf", ["Total income was forty thousand."])
    make_pdf(root / "scan.pdf", [""])  # no text: can't be read
    (root / "notes.md").write_text("# Notes\n\nCall the plumber.\n")
    return root


def rows(lib):
    """What the table shows: name -> status, in its order."""
    p = lib.proxy
    return {p.index(i, page.NAME).data(): p.index(i, page.STATUS).data() for i in range(p.rowCount())}


def test_choosing_a_folder_reads_its_files(hd, app, folder):
    w = open_window(app)
    lib = w.library_page
    try:
        assert lib.stack.currentIndex() == 0 and lib.choose.isVisible()  # nothing chosen yet
        lib.add(str(folder))
        assert lib.stack.currentIndex() == 1
        wait_until(app, lambda: len(rows(lib)) == 3 and "Waiting to be read" not in rows(lib).values())
        r = rows(lib)
        assert r["2025 return.pdf"] == "Read" and r["notes.md"] == "Read"
        assert "scanned image" in r["scan.pdf"]
        assert w.count_label.text() == "3 files"
        i = next(i for i in range(lib.proxy.rowCount()) if lib.proxy.index(i, 0).data() == "2025 return.pdf")
        assert lib.proxy.index(i, page.FOLDER).data() == os.path.join("docs", "taxes")
        assert lib.proxy.index(i, page.PASSAGES).data() == "1"
    finally:
        w.close()


def test_filters_show_one_kind_of_file(hd, app, folder):
    w = open_window(app)
    lib = w.library_page
    try:
        lib.add(str(folder))
        wait_until(app, lambda: len(rows(lib)) == 3 and "Waiting to be read" not in rows(lib).values())
        assert lib.filter_buttons["failed"].text().endswith("1") and not lib.filter_buttons["offline"].isVisible()
        lib.filter_buttons["failed"].click()
        assert list(rows(lib)) == ["scan.pdf"]
        lib.filter_buttons["all"].click()
        lib.find.setText("return")
        assert list(rows(lib)) == ["2025 return.pdf"]
    finally:
        w.close()


def test_settings_removes_a_folder_only_when_confirmed(hd, app, folder, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from homedocs.ui.views.settings_dialog import SettingsDialog

    asked = []
    answer = [QMessageBox.StandardButton.Cancel]
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a: (asked.append(a[2]), answer[0])[1]))
    w = open_window(app)
    lib = w.library_page
    try:
        lib.add(str(folder))
        wait_until(app, lambda: len(rows(lib)) == 3 and not w.watcher.running())
        d = SettingsDialog(w)
        d.folders_changed.connect(w.folders_changed)
        wait_until(app, lambda: "3 files" in d.folder_list.item(0).text())
        d.folder_list.item(0).setSelected(True)
        d.remove_button.click()
        assert "3 files leave the search" in asked[0] and d.folder_list.count() == 1  # cancelled
        answer[0] = QMessageBox.StandardButton.Yes
        d.remove_button.click()
        assert d.folder_list.count() == 0 and lib.stack.currentIndex() == 0
        wait_until(app, lambda: not w.watcher.running() and lib.model.rows == [])
        assert folder.joinpath("notes.md").exists()
        d.close()
    finally:
        w.close()


def test_leaving_out_and_taking_back_from_the_library(hd, app, folder):
    w = open_window(app)
    lib = w.library_page
    try:
        lib.add(str(folder))
        wait_until(app, lambda: len(rows(lib)) == 3 and not w.watcher.running())
        lib.leave_out(str(folder / "taxes"))
        wait_until(app, lambda: not w.watcher.running() and "2025 return.pdf" not in rows(lib))
        assert rows(lib)["taxes"].startswith("Left out") and w.count_label.text() == "2 files"
        lib.filter_buttons["left_out"].click()
        assert list(rows(lib)) == ["taxes"]
        lib.filter_buttons["all"].click()
        lib.take_back(str(folder / "taxes"))
        wait_until(app, lambda: rows(lib).get("2025 return.pdf") == "Read")
    finally:
        w.close()


def test_a_folder_inside_one_already_searched_isnt_added(hd, app, folder, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    said = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda parent, title, text: said.append(text)))
    w = open_window(app)
    try:
        w.library_page.add(str(folder))
        w.library_page.add(str(folder / "taxes"))
        assert "already searched" in said[0]
    finally:
        w.close()


def test_progress_shows_under_the_sidebar_in_a_few_words(hd, app, folder):
    w = open_window(app)
    try:
        seen = []
        w.watcher.state.connect(lambda s: seen.append(w.status_line.text))
        w.library_page.add(str(folder))
        wait_until(app, lambda: seen and not w.watcher.running())
        wait_until(app, lambda: not w.status_line.text)
        assert any(t.startswith(("Indexing", "Checking")) for t in seen)
        assert w.sidebar_dock.isAncestorOf(w.status_line)
    finally:
        w.close()


def test_with_the_sidebar_closed_the_top_bar_shows_the_status(hd, app):
    w = open_window(app)
    try:
        w.sidebar_action.trigger()
        w.statusBar().showMessage("Copied")
        assert w.indexed.text() == "Copied"
        w.statusBar().clearMessage()
        assert w.indexed.text() != "Copied"
    finally:
        w.close()


def test_the_short_words():
    from homedocs.ui.indexing import brief

    assert brief({"running": True, "phase": "reading", "done": 312, "todo": 1204}) == "Indexing 312 of 1,204"
    assert brief({"running": True, "phase": "embedding", "done": 40, "todo": 100}) == "Finishing up · 40%"
    assert brief({"running": True, "phase": "scanning", "done": 0, "todo": 0}) == "Checking for changes"
    assert brief({"running": False, "phase": "idle"}) == ""


def test_sorting_by_a_column(hd, app, folder):
    w = open_window(app)
    lib = w.library_page
    try:
        lib.add(str(folder))
        wait_until(app, lambda: len(rows(lib)) == 3)
        lib.table.sortByColumn(page.NAME, page.Qt.SortOrder.AscendingOrder)
        assert list(rows(lib)) == ["2025 return.pdf", "notes.md", "scan.pdf"]
        lib.table.sortByColumn(page.NAME, page.Qt.SortOrder.DescendingOrder)
        assert list(rows(lib)) == ["scan.pdf", "notes.md", "2025 return.pdf"]
    finally:
        w.close()


def test_the_hidden_library_waits_to_be_shown_before_reading_the_files(hd, app, folder, monkeypatch):
    w = open_window(app)
    lib = w.library_page
    try:
        lib.add(str(folder))
        wait_until(app, lambda: len(rows(lib)) == 3 and not w.watcher.running())
        w.show_page("ask")
        reads = []
        monkeypatch.setattr(page, "_read_rows", lambda *a: reads.append(1) or [])
        lib.reload()  # (as indexing asks, every few seconds)
        app.processEvents()
        assert reads == []
        w.show_page("library")
        wait_until(app, lambda: reads)
    finally:
        w.close()
