"""The toolbar's menu: one list (no submenus), rows that say what they do, and the button's lines
turning into an X while it's open."""

from conftest import open_window, wait_until
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QToolBar, QWidgetAction


def _rows(menu):
    return {
        a.defaultWidget().title.text(): a.defaultWidget()
        for a in menu.actions()
        if isinstance(a, QWidgetAction) and hasattr(a.defaultWidget(), "title")
    }


def test_one_list_with_described_rows(hd, app):
    w = open_window(app)
    try:
        m = w.more_menu
        assert not any(a.menu() for a in m.actions())  # no submenus
        rows = _rows(m)
        assert list(rows) == ["Library", "Settings...", "Check for updates", "Quit"]
        assert [t for t, r in rows.items() if r.description.text()] == ["Library"]
        assert all(not r.icon.pixmap().isNull() for r in rows.values())  # an icon on every row
        assert rows["Settings..."].right.text()  # its shortcut
        rows["Library"].choose()
        assert w.stack.currentWidget() is w.library_page and w.page_title.text() == "Library"
    finally:
        w.close()


def test_settings_only_in_the_menu(hd, app):
    w = open_window(app)
    try:
        toolbar = w.findChild(QToolBar, "main")
        settings = next(a for a in w.actions() if a.text() == "Settings...")
        assert settings not in toolbar.actions()  # Ctrl+, still works from anywhere in the window
    finally:
        w.close()


def test_nothing_lit_until_pointed_at(hd, app):
    w = open_window(app)
    try:
        m = w.more_menu
        rows = _rows(m)
        QTimer.singleShot(50, m.close)
        w.more_button.showMenu()
        assert not any(r.property("active") for r in rows.values())
        m.setActiveAction(rows["Library"].item)  # (what pointing at it, or the arrow keys, does)
        assert [t for t, r in rows.items() if r.property("active")] == ["Library"]
    finally:
        w.close()


def test_lines_turn_into_an_x(hd, app):
    w = open_window(app)
    try:
        button, seen = w.more_button, []

        def look():
            seen.append(button.progress)
            if button.progress < 1:
                QTimer.singleShot(20, look)
            else:
                w.more_menu.close()

        QTimer.singleShot(0, look)
        button.showMenu()  # (returns once the menu closes)
        assert seen[-1] == 1.0, seen
        wait_until(app, lambda: button.progress == 0.0)
    finally:
        w.close()
