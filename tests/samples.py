"""Small real files for the tests: PDFs and Word documents written from scratch, so the repo holds no
binary samples."""

import zipfile
from pathlib import Path


def make_pdf(path: Path, pages: list[str], bookmarks: list[tuple[str, int]] = ()) -> Path:
    """A PDF with one page per string (each line of it a line of text), and bookmarks: (title, page from 0)."""
    objs = {}  # number -> bytes
    page_ids = [4 + 2 * i for i in range(len(pages))]
    outline_id = 4 + 2 * len(pages)
    mark_ids = [outline_id + 1 + i for i in range(len(bookmarks))]
    objs[1] = (
        b"<< /Type /Catalog /Pages 2 0 R" + (f" /Outlines {outline_id} 0 R".encode() if bookmarks else b"") + b" >>"
    )
    kids = " ".join(f"{p} 0 R" for p in page_ids)
    objs[2] = f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode()
    objs[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    for pid, text in zip(page_ids, pages, strict=True):
        lines = []
        for line in text.split("\n"):
            safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            lines.append(f"({safe}) Tj T*")
        stream = ("BT /F1 11 Tf 14 TL 50 780 Td " + " ".join(lines) + " ET").encode("latin-1")
        objs[pid] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
            + f"/Contents {pid + 1} 0 R >>".encode()
        )
        objs[pid + 1] = f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"
    if bookmarks:
        objs[outline_id] = (
            f"<< /Type /Outlines /First {mark_ids[0]} 0 R /Last {mark_ids[-1]} 0 R /Count {len(mark_ids)} >>".encode()
        )
        for i, ((title, page), mid) in enumerate(zip(bookmarks, mark_ids, strict=True)):
            links = (f" /Prev {mark_ids[i - 1]} 0 R" if i else "") + (
                f" /Next {mark_ids[i + 1]} 0 R" if i + 1 < len(mark_ids) else ""
            )
            objs[mid] = (
                f"<< /Title ({title}) /Parent {outline_id} 0 R /Dest [{page_ids[page]} 0 R /Fit]{links} >>".encode()
            )
    out, offsets = bytearray(b"%PDF-1.4\n"), {}
    for n in sorted(objs):
        offsets[n] = len(out)
        out += f"{n} 0 obj\n".encode() + objs[n] + b"\nendobj\n"
    xref = len(out)
    size = max(objs) + 1
    out += f"xref\n0 {size}\n0000000000 65535 f \n".encode()
    for n in range(1, size):
        out += f"{offsets.get(n, 0):010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))
    return path


W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def make_docx(path: Path, body: str, styles: str = "") -> Path:
    """A .docx whose <w:body> holds body (WordprocessingML), with optional <w:styles> content."""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", f"<w:document {W}><w:body>{body}</w:body></w:document>")
        if styles:
            z.writestr("word/styles.xml", f"<w:styles {W}>{styles}</w:styles>")
    return path


def para(text: str, style: str = "") -> str:
    """One Word paragraph (a style id like "Heading1" makes it a heading, with heading_styles())."""
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f'<w:p>{ppr}<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'


def heading_styles() -> str:
    return "".join(
        f'<w:style w:type="paragraph" w:styleId="Heading{n}"><w:name w:val="heading {n}"/></w:style>' for n in (1, 2)
    )
