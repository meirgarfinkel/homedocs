"""Reading files: each kind gives its text, its pages (PDFs) and its headings; a file that can't be read
says why in plain words."""

import pytest
from samples import heading_styles, make_docx, make_pdf, para

from homedocs.core.parsers import ReadError, decode, read


def test_a_pdf_gives_each_page_and_its_bookmarks(tmp_path):
    f = make_pdf(
        tmp_path / "car.pdf",
        ["Car insurance\nThe deductible is five hun-\ndred dollars.", "Claims\nCall within 30 days."],
        bookmarks=[("Overview", 0), ("Claims", 1)],
    )
    doc = read(f)
    assert doc.text.startswith("Car insurance\nThe deductible is five hundred dollars.")  # the hyphen joined
    assert doc.pages == [0, doc.text.index("Claims\nCall")]
    assert [(lvl, title) for _, lvl, title in doc.headings] == [(1, "Overview"), (1, "Claims")]
    assert doc.headings[1][0] == doc.pages[1]


def test_a_pdf_without_text_is_probably_a_scan(tmp_path):
    with pytest.raises(ReadError, match="scanned image"):
        read(make_pdf(tmp_path / "scan.pdf", ["", ""]))


def test_a_broken_pdf_says_so(tmp_path):
    (tmp_path / "bad.pdf").write_bytes(b"%PDF-1.4 nothing else")
    with pytest.raises(ReadError, match="damaged"):
        read(tmp_path / "bad.pdf")


def test_word_reads_paragraphs_tables_and_headings_but_not_deleted_text(tmp_path):
    body = (
        para("Coverage", "Heading1")
        + para("The deductible is $500.")
        + para("Limits", "Heading2")
        + "<w:tbl><w:tr><w:tc>"
        + para("Plan")
        + "</w:tc><w:tc>"
        + para("Gold")
        + "</w:tc></w:tr></w:tbl>"
        + "<w:p><w:r><w:t>Kept</w:t></w:r><w:del><w:r><w:delText>deleted</w:delText></w:r></w:del>"
        + "<w:r><w:instrText>PAGE</w:instrText></w:r><w:r><w:tab/><w:t>words</w:t></w:r></w:p>"
        + "<w:sdt><w:sdtContent>"
        + para("Inside a content control")
        + "</w:sdtContent></w:sdt>"
    )
    doc = read(make_docx(tmp_path / "policy.docx", body, heading_styles()))
    assert (
        doc.text
        == "Coverage\n\nThe deductible is $500.\n\nLimits\n\nPlan | Gold\n\nKept\twords\n\nInside a content control"
    )
    assert [(doc.text[s:].split("\n")[0], lvl) for s, lvl, _ in doc.headings] == [("Coverage", 1), ("Limits", 2)]


def test_a_broken_word_file_says_so(tmp_path):
    (tmp_path / "bad.docx").write_bytes(b"PK not really")
    with pytest.raises(ReadError, match="damaged"):
        read(tmp_path / "bad.docx")


def test_markdown_is_read_as_written_with_its_headings(tmp_path):
    text = "# Title\n\nSome text.\n\nSection\n-------\n\n```\n# not a heading\n```\n\n## Last ##\n"
    (tmp_path / "notes.md").write_text(text, encoding="utf-8")
    doc = read(tmp_path / "notes.md")
    assert doc.text == text
    assert [(lvl, title) for _, lvl, title in doc.headings] == [(1, "Title"), (2, "Section"), (2, "Last")]
    assert all(text[s:].lstrip("# ").startswith(t) for s, _, t in doc.headings)


def test_text_in_any_common_encoding():
    assert decode("café".encode()) == "café"
    assert decode("café".encode("utf-16")) == "café"
    assert decode("café".encode("cp1252")) == "café"
    assert decode(b"\xef\xbb\xbfhello") == "hello"  # (the marker some editors put first)


def test_windows_line_endings_become_plain_ones(tmp_path):
    (tmp_path / "a.txt").write_bytes(b"one\r\ntwo\rthree")
    assert read(tmp_path / "a.txt").text == "one\ntwo\nthree"


def test_an_empty_file_has_no_text(tmp_path):
    (tmp_path / "empty.txt").write_text("  \n\n")
    with pytest.raises(ReadError, match="no text"):
        read(tmp_path / "empty.txt")
