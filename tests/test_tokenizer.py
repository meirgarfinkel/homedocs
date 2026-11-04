"""The tokenizer gives exactly the ids Hugging Face's `tokenizers` gives for the same text (see
fixtures/README.md), single texts and question-passage pairs, including cutting a pair to fit."""

import json
from pathlib import Path

import pytest

from homedocs.core.tokenizer import Tokenizer

FIXTURES = Path(__file__).parent / "fixtures"
CASES = json.loads((FIXTURES / "tokenizer-cases.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def tok():
    return Tokenizer(FIXTURES / "bert-uncased-vocab.txt")


@pytest.mark.parametrize("text, ids", CASES["single"], ids=lambda v: repr(v)[:30] if isinstance(v, str) else "")
def test_single_texts(tok, text, ids):
    assert tok.encode(text, max_len=10**6) == ids


@pytest.mark.parametrize("first, second, ids, types", CASES["pairs"], ids=lambda v: "")
def test_pairs_cut_to_fit(tok, first, second, ids, types):
    assert tok.encode_pair(first, second) == (ids, types)


def test_a_long_text_is_cut_to_the_limit(tok):
    ids = tok.encode("word " * 1000)
    assert len(ids) == 512 and ids[0] == tok.cls and ids[-1] == tok.sep
