"""The window: the sidebar switches pages, its switch stays put, the Info panel opens and closes, and the
layout is remembered."""

from conftest import open_window

from homedocs.ui.views.sidebar import ASK, LIBRARY


def test_the_first_time_it_opens_on_the_library_to_choose_folders(hd, app, tmp_path):
    w = open_window(app)
    try:
        assert w.stack.currentWidget() is w.library_page and w.library_page.stack.currentIndex() == 0
    finally:
        w.close()


def test_the_sidebar_switches_pages(hd, app, tmp_path):
    from homedocs.core import library

    library.add_folder(tmp_path)
    w = open_window(app)
    try:
        assert w.stack.currentWidget() is w.ask_page and w.page_title.text() == "Ask"
        w.sidebar.select(LIBRARY)  # quiet: no page change
        assert w.stack.currentWidget() is w.ask_page
        w.sidebar.chosen.emit(LIBRARY)
        assert w.stack.currentWidget() is w.library_page
        w.show_page(ASK)
        assert w.sidebar.current_place() == ASK
    finally:
        w.close()


def test_the_sidebar_switch_stays_put(hd, app):
    w = open_window(app)
    try:
        app.processEvents()
        at = w.sidebar_button.mapTo(w, w.sidebar_button.rect().topLeft())
        w.sidebar_action.trigger()
        app.processEvents()
        assert not w.sidebar_dock.isVisible()
        assert w.sidebar_button.mapTo(w, w.sidebar_button.rect().topLeft()) == at
    finally:
        w.close()


def test_the_info_panel_has_no_top_bar_button_and_closes_itself(hd, app):
    from PySide6.QtWidgets import QToolBar

    w = open_window(app)
    try:
        assert w.info_action not in w.findChild(QToolBar, "main").actions()
        w.info_action.trigger()  # (Ctrl+I)
        assert w.info_dock.isVisible()
        w.info.close_button.click()
        assert not w.info_dock.isVisible()
    finally:
        w.close()


def test_the_layout_is_remembered(hd, app):
    w = open_window(app)
    w.info_action.trigger()
    w.close()
    w = open_window(app)
    try:
        assert w.info_dock.isVisible()
        assert (hd.data_dir() / "window.ini").exists()
    finally:
        w.close()
