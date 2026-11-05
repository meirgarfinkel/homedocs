"""The Ask page: the question as its title, then the answer written from the passages found, each sentence
followed by the number of the passage that backs it; then the passages, each with its file, its place in the
file and the question's words highlighted. Clicking a number or a passage opens it in the Info panel."""

import html
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from ...core.answer import Answer
from ...core.search import Found, Result
from .. import theme
from .common import ElidedLabel

SNIPPET_CHARS = 240  # how much of a passage a result shows, about two lines (all of it in the Info panel)
COLUMN = 720  # the page's text column, at most


def marked(
    text: str, spans, start: int = 0, end: int | None = None, paragraphs: bool = False, strong: bool = False
) -> str:
    """text[start:end] as rich text, with spans (positions in text) highlighted. paragraphs: keep its line
    breaks (else every run of spaces is one space). strong: the sentence backing an answer (highlighted; the
    question's words are also in bold)."""
    end = len(text) if end is None else end
    out, at = [], start
    style = f"background-color:{_mark_colour()};" + ("" if strong else " font-weight:600;")
    for s, e in spans:
        if e <= start or s >= end or s < at:
            continue
        s, e = max(s, start), min(e, end)
        out.append(_plain(text[at:s], paragraphs))
        out.append(f'<span style="{style}">{html.escape(text[s:e])}</span>')
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
    c = theme.color("highlight")
    return f"rgba({c.red()},{c.green()},{c.blue()},{c.alphaF():.2f})"


def answer_html(a: Answer) -> str:
    """The answer as rich text: each backed sentence followed by its link, a raised n, each unbacked one marked."""
    muted = theme.color("muted").name()
    accent = theme.color("accentText").name()
    link = f"color:{accent}; text-decoration:none; font-weight:600;"
    out, at = [], 0
    for s in a.sentences:
        out.append(html.escape(a.text[at : s.end]))
        if s.backed:
            out.append(f'<sup><a href="source:{a.sentences.index(s)}" style="{link}">{s.passage + 1}</a></sup>')
        elif s.checked:
            out.append(f' <span style="color:{muted}; font-style:italic;">(not found in your files)</span>')
        at = s.end
    out.append(html.escape(a.text[at:]))
    return _spaced("".join(out).replace("\n", "<br>"))


def _spaced(body: str) -> str:
    return f'<p style="line-height:160%;">{body}</p>'


def answer_for_copying(a: Answer) -> str:
    """The answer as plain text with its sources listed under it."""
    used = sorted({s.passage for s in a.sentences if s.backed})
    text, at = [], 0
    for s in a.sentences:
        text.append(a.text[at : s.end] + (f" [{s.passage + 1}]" if s.backed else ""))
        at = s.end
    text.append(a.text[at:])
    lines = ["".join(text).strip()]
    if used:
        lines.append("")
        lines.append("Sources:")
        for i in used:
            r = a.passages[i]
            lines.append(f"[{i + 1}] {r.name}" + (f", {r.where()}" if r.where() else "") + f" ({r.path})")
    return "\n".join(lines)


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

    def __init__(self, r: Result, first: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("result")
        self.setProperty("first", first)  # the best one, a step lighter
        self.result = r
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(12)
        icon = QLabel()
        icon.setPixmap(theme.icon("doc", "accent").pixmap(18, 18))
        lay.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(2)
        top = QHBoxLayout()
        top.setSpacing(4)
        self.title = QLabel(r.name)
        self.title.setObjectName("resultTitle")
        top.addWidget(self.title)
        if r.where():
            dot = QLabel("·")
            dot.setObjectName("hint")
            top.addWidget(dot)
        self.where = ElidedLabel(r.where(), mode=Qt.TextElideMode.ElideLeft)  # (the nearest heading last: kept)
        self.where.setObjectName("hint")
        top.addWidget(self.where, 1)
        if r.offline:
            off = QLabel("Offline")
            off.setObjectName("tag")
            off.setToolTip("Its folder can't be reached right now")
            top.addWidget(off)
        words.addLayout(top)
        start, end = snippet_range(r)
        body = ("… " if start else "") + marked(r.text, r.spans, start, end) + (" …" if end < len(r.text) else "")
        self.body = QLabel(body)
        self.body.setObjectName("resultText")
        self.body.setTextFormat(Qt.TextFormat.RichText)
        self.body.setWordWrap(True)
        words.addWidget(self.body)
        lay.addLayout(words, 1)

    def mouseReleaseEvent(self, e):  # noqa: N802 (Qt's name)
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.result)
        super().mouseReleaseEvent(e)


class AnswerCard(QFrame):
    """The answer: "Reading the passages..." while the model reads, then the text as it's written, then the
    finished answer with its links. source(Answer, sentence index): a link was clicked."""

    source = Signal(object, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("answer")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 22)
        lay.setSpacing(0)
        top = QHBoxLayout()
        top.setSpacing(12)
        label = QLabel("Answer")
        label.setObjectName("answerLabel")
        self.copy_button = QPushButton("Copy")
        self.copy_button.setObjectName("copy")
        self.copy_button.setToolTip("Copy the answer with its sources")
        self.copy_button.clicked.connect(self._copy)
        top.addWidget(label)
        top.addWidget(self.copy_button)
        top.addStretch()
        lay.addLayout(top)
        lay.addSpacing(12)
        self.body = QLabel()
        self.body.setObjectName("answerText")
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.TextFormat.RichText)
        self.body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.body.linkActivated.connect(self._link)
        lay.addWidget(self.body)
        self.answer: Answer | None = None
        self.hide()

    def reading(self, count: int):
        self.answer = None
        self.copy_button.hide()
        self.body.setText(_spaced(f"<i>Reading the {count} best passage{'s' if count != 1 else ''}...</i>"))
        self.show()

    def writing(self, text: str):
        self.body.setText(_spaced(html.escape(text).replace("\n", "<br>")))

    def done(self, a: Answer, saved: bool = False):
        self.answer = a
        self.body.setText(answer_html(a))
        self.copy_button.setVisible(not a.not_found)
        self.show()

    def failed(self, message: str):
        self.answer = None
        self.copy_button.hide()
        self.body.setText(_spaced(html.escape(message)))
        self.show()

    def _link(self, href: str):
        if self.answer is not None and href.startswith("source:"):
            self.source.emit(self.answer, int(href.split(":")[1]))

    def _copy(self):
        if self.answer is not None:
            QGuiApplication.clipboard().setText(answer_for_copying(self.answer))


class AskPage(QWidget):
    """opened(Result): a result was clicked; choose_folders(): the note's button, with no folders yet;
    retry(): the note's button after the models failed to download."""

    opened = Signal(object)
    source = Signal(object, int)  # (Answer, sentence index): an answer's link was clicked
    choose_folders = Signal()
    retry = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("results")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list = QWidget()
        self.list.setObjectName("resultList")
        page = QHBoxLayout(self.list)
        page.setContentsMargins(56, 40, 56, 24)
        column = QWidget()
        column.setMaximumWidth(COLUMN)
        page.addWidget(column, 1)
        page.addStretch(0)
        self.list_lay = QVBoxLayout(column)
        self.list_lay.setContentsMargins(0, 0, 0, 0)
        self.list_lay.setSpacing(6)
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
        self.list_lay.addWidget(self.note)
        self.status = QLabel("Ask a question about your documents in the box above.")
        self.status.setObjectName("hint")
        self.status.setWordWrap(True)
        self.list_lay.addWidget(self.status)
        self.title = QLabel()  # the question
        self.title.setObjectName("questionTitle")
        self.title.setWordWrap(True)
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.title.hide()
        self.list_lay.addWidget(self.title)
        self.list_lay.addSpacing(6)
        self.answer = AnswerCard()
        self.answer.source.connect(self.source)
        self.list_lay.addWidget(self.answer)
        self.heading = QLabel()
        self.heading.setObjectName("sectionTitle")
        self.heading.hide()
        self.list_lay.addWidget(self.heading)
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
        self.answer.hide()
        self.title.setText(question)
        self.title.show()
        self.heading.hide()
        self.status.setText("Searching...")
        self.status.show()

    def show_found(self, question: str, found: Found, asked: str | None = None):
        """The passages found (asked: when a saved question was asked, shown instead of the search time)."""
        self.question = question
        for c in self.cards:
            c.deleteLater()
        self.cards = []
        self.title.setText(question)
        self.title.show()
        n = len(found.results)
        self.status.setVisible(not n)
        self.heading.setVisible(bool(n))
        if not n:
            self.status.setText("Nothing in your files matches that. Try other words, or check the Library.")
        else:
            files = len({r.path for r in found.results})
            how = "" if found.by_meaning else " · by keyword only, until search by meaning is ready"
            when = f" · asked {asked}" if asked else ""
            self.heading.setText(f"Passages · {n} from {files} file{'s' if files != 1 else ''}{how}{when}")
        for i, r in enumerate(found.results):
            card = ResultCard(r, first=i == 0)
            card.clicked.connect(self.opened)
            self.list_lay.insertWidget(self.list_lay.count() - 1, card)
            self.cards.append(card)
        self.scroll.verticalScrollBar().setValue(0)

    def show_error(self, message: str):
        self.heading.hide()
        self.status.setText(message)
        self.status.show()
