"""Splitting a file's text into passages: the pieces that are searched, and given to the model to answer
from.

A passage is up to PASSAGE_WORDS words. When it's full, it ends at the last paragraph end past
MIN_PASSAGE_WORDS; failing that (one long paragraph) between sentences, else between words. The last
OVERLAP_WORDS words or so (whole sentences) start the next passage too, at every cut: a paragraph often
leans on the one before it ("This limit applies..."). A heading starts a new passage (once the current one has
some text), and never ends one. Each passage keeps its page, the headings it's under, and where it starts
and ends in the text, so it can be shown and highlighted later.
"""

import bisect
import re
from dataclasses import dataclass

from .config import MAX_PASSAGE_CHARS, MIN_PASSAGE_WORDS, MIN_SECTION_WORDS, OVERLAP_WORDS, PASSAGE_WORDS
from .parsers import Doc

_PARAGRAPHS = re.compile(r"\n[ \t]*\n\s*")  # a blank line
_SENTENCE_END = re.compile(r"[.!?…]+[\"'”’)\]]*\s+|\n")  # a sentence's end, or a line break inside a paragraph
_WORD = re.compile(r"\S+")
_NOTHING = re.compile(r"(?!)")  # matches nowhere


@dataclass
class Passage:
    start: int  # where it starts and ends in Doc.text
    end: int
    text: str
    page: int | None  # the pages it starts and ends on, from 1 (PDFs)
    page_end: int | None
    heading: str | None  # e.g. "Coverage > Deductible"


@dataclass
class _Piece:
    """A sentence (or less, when one runs too long): the smallest thing a passage is made of."""

    start: int
    end: int
    words: int
    heading: bool = False  # a heading's own line
    starts_paragraph: bool = False


def _words(text: str) -> int:
    return len(_WORD.findall(text))


def _spans(text: str, start: int, end: int, pattern) -> list[tuple[int, int]]:
    """text[start:end] cut after each match of pattern, as (start, end) spans without the spaces around."""
    spans, at = [], start
    for m in pattern.finditer(text, start, end):
        spans.append((at, m.end()))
        at = m.end()
    spans.append((at, end))
    out = []
    for s, e in spans:
        piece = text[s:e]
        lead = len(piece) - len(piece.lstrip())
        trail = len(piece) - len(piece.rstrip())
        if e - trail > s + lead:
            out.append((s + lead, e - trail))
    return out


def _split_long(text: str, start: int, end: int) -> list[_Piece]:
    """A sentence longer than a passage, cut between words into passage-sized parts (or by characters, when
    it has hardly any spaces)."""
    words = [(m.start(), m.end()) for m in _WORD.finditer(text, start, end)]
    step = PASSAGE_WORDS - OVERLAP_WORDS
    pieces = []
    for i in range(0, len(words), step):
        part = words[i : i + step]
        s, e = part[0][0], part[-1][1]
        while e - s > MAX_PASSAGE_CHARS:  # one huge "word"
            pieces.append(_Piece(s, s + MAX_PASSAGE_CHARS, 1))
            s += MAX_PASSAGE_CHARS
        pieces.append(_Piece(s, e, len(part)))
    return pieces


def _pieces(doc: Doc) -> list[_Piece]:
    text = doc.text
    heading_starts = {h[0] for h in doc.headings}
    pieces = []
    for p_start, p_end in _spans(text, 0, len(text), _PARAGRAPHS):
        # a heading is its own line: split it off the paragraph it might be part of (as in Markdown)
        lines_at = sorted(h for h in heading_starts if p_start <= h < p_end)
        cuts = [p_start]
        for h in lines_at:
            nl = text.find("\n", h, p_end)
            cuts += [h, nl if nl != -1 else p_end]
        cuts.append(p_end)
        for a, b in zip(cuts, cuts[1:], strict=False):
            for s, e in _spans(text, a, b, _NOTHING):  # (just trims the spaces)
                if s in heading_starts:
                    pieces.append(_Piece(s, e, _words(text[s:e]), heading=True, starts_paragraph=True))
                    continue
                first = len(pieces)
                for ss, se in _spans(text, s, e, _SENTENCE_END):
                    n = _words(text[ss:se])
                    if n > PASSAGE_WORDS or se - ss > MAX_PASSAGE_CHARS:
                        pieces += _split_long(text, ss, se)
                    else:
                        pieces.append(_Piece(ss, se, n))
                if len(pieces) > first:
                    pieces[first].starts_paragraph = True
    return pieces


def _trail(doc: Doc):
    """A function giving the headings at a position, like "Coverage > Deductible"."""
    trails, stack = [], []  # (start, trail) for each heading; stack: (level, title) of the open ones
    for start, level, title in doc.headings:
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        trails.append((start, " > ".join(t for _, t in stack)))
    starts = [s for s, _ in trails]

    def at(pos: int) -> str | None:
        i = bisect.bisect_right(starts, pos) - 1
        return trails[i][1] if i >= 0 else None

    return at


def split(doc: Doc) -> list[Passage]:
    """The passages of a file's text, in order."""
    groups, current, words = [], [], 0
    for piece in _pieces(doc):
        while True:  # (again after a cut at a paragraph's end: what's carried over may still be too long)
            content = any(not p.heading for p in current)
            new_section = piece.heading and words >= MIN_SECTION_WORDS
            too_long = words + piece.words > PASSAGE_WORDS or (
                current and piece.end - current[0].start > MAX_PASSAGE_CHARS
            )
            if not (content and (new_section or too_long)):
                break
            tail = []  # headings at the end go with what follows them
            while current[-1].heading:
                tail.insert(0, current.pop())
            cut = None if (tail or new_section) else _paragraph_cut(current)
            group, rest = (current[:cut], current[cut:]) if cut else (current, [])
            groups.append(group)
            current = tail if (tail or new_section) else _overlap(group) + rest
            words = sum(p.words for p in current)
            if not rest:
                break
        current.append(piece)
        words += piece.words
    if current and (any(not p.heading for p in current) or not groups):
        groups.append(current)
    elif current:  # only headings at the very end, with nothing after them: they join the last passage
        groups[-1] = groups[-1] + current

    heading_at = _trail(doc)
    out = []
    for g in groups:
        start, end = g[0].start, g[-1].end
        pages = (
            (bisect.bisect_right(doc.pages, start), bisect.bisect_right(doc.pages, end - 1))
            if doc.pages
            else (None, None)
        )
        out.append(Passage(start, end, doc.text[start:end], *pages, heading_at(start)))
    return out


def _paragraph_cut(current: list[_Piece]) -> int | None:
    """Where a full passage should end: before the last paragraph that starts past MIN_PASSAGE_WORDS words
    (the rest goes on to the next passage), or None to end it where it is."""
    words = sum(p.words for p in current)
    for i in range(len(current) - 1, 0, -1):
        words -= current[i].words
        if words < MIN_PASSAGE_WORDS:
            return None
        if current[i].starts_paragraph:
            return i
    return None


def _overlap(group: list[_Piece]) -> list[_Piece]:
    """The whole sentences at the end of a passage that start the next one: up to OVERLAP_WORDS words (and a
    sixth of a passage's characters, for text with few spaces)."""
    keep, words = [], 0
    for piece in reversed(group):
        chars = (keep[-1].end if keep else piece.end) - piece.start
        if piece.heading or words + piece.words > OVERLAP_WORDS or chars > MAX_PASSAGE_CHARS // 6:
            break
        keep.insert(0, piece)
        words += piece.words
    return keep
