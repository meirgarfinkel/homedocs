"""The Library page: choosing the first folder, the files appearing as they're read with their status,
the filters, and removing a folder."""

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
        (row,) = [lib.folder_list.itemAt(i).widget() for i in range(lib.folder_list.count())]
        assert row.path.text() == str(folder) and row.count.text() == "3 files"
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


def test_removing_a_folder_forgets_its_files(hd, app, folder):
    w = open_window(app)
    lib = w.library_page
    try:
        lib.add(str(folder))
        wait_until(app, lambda: len(rows(lib)) == 3)
        lib.folder_list.itemAt(0).widget().remove.click()
        assert lib.stack.currentIndex() == 0
        wait_until(app, lambda: not w.watcher.running() and lib.model.rows == [])
        assert folder.joinpath("notes.md").exists()
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


def test_progress_shows_in_the_status_pill(hd, app, folder):
    w = open_window(app)
    try:
        seen = []
        w.watcher.state.connect(lambda s: seen.append(w.background.label.text()))
        w.library_page.add(str(folder))
        wait_until(app, lambda: seen and not w.watcher.running())
        wait_until(app, lambda: w.background.isHidden() or not w.background.label.text())
        assert any(t.startswith(("Reading files", "Looking for")) for t in seen)
    finally:
        w.close()
