"""The BERT WordPiece tokenizer both ONNX models use, in plain Python.

It does what Hugging Face's `tokenizers` does for these models (BertNormalizer, BertPreTokenizer,
WordPiece), so the models see exactly the token ids they were trained on: tests/test_tokenizer.py checks
it against ids `tokenizers` produced. Written here rather than depending on `tokenizers`, which brings
huggingface-hub and an HTTP stack for this one job.

1. Normalize: drop control characters, make every kind of space a space, put spaces around Chinese,
   Japanese and Korean characters, take accents off (é -> e), lowercase.
2. Split into words at spaces, with each punctuation mark a word of its own.
3. WordPiece: each word becomes the longest pieces in the vocabulary, left to right ("unaffable" ->
   "un", "##aff", "##able"); a word that can't be made of pieces, or is over 100 characters, is [UNK].

Characters added or reclassified in recent Unicode versions can come out differently from `tokenizers`,
which uses older Unicode tables than Python; English text never meets them.
"""

import re
import unicodedata
from functools import lru_cache
from pathlib import Path


def _is_control(ch: str) -> bool:
    if ch in "\t\n\r":
        return False
    return unicodedata.category(ch) in ("Cc", "Cf", "Co")  # (not unassigned ones: `tokenizers` keeps them)


def _is_whitespace(ch: str) -> bool:
    return ch in " \t\n\r\u2028\u2029" or unicodedata.category(ch) == "Zs"


def _is_punctuation(ch: str) -> bool:
    cp = ord(ch)
    if 33 <= cp <= 47 or 58 <= cp <= 64 or 91 <= cp <= 96 or 123 <= cp <= 126:
        return True
    return unicodedata.category(ch).startswith("P")


def _is_cjk(cp: int) -> bool:
    return (
        0x4E00 <= cp <= 0x9FFF
        or 0x3400 <= cp <= 0x4DBF
        or 0x20000 <= cp <= 0x2A6DF
        or 0x2A700 <= cp <= 0x2B73F
        or 0x2B740 <= cp <= 0x2B81F
        or 0x2B820 <= cp <= 0x2CEAF
        or 0xF900 <= cp <= 0xFAFF
        or 0x2F800 <= cp <= 0x2FA1F
    )


def normalize(text: str) -> str:
    out = []
    for ch in text:
        cp = ord(ch)
        if cp == 0 or cp == 0xFFFD or _is_control(ch):
            continue
        if _is_whitespace(ch):
            out.append(" ")
        elif _is_cjk(cp):
            out.append(f" {ch} ")
        else:
            out.append(ch)
    text = unicodedata.normalize("NFD", "".join(out))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return text.lower()


def pre_tokenize(text: str) -> list[str]:
    words, current = [], []
    for ch in text:
        if ch.isspace() or _is_whitespace(ch):
            if current:
                words.append("".join(current))
                current = []
        elif _is_punctuation(ch):
            if current:
                words.append("".join(current))
                current = []
            words.append(ch)
        else:
            current.append(ch)
    if current:
        words.append("".join(current))
    return words


SPECIAL = ("[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]")
_SPECIAL = re.compile("(" + "|".join(re.escape(t) for t in SPECIAL) + ")")


class Tokenizer:
    """Reads a model's vocab.txt (one token per line, its id the line number)."""

    MAX_WORD_CHARS = 100

    def __init__(self, vocab_file):
        lines = Path(vocab_file).read_text(encoding="utf-8").split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        self.vocab = {tok: i for i, tok in enumerate(lines)}
        self.unk = self.vocab["[UNK]"]
        self.cls = self.vocab["[CLS]"]
        self.sep = self.vocab["[SEP]"]
        self.pad = self.vocab["[PAD]"]
        self._word = lru_cache(maxsize=200_000)(self._word_ids)

    def _word_ids(self, word: str) -> tuple[int, ...]:
        if len(word) > self.MAX_WORD_CHARS:
            return (self.unk,)
        ids, start = [], 0
        while start < len(word):
            end = len(word)
            found = None
            while start < end:
                piece = word[start:end] if start == 0 else "##" + word[start:end]
                found = self.vocab.get(piece)
                if found is not None:
                    break
                end -= 1
            if found is None:
                return (self.unk,)
            ids.append(found)
            start = end
        return tuple(ids)

    def tokenize(self, text: str) -> list[int]:
        """The ids of text's tokens, without [CLS] and [SEP]. (Like `tokenizers`, "[SEP]" and the other special
        tokens written in the text are those tokens.)"""
        ids = []
        for i, part in enumerate(_SPECIAL.split(text)):
            if i % 2:
                ids.append(self.vocab[part])
                continue
            for word in pre_tokenize(normalize(part)):
                ids.extend(self._word(word))
        return ids

    def encode(self, text: str, max_len: int = 512) -> list[int]:
        """[CLS] text [SEP], cut to max_len tokens."""
        return [self.cls, *self.tokenize(text)[: max_len - 2], self.sep]

    def encode_pair(self, first: str, second: str, max_len: int = 512) -> tuple[list[int], list[int]]:
        """[CLS] first [SEP] second [SEP] and its token type ids (0 for first's part, 1 for second's). Too long,
        the longer is cut to fit beside the shorter; both longer than half, each gets half."""
        a, b = self.tokenize(first), self.tokenize(second)
        room = max_len - 3
        if len(a) + len(b) > room:
            half = room // 2
            if len(a) > half and len(b) > half:  # (an odd token over goes to the longer; tied, the second)
                a, b = (a[: room - half], b[:half]) if len(a) > len(b) else (a[:half], b[: room - half])
            elif len(a) <= half:
                b = b[: room - len(a)]
            else:
                a = a[: room - len(b)]
        ids = [self.cls, *a, self.sep, *b, self.sep]
        return ids, [0] * (len(a) + 2) + [1] * (len(b) + 1)
