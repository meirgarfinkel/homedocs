"""Reading files into plain text: one function per kind of file.

Each returns a Doc: the text, where each page starts in it (PDFs only), and its headings with where each
starts. Passages are cut from that text and remember positions in it, so the same file always reads the
same way. A file that can't be read raises ReadError with a reason the Library page shows.
"""

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

from . import pdf_layout


class ReadError(Exception):
    """This file can't be read; the message says why, in words for the Library page."""


@dataclass
class Doc:
    text: str
    pages: list[int] | None = None  # where each page starts in text (PDFs), page 1 first
    headings: list[tuple[int, int, str]] = field(default_factory=list)  # (where it starts, level 1-9, title)


def read(path) -> Doc:
    """The file's text. Raises ReadError when it can't be read."""
    path = Path(path)
    kind = path.suffix.lower()
    try:
        if kind == ".pdf":
            doc = read_pdf(path)
        elif kind == ".docx":
            doc = read_docx(path)
        elif kind in (".md", ".markdown"):
            doc = read_markdown(path)
        else:
            doc = read_text(path)
    except ReadError:
        raise
    except PermissionError:
        raise ReadError("HomeDocs isn't allowed to open this file.") from None
    except FileNotFoundError:
        raise ReadError("The file was moved or deleted while being read.") from None
    except MemoryError:
        raise ReadError("This file is too big to read.") from None
    if not re.search(r"\w", doc.text):
        if kind == ".pdf":
            raise ReadError("No text found. It may be a scanned image (reading scans comes in a later version).")
        raise ReadError("This file has no text.")
    return doc


# --- plain text ------------------------------------------------------------------------------------


def decode(data: bytes) -> str:
    """Text from bytes: UTF-8, UTF-16 when it starts with that marker, else Windows-1252 (which reads
    anything; the few bytes it lacks become U+FFFD)."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            pass
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _clean(text: str) -> str:
    """One kind of line ending, and no NUL characters."""
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")


def read_text(path: Path) -> Doc:
    return Doc(_clean(decode(path.read_bytes())))


# --- Markdown: read as written (so positions match the file), with its headings ---------------------

_ATX = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t#]*$")
_SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(```|~~~)")


def read_markdown(path: Path) -> Doc:
    text = _clean(decode(path.read_bytes()))
    headings, pos, fence, prev = [], 0, None, None  # prev: the line before (start, text), for "Title\n==="
    for line in text.split("\n"):
        start, pos = pos, pos + len(line) + 1
        f = _FENCE.match(line)
        if fence:
            if f and f.group(1) == fence:
                fence = None
            prev = None
            continue
        if f:
            fence, prev = f.group(1), None
            continue
        m = _ATX.match(line)
        if m:
            headings.append((start, len(m.group(1)), m.group(2).strip()))
            prev = None
            continue
        s = _SETEXT.match(line)
        if s and prev and prev[1].strip() and not prev[1].startswith((" " * 4, "\t", "-", "*", ">")):
            headings.append((prev[0], 1 if s.group(1)[0] == "=" else 2, prev[1].strip()))
            prev = None
            continue
        prev = (start, line)
    return Doc(text, headings=headings)


# --- Word (.docx): the body's paragraphs and tables, from the XML inside the zip ---------------------

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
MAX_XML_BYTES = 200 * 1024 * 1024  # a document.xml bigger than this unpacked is refused (zip bombs)


def _xml(z: zipfile.ZipFile, name: str):
    try:
        info = z.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_XML_BYTES:
        raise ReadError("This Word file is too big to read.")
    return ElementTree.fromstring(z.read(info))


def _heading_styles(styles) -> dict[str, int]:
    """Style id -> heading level, for styles named "heading N" or "Title", or with an outline level."""
    levels = {}
    if styles is None:
        return levels
    for s in styles.iter(W + "style"):
        sid = s.get(W + "styleId")
        name = (s.find(W + "name").get(W + "val", "") if s.find(W + "name") is not None else "").lower()
        m = re.fullmatch(r"heading (\d)", name)
        level = 1 if name == "title" else int(m.group(1)) if m else _outline_level(s.find(W + "pPr"))
        if level:
            levels[sid] = level
    return levels


def _runs_text(el, out: list):
    """The text of a paragraph's runs, in order: not deleted text, field codes, or the older copy Word
    keeps of drawings for old readers (mc:Fallback)."""
    for child in el:
        tag = child.tag
        if tag == W + "t":
            out.append(child.text or "")
        elif tag == W + "tab":
            out.append("\t")
        elif tag in (W + "br", W + "cr"):
            out.append("\n")
        elif tag in (W + "del", W + "delText", W + "instrText", MC_FALLBACK):
            continue
        else:
            _runs_text(child, out)


def _outline_level(p_pr) -> int | None:
    """A paragraph's or style's own heading level (outlineLvl 0-8 is level 1-9), if it has one."""
    lvl = p_pr.find(W + "outlineLvl") if p_pr is not None else None
    val = lvl.get(W + "val", "9") if lvl is not None else "9"
    return int(val) + 1 if val.isdigit() and int(val) < 9 else None


class _DocxText:
    """The text of a document.xml body: paragraphs and table rows, a blank line between them."""

    def __init__(self, levels: dict[str, int]):
        self.levels, self.parts, self.headings, self.pos = levels, [], [], 0

    def add(self, text: str, level: int = 0):
        text = text.strip()
        if not text:
            return
        if self.parts:
            self.parts.append("\n\n")
            self.pos += 2
        if level:
            self.headings.append((self.pos, level, " ".join(text.split())))
        self.parts.append(text)
        self.pos += len(text)

    def paragraph(self, p):
        p_pr = p.find(W + "pPr")
        style = p_pr.find(W + "pStyle") if p_pr is not None else None
        level = _outline_level(p_pr) or (self.levels.get(style.get(W + "val")) if style is not None else None)
        out = []
        _runs_text(p, out)
        self.add("".join(out), level or 0)

    def table(self, tbl):
        for row in tbl.iter(W + "tr"):
            cells = []
            for cell in row.findall(W + "tc"):
                out = []
                for p in cell.iter(W + "p"):
                    _runs_text(p, out)
                    out.append(" ")
                cells.append(" ".join("".join(out).split()))
            self.add(" | ".join(c for c in cells if c))

    def block(self, el):
        for child in el:
            if child.tag == W + "p":
                self.paragraph(child)
            elif child.tag == W + "tbl":
                self.table(child)
            elif child.tag in (W + "sdt", W + "sdtContent", W + "customXml", W + "ins"):
                self.block(child)  # content controls and the like: what's inside counts


def read_docx(path: Path) -> Doc:
    try:
        with zipfile.ZipFile(path) as z:
            body = _xml(z, "word/document.xml")
            levels = _heading_styles(_xml(z, "word/styles.xml"))
    except zipfile.BadZipFile:
        raise ReadError("This Word file is damaged, or isn't really a .docx file.") from None
    except ElementTree.ParseError:
        raise ReadError("This Word file is damaged.") from None
    if body is None or body.find(W + "body") is None:
        raise ReadError("This Word file is damaged, or isn't really a .docx file.")
    out = _DocxText(levels)
    out.block(body.find(W + "body"))
    return Doc("".join(out.parts), headings=out.headings)


# --- PDF: each page's text, through PDFium ---------------------------------------------------------


def read_pdf(path: Path) -> Doc:
    import pypdfium2 as pdfium

    f = open(path, "rb")  # noqa: SIM115 (PDFium reads it as it goes, and closes it with the document)
    try:
        # (an open file, not the path: PDFium's own opening fails on some non-English paths on Windows)
        pdf = pdfium.PdfDocument(f, autoclose=True)
    except pdfium.PdfiumError as e:
        f.close()
        if "password" in str(e).lower():
            raise ReadError("This PDF is protected by a password.") from None
        raise ReadError("This PDF is damaged, or isn't really a PDF.") from None
    try:
        parts, pages, pos = [], [], 0
        for i in range(len(pdf)):
            page = pdf[i]
            textpage = page.get_textpage()
            laid_out = pdf_layout.page_text(textpage)  # (in reading order: see pdf_layout.py)
            text = _pdf_text(textpage.get_text_bounded() if laid_out is None else laid_out)
            textpage.close()
            page.close()
            if parts:
                parts.append("\n\n")
                pos += 2
            pages.append(pos)
            parts.append(text)
            pos += len(text)
        headings = []
        try:
            for mark in pdf.get_toc(max_depth=6):  # the PDF's bookmarks, as headings at their pages
                dest, title = mark.get_dest(), " ".join(mark.get_title().split())
                index = dest.get_index() if dest is not None else None
                if index is not None and 0 <= index < len(pages) and title:
                    headings.append((pages[index], mark.level + 1, title))
        except Exception:
            headings = []  # a broken outline isn't a reason to skip the text
        headings.sort(key=lambda h: h[0])  # (stable: same page keeps the outline's order)
        return Doc("".join(parts), pages=pages, headings=headings)
    finally:
        pdf.close()


_LEADER = re.compile(r"[ \t]*(?:\.[ \t]*){4,}")


def _pdf_text(text: str) -> str:
    """A page's text, tidied: one kind of line ending, words split by a hyphen at the end of a line
    joined again (PDFium marks those hyphens), dot leaders made one "…", and no trailing spaces."""
    text = _clean(text)
    text = re.sub(r"[\x02­￾]\n", "", text)  # PDFium's soft hyphen, then the line break
    text = text.replace("\x02", "").replace("￾", "")
    # a form's or a contents page's ". . . . . . 1a" (each dot would count as a word, leaving a passage room
    # for a line or two of a tax form, and cutting the line's amount off)
    text = _LEADER.sub(" … ", text)
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()
