"""The household documents for the eval, made into PDFs from the text in household/.

A file starting with a "title:" header becomes a PDF the way a printed document looks: wrapped lines, the
title at the top of each page and "Page N of M" at the bottom, tables as rows of cells, and page breaks
wherever the page fills up (so some facts are split across two pages, as in real files). With
"bookmarks: yes", its headings become the PDF's bookmarks. Any other file is copied as it is.
"""

import shutil
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.samples import make_pdf  # noqa: E402

HERE = Path(__file__).resolve().parent / "household"
WIDTH = 92  # characters on a line (Helvetica 11 on a letter page)
LINES = 50  # lines of text on a page, besides the title and page number


def _lines(body: str) -> tuple[list[str], list[tuple[str, int]]]:
    """The body as printed lines, and (heading, its line number) for each heading."""
    out, headings = [], []
    for block in body.strip().split("\n\n"):
        block = block.strip()
        if block.startswith("# "):
            if out:
                out.append("")
            headings.append((block[2:].strip(), len(out)))
            out.append(block[2:].strip())
            continue
        if block.startswith("|"):
            for row in block.splitlines():
                cells = [c.strip() for c in row.strip().strip("|").split("|")]
                row_text = "    ".join(cells)
                out.extend(textwrap.wrap(row_text, WIDTH, subsequent_indent="        ", break_on_hyphens=False))
            out.append("")
            continue
        for para in block.splitlines():  # (a line break inside a block is its own paragraph)
            out.extend(textwrap.wrap(para, WIDTH, break_on_hyphens=False))
        out.append("")
    return out, headings


def make(src: Path, dest_dir: Path) -> Path:
    text = src.read_text(encoding="utf-8")
    if not text.startswith("title:"):
        return Path(shutil.copy(src, dest_dir / src.name))
    head, body = text.split("\n---\n", 1)
    meta = dict(line.split(":", 1) for line in head.splitlines())
    title = meta["title"].strip()
    lines, headings = _lines(body)
    pages = [lines[i : i + LINES] for i in range(0, len(lines), LINES)]
    printed = [
        "\n".join([title, "", *page, *[""] * (LINES - len(page)), f"Page {n} of {len(pages)}"])
        for n, page in enumerate(pages, 1)
    ]
    marks = [(h, line // LINES) for h, line in headings] if meta.get("bookmarks", "").strip() == "yes" else []
    return make_pdf(dest_dir / (src.stem + ".pdf"), printed, marks)


def make_all(dest_dir: Path) -> list[Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    return [make(src, dest_dir) for src in sorted(HERE.iterdir()) if src.is_file()]


if __name__ == "__main__":
    for p in make_all(Path(sys.argv[1] if len(sys.argv) > 1 else "build/evals/household")):
        print(p)
