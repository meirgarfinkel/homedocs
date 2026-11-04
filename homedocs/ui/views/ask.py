"""The Ask page: the passages that best answer the question asked in the toolbar's box, each with its file,
its place in the file and the question's words highlighted. Clicking one opens it in the Info panel.

(The answer itself, written from these passages, joins the page in the next milestone.)"""

import html
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from ...core.search import Found, Result
from .. import theme
from .common import ElidedLabel

SNIPPET_CHARS = 420  # how much of a passage a result shows (all of it in the Info panel)


def marked(text: str, spans, start: int = 0, end: int | None = None, paragraphs: bool = False) -> str:
    """text[start:end] as rich text, with spans (positions in text) highlighted. paragraphs: keep its line
    breaks (else every run of spaces is one space)."""
    end = len(text) if end is None else end
    out, at = [], start
    for s, e in spans:
        if e <= start or s >= end:
            continue
        s, e = max(s, start), min(e, end)
        out.append(_plain(text[at:s], paragraphs))
        out.append(f'<span style="background-color:{_mark_colour()}; font-weight:600;">{html.escape(text[s:e])}</span>')
        at = e
    out.append(_plain(text[at:end], paragraphs))
    return "".join(out)


def _plain(text: str, paragraphs: bool) -> str:
    text = html.escape(text)
    if paragraphs:  # a blank line is a new paragraph; a line break before a list item, table row or quote stays
        text = re.sub(r"\n[ \t]*\n\s*", "<br><br>", text)
        text = re.sub(r"\n(?=\s*(?:[-*•|#]|&gt;|\d+[.)]\s))", "<br>", text)
        return re.sub(r"\s*\n\s*", " ", text)
    return re.sub(r"\s+", " ", text)


def _mark_colour() -> str:
    c = theme.color("accent")
    return f"rgba({c.red()},{c.green()},{c.blue()},0.28)"


def snippet_range(r: Result) -> tuple[int, int]:
    """The part of a passage a result shows: from the sentence with the first highlighted word (or the start),
    about SNIPPET_CHARS long."""
    text = r.text
    if len(text) <= SNIPPET_CHARS:
        return 0, len(text)
    start = 0
    if r.spans and r.spans[0][0] > SNIPPET_CHARS // 2:
        before = text.rfind(". ", 0, r.spans[0][0])
        start = before + 2 if before != -1 and r.spans[0][0] - before < SNIPPET_CHARS // 2 else r.spans[0][0] - 60
        start = max(0, text.find(" ", start) + 1 if start > 0 and not text[start - 1].isspace() else start)
    end = min(len(text), start + SNIPPET_CHARS)
    if end < len(text):
        space = text.rfind(" ", start, end)
        end = space if space > start else end
    return start, end


class ResultCard(QFrame):
    clicked = Signal(object)

    def __init__(self, r: Result, parent=None):
        super().__init__(parent)
        self.setObjectName("result")
        self.result = r
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 14)
        lay.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.title = QLabel(r.name)
        self.title.setObjectName("resultTitle")
        self.where = ElidedLabel(r.where(), mode=Qt.TextElideMode.ElideLeft)  # (the nearest heading last: kept)
        self.where.setObjectName("hint")
        top.addWidget(self.title)
        top.addWidget(self.where, 1)
        if r.offline:
            off = QLabel("Offline")
            off.setObjectName("tag")
            off.setToolTip("Its folder can't be reached right now")
            top.addWidget(off)
        lay.addLayout(top)
        start, end = snippet_range(r)
        body = ("… " if start else "") + marked(r.text, r.spans, start, end) + (" …" if end < len(r.text) else "")
        self.body = QLabel(body)
        self.body.setObjectName("resultText")
        self.body.setTextFormat(Qt.TextFormat.RichText)
        self.body.setWordWrap(True)
        lay.addWidget(self.body)

    def mouseReleaseEvent(self, e):  # noqa: N802 (Qt's name)
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.result)
        super().mouseReleaseEvent(e)


class AskPage(QWidget):
    """opened(Result): a result was clicked; choose_folders(): the note's button, with no folders yet;
    retry(): the note's button after the models failed to download."""

    opened = Signal(object)
    choose_folders = Signal()
    retry = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 8, 24, 0)
        lay.setSpacing(10)
        self.note = QFrame()
        self.note.setObjectName("note")
        note = QHBoxLayout(self.note)
        note.setContentsMargins(14, 10, 10, 10)
        self.note_text = QLabel()
        self.note_text.setWordWrap(True)
        self.note_button = QPushButton()
        self.note_button.clicked.connect(self._note_clicked)
        note.addWidget(self.note_text, 1)
        note.addWidget(self.note_button)
        self.note.hide()
        lay.addWidget(self.note)
        self.status = QLabel("Ask a question about your documents in the box above.")
        self.status.setObjectName("hint")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("results")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list = QWidget()
        self.list.setObjectName("resultList")
        self.list_lay = QVBoxLayout(self.list)
        self.list_lay.setContentsMargins(0, 0, 0, 16)
        self.list_lay.setSpacing(10)
        self.list_lay.addStretch()
        self.scroll.setWidget(self.list)
        lay.addWidget(self.scroll, 1)
        self._note_action = None
        self.cards: list[ResultCard] = []
        self.question = ""

    # --- the note at the top ------------------------------------------------------------------------

    def show_note(self, text: str, button: str = "", action=None):
        """A line about what's not ready yet (and a button), or no note with text ""."""
        self.note_text.setText(text)
        self.note_button.setText(button)
        self.note_button.setVisible(bool(button))
        self._note_action = action
        self.note.setVisible(bool(text))

    def _note_clicked(self):
        if self._note_action == "folders":
            self.choose_folders.emit()
        elif self._note_action == "retry":
            self.retry.emit()

    # --- results ------------------------------------------------------------------------------------

    def searching(self, question: str):
        self.question = question
        self.status.setText("Searching...")

    def show_found(self, question: str, found: Found):
        self.question = question
        for c in self.cards:
            c.deleteLater()
        self.cards = []
        n = len(found.results)
        if not n:
            self.status.setText("Nothing in your files matches that. Try other words, or check the Library.")
        else:
            files = len({r.path for r in found.results})
            how = "" if found.by_meaning else " (by keyword only, until search by meaning is ready)"
            self.status.setText(
                f"{n} passage{'s' if n != 1 else ''} from {files} file{'s' if files != 1 else ''}, "
                f"best first{how} · {found.seconds:.1f} s"
            )
        for r in found.results:
            card = ResultCard(r)
            card.clicked.connect(self.opened)
            self.list_lay.insertWidget(self.list_lay.count() - 1, card)
            self.cards.append(card)
        self.scroll.verticalScrollBar().setValue(0)

    def show_error(self, message: str):
        self.status.setText(message)
