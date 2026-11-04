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


def indexed(hd) -> int:
    db = hd.open_db()
    try:
        return db.execute("SELECT COUNT(*) FROM files WHERE status = 'indexed'").fetchone()[0]
    finally:
        db.close()


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
        wait_until(app, lambda: indexed(hd) == 2 and not w.watcher.running())  # (slow on Windows CI)
        w.ask("router password")  # keyword search still works
        wait_until(app, lambda: page.cards)
        assert page.cards[0].title.text() == "wifi.md" and "keyword only" in page.status.text()
        monkeypatch.setattr(models.Model, "ready", lambda self: True)
        page.note_button.click()
        wait_until(app, lambda: w.models.loaded)
        assert not page.note.isVisible()
    finally:
        w.close()


# --- answers ---------------------------------------------------------------------------------------------


def answered(page):
    return page.answer.isVisible() and page.answer.answer is not None


def test_an_answer_with_links_to_its_sources(hd, app, folder):
    library.add_folder(folder)
    w = open_window(app)
    try:
        wait_until(app, lambda: ready(w) and len(w.searcher.index) == 2 and w.models.answers)
        w.ask("what is my deductible?")
        page = w.ask_page
        wait_until(app, lambda: answered(page))
        a = page.answer.answer
        assert a.text == "The deductible is five hundred dollars per claim."
        assert 'href="source:0"' in page.answer.body.text() and "1 of 1 sentences" in page.answer.info.text()
        page.answer._link("source:0")
        assert w.info_dock.isVisible() and w.info.title.text() == "car.md"
        assert "0.45" in w.info.text.text()  # (the backing sentence, highlighted)
        wait_until(app, lambda: w.sidebar._recent)
        assert w.sidebar._recent[1].text(0) == "what is my deductible?"
    finally:
        w.close()


def test_a_recent_question_reopens_without_the_model(hd, app, folder):
    library.add_folder(folder)
    w = open_window(app)
    try:
        wait_until(app, lambda: ready(w) and len(w.searcher.index) == 2 and w.models.answers)
        page = w.ask_page
        w.ask("what is my deductible?")
        wait_until(app, lambda: answered(page) and len(w.sidebar._recent) == 2)
        w.ask("router password")
        wait_until(app, lambda: answered(page) and "router" in page.answer.answer.text and len(w.sidebar._recent) == 3)
        asked = len(w.answerer._llm.prompts)
        old = next(i for i in w.sidebar._recent[1:] if "deductible" in i.text(0))
        w.sidebar.setCurrentItem(old)
        wait_until(app, lambda: "deductible" in page.answer.answer.text)
        assert w.ask_box.text() == "what is my deductible?" and "asked" in page.status.text()
        assert page.cards[0].title.text() == "car.md" and 'href="source:0"' in page.answer.body.text()
        assert len(w.answerer._llm.prompts) == asked  # (the model wasn't asked again)
    finally:
        w.close()


def test_a_new_question_stops_the_answer_being_written(hd, app, folder, monkeypatch):
    from fakes import FakeLLM

    from homedocs.core import models

    slow = FakeLLM(delay=0.2)
    monkeypatch.setattr(models, "load_chat", lambda model=None: slow)
    library.add_folder(folder)
    w = open_window(app)
    try:
        wait_until(app, lambda: ready(w) and len(w.searcher.index) == 2 and w.models.answers)
        page = w.ask_page
        w.ask("what is my deductible?")
        wait_until(app, lambda: "The" in page.answer.body.text())  # (being written)
        slow.delay = 0.0
        w.ask("router password")
        wait_until(app, lambda: answered(page))
        assert "router" in page.answer.answer.text
        wait_until(app, lambda: len(w.sidebar._recent) == 2)  # (only the finished answer was kept)
    finally:
        w.close()


def test_a_failed_chat_download_offers_to_try_again(hd, app, folder, monkeypatch):
    from homedocs.core import models
    from homedocs.ui import models_ui

    library.add_folder(folder)
    monkeypatch.setattr(models.Model, "ready", lambda self: self is not models.CHAT)

    def no_connection(*a, **k):
        raise models.DownloadError("Couldn't download the answer model: no connection.")

    monkeypatch.setattr(models_ui.models, "download", no_connection)
    w = open_window(app)
    try:
        page = w.ask_page
        wait_until(app, lambda: page.note_button.text() == "Try again" and "answer model" in page.note_text.text())
        assert "passages without an answer" in page.note_text.text() and not w.models.answers
        monkeypatch.setattr(models.Model, "ready", lambda self: True)
        page.note_button.click()
        wait_until(app, lambda: w.models.answers)
        assert not page.note.isVisible()
    finally:
        w.close()


def test_without_llama_cpp_there_are_passages_and_no_answer(hd, app, folder, monkeypatch):
    from homedocs.core import llm

    monkeypatch.setattr(llm, "available", lambda: False)
    library.add_folder(folder)
    w = open_window(app)
    try:
        wait_until(app, lambda: ready(w) and len(w.searcher.index) == 2)
        w.ask("what is my deductible?")
        wait_until(app, lambda: w.ask_page.cards)
        assert not w.ask_page.answer.isVisible() and not w.ask_page.note.isVisible()
    finally:
        w.close()
