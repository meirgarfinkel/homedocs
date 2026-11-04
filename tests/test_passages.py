"""Splitting text into passages: about PASSAGE_WORDS words, cut between paragraphs or sentences, the end of
each starting the next, a heading starting a passage and never ending one, and each passage knowing its
page, headings and place in the text."""

import re

from homedocs.core.config import MIN_SECTION_WORDS, OVERLAP_WORDS, PASSAGE_WORDS
from homedocs.core.parsers import Doc
from homedocs.core.passages import split


def words(text):
    return len(text.split())


def sentences(n, word="word"):
    return " ".join(f"Sentence {i} has a few more {word}s in it." for i in range(n))  # 8 words each


def check_positions(doc, parts):
    for p in parts:
        assert doc.text[p.start : p.end] == p.text


def test_short_text_is_one_passage():
    doc = Doc("Just a line.\n\nAnd another.")
    (p,) = split(doc)
    assert p.text == doc.text and p.page is p.page_end is None and p.heading is None


def test_long_text_is_cut_between_sentences_with_overlap():
    doc = Doc(sentences(200))
    parts = split(doc)
    check_positions(doc, parts)
    assert len(parts) > 4
    for p in parts:
        assert words(p.text) <= PASSAGE_WORDS
        assert re.match(r"Sentence \d+ ", p.text) and p.text.endswith("in it.")  # whole sentences
    for a, b in zip(parts, parts[1:], strict=False):
        shared = a.end - b.start
        assert shared > 0 and words(doc.text[b.start : a.end]) <= OVERLAP_WORDS


def test_a_sentence_longer_than_a_passage_is_cut_between_words():
    doc = Doc(" ".join(f"w{i}" for i in range(1000)))  # no sentence ends at all
    parts = split(doc)
    check_positions(doc, parts)
    assert all(words(p.text) <= PASSAGE_WORDS for p in parts)
    assert parts[0].text.startswith("w0 ") and parts[-1].text.endswith("w999")


def test_text_without_spaces_is_cut_too():
    doc = Doc("x" * 10000)
    parts = split(doc)
    check_positions(doc, parts)
    assert len(parts) == 4 and all(len(p.text) <= 3000 for p in parts)
    assert parts[-1].end == 10000


def test_a_heading_starts_a_passage_and_never_ends_one():
    body = sentences(MIN_SECTION_WORDS // 8 + 2)
    text = f"Coverage\n\n{body}\n\nClaims\n\n{body}\n\nDeductible\n\n{sentences(40)}"
    doc = Doc(
        text,
        headings=[(0, 1, "Coverage"), (text.index("Claims"), 1, "Claims"), (text.index("Deductible"), 2, "Deductible")],
    )
    parts = split(doc)
    check_positions(doc, parts)
    assert parts[0].text.startswith("Coverage") and parts[0].heading == "Coverage"
    assert parts[1].text.startswith("Claims") and parts[1].heading == "Claims"
    assert not any(p.text.endswith(("Claims", "Deductible")) for p in parts)
    assert parts[-1].heading == "Claims > Deductible"


def test_each_passage_knows_its_page():
    page1, page2 = sentences(30), sentences(30, "thing")
    text = page1 + "\n\n" + page2
    doc = Doc(text, pages=[0, len(page1) + 2])
    parts = split(doc)
    assert [(p.page, p.page_end) for p in parts] == [(1, 2), (1, 2)]  # (both cross the page break)
    (one,) = split(Doc(page1, pages=[0]))
    assert (one.page, one.page_end) == (1, 1)


def test_markdown_headings_inside_a_paragraph_are_their_own_line():
    text = "# Title\nIntro line.\n## Part\nBody."
    doc = Doc(text, headings=[(0, 1, "Title"), (text.index("## Part"), 2, "Part")])
    (p,) = split(doc)  # all short: one passage, the heading trail at its start
    assert p.heading == "Title" and p.text == text
