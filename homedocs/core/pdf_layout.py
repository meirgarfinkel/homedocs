"""A PDF page's text in reading order, worked out from where each character sits on the page, not the order the
file happens to draw them in.

The order a PDF draws its text in is often not the order it's read in: tax software prints the blank form, then
every value typed into it from the top again, so "Wages ... 1a" and its "41,250" end up far apart; a W-2's amounts
sit under their labels in boxes; a newsletter draws its boxes in any order. Read by position, a label and its
value stay together, and so do the lines of a column.

1. Pieces: the characters as drawn, a piece ending where the next one isn't right beside it on the same line
   (a column's edge, the end of a form's dot leaders).
2. The page is cut in two at a gap, again and again (an "XY cut"): first at a gap running down the page between
   two columns of text, but only when both sides read as lines of text (a column of short values, like a form's
   amounts or a table's numbers, stays with its labels: those are read a row at a time). A few pieces running
   across the gap at the top or bottom (a heading close above the columns) are read before or after them. Else the
   page is cut into bands at the gaps across it (a title over two columns, breaks between paragraphs).
3. What can't be cut is read a line at a time, each line's pieces left to right (right to left for Hebrew and
   Arabic, and so are two columns of it).

Text turned sideways (a "Downloaded by..." stamp up a margin, a chart's axis title) isn't part of the lines: it's
kept as it was drawn, after them.
"""

import itertools
import math
import re
import statistics

GAP = 0.8  # a piece ends at a space wider than this many times the text's height
GUTTER = 0.5  # a gap down the page at least this many times the height can be between two columns
WORD = 0.2  # a space between characters wider than this (times the height) is one between words
LINE_CHARS = 18  # a side of a gap is a column of text when its lines average at least this many characters
MIN_LINES = 3  # ...and it has at least this many lines
SAME_LINE = 0.3  # pieces sharing this much of the shorter one's height are one line (a label centred by two rows
# of a table joins the first)
ACROSS = 5  # at most this many pieces can run across the gap between two columns...
ACROSS_SHARE = 0.15  # ...and at most this share of them
BAND = 0.5  # a gap across the page at least this many times the text's height divides it into bands
_RTL = re.compile("[\u0590-\u08ff\ufb1d-\ufdff\ufe70-\ufeff]")  # Hebrew and Arabic letters


class Piece:
    __slots__ = ("text", "left", "bottom", "right", "top")

    def __init__(self, text, left, bottom, right, top):
        self.text, self.left, self.bottom, self.right, self.top = text, left, bottom, right, top

    @property
    def height(self) -> float:
        return self.top - self.bottom


def page_text(textpage) -> str | None:
    """The page's text in reading order, a line of the page to a line. None if its characters can't be
    lined up with its text (then the drawn order is all there is)."""
    import pypdfium2.raw as pdfium_c

    text = textpage.get_text_range()
    if len(text) != textpage.count_chars():
        return None
    boxes = [textpage.get_charbox(i, loose=True) for i in range(len(text))]
    left, bottom, right, top = textpage.page.get_bbox()
    text = "".join(  # (text off the page, cut away or hidden there, is left out, as PDFium's own reading does)
        c if left <= (b[0] + b[2]) / 2 <= right and bottom <= (b[1] + b[3]) / 2 <= top else " "
        for c, b in zip(text, boxes, strict=True)
    )
    turned = [abs(math.sin(pdfium_c.FPDFText_GetCharAngle(textpage.raw, i))) > 0.1 for i in range(len(text))]
    upright = "".join(" " if t else c for c, t in zip(text, turned, strict=True))
    sideways = "".join(c if t or c.isspace() else "\n" for c, t in zip(text, turned, strict=True))
    lines = read(pieces_of(upright, boxes))
    lines += [" ".join(line.split()) for line in sideways.split("\n") if line.strip()]
    return "\n".join(lines)


def pieces_of(text: str, boxes) -> list[Piece]:
    """The characters (with their boxes: left, bottom, right, top) as pieces of lines."""
    out: list[Piece] = []
    cur, last, spaced = None, None, False
    for c, (left, bottom, right, top) in zip(text, boxes, strict=True):
        if c.isspace():
            spaced = True  # (a space or line break in the text: whether one came between matters)
            continue
        if right <= left or top <= bottom:  # no place on the page (a marker like PDFium's soft hyphen)
            if cur is not None:
                cur.text += c
            continue
        if cur is not None:
            h = max(top - bottom, last[3] - last[1])
            level = min(top, last[3]) - max(bottom, last[1]) >= 0.5 * min(top - bottom, last[3] - last[1])
            gap = max(left - last[2], last[0] - right)  # (after the last one, or before it: right to left)
            overlap = min(right, last[2]) - max(left, last[0])  # (a ligature's letters share its box)
            if level and gap < GAP * h and (gap > -0.5 * h or overlap > 0.5 * min(right - left, last[2] - last[0])):
                cur.text += (" " if spaced or gap > WORD * h else "") + c
                cur.left, cur.bottom = min(cur.left, left), min(cur.bottom, bottom)
                cur.right, cur.top = max(cur.right, right), max(cur.top, top)
                last, spaced = (left, bottom, right, top), False
                continue
        cur = Piece(c, left, bottom, right, top)
        out.append(cur)
        last, spaced = (left, bottom, right, top), False
    return out


def read(pieces: list[Piece]) -> list[str]:
    """The pieces' lines, in reading order (see the top of this file)."""
    if not pieces:
        return []
    rtl = _is_rtl(pieces)
    columns = _columns(pieces)
    if columns:
        first, second, crossing = columns
        if crossing:
            return _around(pieces, crossing, rtl)
        return read(second if rtl else first) + read(first if rtl else second)
    bands = _bands(pieces)
    if len(bands) > 1:
        return [line for band in bands for line in read(band)]
    return [_line_text(line, rtl) for line in lines_of(pieces)]


def lines_of(pieces: list[Piece]) -> list[list[Piece]]:
    """The pieces grouped into lines, top to bottom: pieces side by side (sharing SAME_LINE of the shorter
    one's height with its first piece: a line doesn't grow, so a label between two rows can't join them)
    are one line."""
    lines: list[list] = []  # [bottom, top, pieces]
    for p in sorted(pieces, key=lambda p: (-p.top, p.left)):
        for line in reversed(lines[-3:]):  # (the last few: a line's pieces can start a little lower)
            if min(p.top, line[1]) - max(p.bottom, line[0]) >= SAME_LINE * min(p.height, line[1] - line[0]):
                line[2].append(p)
                break
        else:
            lines.append([p.bottom, p.top, [p]])
    return [line[2] for line in lines]


def _line_text(line: list[Piece], rtl: bool) -> str:
    rtl = rtl or _is_rtl(line)
    return " ".join(p.text for p in sorted(line, key=lambda p: -p.right if rtl else p.left))


def _is_rtl(pieces: list[Piece]) -> bool:
    """Mostly Hebrew or Arabic letters."""
    letters = "".join(c for p in pieces for c in p.text if c.isalpha())
    return bool(letters) and len(_RTL.findall(letters)) > len(letters) / 2


def _gaps(spans: list[tuple[float, float]], least: float) -> list[tuple[float, float]]:
    """The empty stretches, at least `least` long, between the spans (start, end), widest first."""
    spans = sorted(spans)
    gaps, reach = [], spans[0][1]
    for start, end in spans[1:]:
        if start - reach >= least:
            gaps.append((reach, start))
        reach = max(reach, end)
    return sorted(gaps, key=lambda g: g[0] - g[1])


def _columns(pieces: list[Piece]):
    """(left, right, crossing): the pieces either side of a gap running all the way down them, when both sides
    are columns of text, and the few running across it at the top or bottom (a heading close above the
    columns, a footer under them); else None. A gap nothing crosses comes first, then the widest."""
    h = statistics.median(p.height for p in pieces)
    most = min(ACROSS, int(len(pieces) * ACROSS_SHARE))
    for start, end in _strips(pieces, GUTTER * h, most):
        middle = (start + end) / 2
        left = [p for p in pieces if p.right <= middle]
        right = [p for p in pieces if p.left >= middle]
        crossing = [p for p in pieces if p.left < middle < p.right]
        if crossing and not _at_edge(crossing, left + right, h):
            continue  # (something in the middle crosses it, like a table wider than a column: not a gap)
        if _is_text(left) and _is_text(right):
            return left, right, crossing
    return None


def _at_edge(crossing: list[Piece], others: list[Piece], h: float) -> bool:
    """Every one of the crossing pieces is above all the others, or below them all."""
    top, bottom = max(p.top for p in others), min(p.bottom for p in others)
    return all(p.bottom >= top - 0.25 * h or p.top <= bottom + 0.25 * h for p in crossing)


def _strips(pieces: list[Piece], least: float, most: int) -> list[tuple[float, float]]:
    """The strips down the pieces (between their edges), at least `least` wide, that at most `most` of them
    cross: fewest crossing first, then widest."""
    edges = sorted({x for p in pieces for x in (p.left, p.right)})
    starts = sorted(p.left for p in pieces)
    ends = sorted(p.right for p in pieces)
    found, a, b = [], 0, 0
    for x0, x1 in itertools.pairwise(edges):
        while a < len(starts) and starts[a] <= x0:
            a += 1
        while b < len(ends) and ends[b] <= x0:
            b += 1
        crossing = a - b  # (pieces started by x0 and not yet ended)
        if x1 - x0 >= least and crossing <= most:
            found.append((crossing, x0 - x1, x0, x1))
    return [(x0, x1) for _, _, x0, x1 in sorted(found)]


def _around(pieces: list[Piece], crossing: list[Piece], rtl: bool) -> list[str]:
    """What's above each of the pieces running across two columns, then it, and so on down."""
    rows = lines_of(crossing)  # (top to bottom)
    mids = [statistics.mean((p.bottom + p.top) / 2 for p in row) for row in rows]
    parts: list[list[Piece]] = [[] for _ in range(len(rows) + 1)]
    ids = {id(p) for p in crossing}
    for p in pieces:
        if id(p) not in ids:
            parts[sum(1 for m in mids if (p.bottom + p.top) / 2 < m)].append(p)
    out = read(parts[0])
    for row, part in zip(rows, parts[1:], strict=True):
        out += [_line_text(row, rtl), *read(part)]
    return out


def _is_text(pieces: list[Piece]) -> bool:
    """Lines of text, not a column of short values (a form's amounts, a table's numbers, a chart's labels)."""
    lines = lines_of(pieces)
    chars = sum(len(p.text) for p in pieces)
    return len(lines) >= MIN_LINES and chars / len(lines) >= LINE_CHARS


def _bands(pieces: list[Piece]) -> list[list[Piece]]:
    """The pieces in bands, top to bottom, divided at the gaps across them at least BAND times their height."""
    h = statistics.median(p.height for p in pieces)
    cuts = sorted((start + end) / 2 for start, end in _gaps([(p.bottom, p.top) for p in pieces], BAND * h))
    bands: list[list[Piece]] = [[] for _ in range(len(cuts) + 1)]
    for p in pieces:
        bands[len(cuts) - sum(1 for c in cuts if p.bottom >= c)].append(p)
    return [b for b in bands if b]
