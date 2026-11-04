"""Answering a question from the passages search found, and finding what backs each sentence of the answer.

1. Ask the model at all? Not when nothing was found, or when even the best passage's re-ranker score is below
   NOT_FOUND_SCORE: then the answer is "Not in your files." at once.
2. The prompt: the best ANSWER_PASSAGES passages, numbered, with their file and place. The model is told to
   answer from them alone in a few sentences, or say "Not in your files." It isn't asked to cite: in the test
   (REQUIREMENTS.md), small models cited the wrong passage or ignored the instruction.
3. The answer streams in, a piece at a time.
4. Sources: each sentence of the answer is matched against every sentence of those passages, by the share of
   its words found there and by meaning (the embedder). The best match, if close enough, backs it; a sentence
   nothing backs is marked as not found in the files.

The chat model sits behind generate(instructions, rest, max_new, should_stop) -> text pieces, and stop(), so
tests use a fake (tests/fakes.py).
"""

import json
import re
import threading
import time
from dataclasses import dataclass, field

from . import store
from .config import (
    ANSWER_PASSAGE_CHARS,
    ANSWER_PASSAGES,
    ANSWER_TOKENS,
    NOT_FOUND_SCORE,
    SUPPORT_MEANING,
    SUPPORT_WORDS,
)
from .search import Found, Result, _results

NOT_FOUND = "Not in your files."

INSTRUCTIONS = (
    "You answer questions using only the numbered passages below, which come from the user's own files. "
    "Answer in one to three short sentences. Use only facts stated in the passages. "
    f'If the passages don\'t contain the answer, reply exactly: "{NOT_FOUND}"'
)


@dataclass
class Sentence:
    """One sentence of an answer and what backs it."""

    start: int  # where it is in the answer's text
    end: int
    passage: int | None = None  # the backing passage: its place in Answer.passages (0 = the first), or None
    span: tuple[int, int] | None = None  # the backing sentence's place in that passage's text
    checked: bool = True  # False: nothing in it to check (like "Yes.")

    @property
    def backed(self) -> bool:
        return self.passage is not None


@dataclass
class Answer:
    question: str
    text: str
    passages: list[Result]  # the ones the model read, best first (the first ones shown)
    sentences: list[Sentence] = field(default_factory=list)
    seconds: float = 0.0
    asked_model: bool = True

    @property
    def not_found(self) -> bool:
        return is_not_found(self.text)


def is_not_found(text: str) -> bool:
    return text.strip().lower().startswith(NOT_FOUND.lower().rstrip("."))


def should_ask(found: Found) -> bool:
    """Whether the model could find an answer in these passages (else it's "Not in your files." at once)."""
    if not found.results:
        return False
    if found.by_meaning:  # (the scores are the re-ranker's only then)
        return found.results[0].score >= NOT_FOUND_SCORE
    return True


def prompt(question: str, passages: list[Result]) -> tuple[str, str]:
    """(instructions, rest) in Qwen's chat format, thinking off. The instructions are the same every time, so
    the model reads them once and keeps them."""
    instructions = f"<|im_start|>system\n{INSTRUCTIONS}<|im_end|>\n<|im_start|>user\nPassages:\n\n"
    blocks = []
    for i, r in enumerate(passages, 1):
        where = f" — {r.where()}" if r.where() else ""
        text = r.text if len(r.text) <= ANSWER_PASSAGE_CHARS else r.text[:ANSWER_PASSAGE_CHARS] + " …"
        blocks.append(f"[{i}] {r.name}{where}\n{text}")
    rest = (
        "\n\n".join(blocks)
        + f"\n\nQuestion: {question.strip()}<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
    )
    return instructions, rest


def tidy(text: str) -> str:
    """The model's answer, cleaned: a "Not in your files." with anything after it is just that."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    return NOT_FOUND if is_not_found(text) else text


# --- sources --------------------------------------------------------------------------------------------

# a sentence ends at . ! or ? and a space, a blank line, or a line break before a list item, heading or table
# row; a single line break inside a paragraph (text wrapped by hand) doesn't end one
_SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]]*\s+|\n[ \t]*\n\s*|\n(?=[ \t]*(?:[-*•#|>]|\d+[.)]\s))")


def sentences(text: str) -> list[tuple[int, int]]:
    """Where each sentence (or line) of text starts and ends, spaces left out."""
    out, at = [], 0
    for m in [*_SENTENCE_END.finditer(text), None]:
        end = m.start() if m else len(text)
        piece = text[at:end]
        if piece.strip():
            s = at + len(piece) - len(piece.lstrip())
            out.append((s, at + len(piece.rstrip())))
        if m:
            at = m.end()
    return out


_FILLER = frozenset({"yes", "no", "ok", "okay", "sure"})  # (a "Yes." on its own has nothing to check)


def _stem(w: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(w) > 4 and w.endswith(suffix):
            return w[: -len(suffix)]
    return w


def words(text: str) -> set[str]:
    """The words worth matching: lower case, roughly stemmed, common words left out ("$500" and "16x25x1"
    stay whole)."""
    text = re.sub(r"'s\b", "", text.lower().replace("\u2019", "'"))
    found = re.findall(r"[a-z0-9$][a-z0-9$.,\-/]*[a-z0-9]|[a-z0-9]", text)
    return {_stem(w.replace(",", "")) for w in found if w not in store.COMMON and w not in _FILLER}


def find_sources(text: str, passages: list[Result], embedder=None, question: str = "") -> list[Sentence]:
    """What backs each sentence of the answer: the passage sentence holding SUPPORT_WORDS of its words, or this
    close in meaning (SUPPORT_MEANING), best first; nothing if none does. embedder None: words only.

    The question's own words are left out of each sentence's (when anything is left): repeating the question
    needs no source, and what the sentence adds, like "45 minutes", is what has to be in the passage."""
    if is_not_found(text):
        return []
    claims = [Sentence(s, e) for s, e in sentences(text)]
    cands = [(i, s, e) for i, r in enumerate(passages) for s, e in sentences(r.text) if e - s > 3]
    asked = words(question)
    claim_words = [(w - asked) or w for w in (words(text[c.start : c.end]) for c in claims)]
    cand_words = [words(passages[i].text[s:e]) for i, s, e in cands]
    meaning = None
    if embedder is not None and cands and claims:
        vecs = embedder.embed([text[c.start : c.end] for c in claims] + [passages[i].text[s:e] for i, s, e in cands])
        meaning = vecs[len(claims) :] @ vecs[: len(claims)].T  # [candidate, claim]
    for k, (claim, cw) in enumerate(zip(claims, claim_words, strict=True)):
        if not cw:
            claim.checked = False
            continue
        best, best_key = None, None
        for j, ((i, s, e), sw) in enumerate(zip(cands, cand_words, strict=True)):
            share = len(cw & sw) / len(cw)
            cos = float(meaning[j, k]) if meaning is not None else 0.0
            if share >= SUPPORT_WORDS or cos >= SUPPORT_MEANING:
                key = share + cos
                if best_key is None or key > best_key:
                    best, best_key = (i, s, e), key
        if best:
            claim.passage, claim.span = best[0], (best[1], best[2])
    return claims


# --- asking ---------------------------------------------------------------------------------------------


class Answerer:
    """Writes answers with the chat model, loaded on the first question and kept. One answer at a time:
    stop() ends the one being written (a new question does this first)."""

    def __init__(self, load):
        self._load = load  # () -> the chat model (models.load_chat)
        self._llm = None
        self._lock = threading.Lock()
        self._stopped = False

    @property
    def loaded(self) -> bool:
        return self._llm is not None

    def answer(self, question: str, found: Found, embedder=None, on_text=lambda text: None) -> Answer | None:
        """The answer, with on_text(the text so far) as it's written. None if stopped."""
        from .llm import Stopped

        start = time.perf_counter()
        self._stopped = False
        passages = found.results[:ANSWER_PASSAGES]
        if not should_ask(found):
            text = NOT_FOUND
            on_text(text)
            return Answer(question, text, passages, [], time.perf_counter() - start, asked_model=False)
        with self._lock:
            if self._llm is None:
                self._llm = self._load()
        parts: list[str] = []
        try:
            for piece in self._llm.generate(*prompt(question, passages), ANSWER_TOKENS, lambda: self._stopped):
                parts.append(piece)
                on_text("".join(parts))
        except Stopped:
            return None
        if self._stopped:
            return None
        text = tidy("".join(parts))
        sources = find_sources(text, passages, embedder, question)
        return Answer(question, text, passages, sources, time.perf_counter() - start)

    def stop(self):
        self._stopped = True
        if self._llm is not None:
            self._llm.stop()

    def close(self):
        self.stop()
        with self._lock:
            if self._llm is not None:
                self._llm.close()
                self._llm = None


# --- saved questions ------------------------------------------------------------------------------------


def save(db, answer: Answer, shown: list[Result]) -> int:
    """Keep a question, its answer and the passages shown (so it reopens without asking the model again)."""
    ids = [r.chunk_id for r in answer.passages]
    sources = [
        [s.start, s.end, ids[s.passage] if s.backed else None, *(s.span or (None, None)), int(s.checked)]
        for s in answer.sentences
    ]
    with db:
        cur = db.execute(
            "INSERT INTO questions (asked, question, answer, passages, sources) VALUES (?, ?, ?, ?, ?)",
            (time.time(), answer.question, answer.text, json.dumps([r.chunk_id for r in shown]), json.dumps(sources)),
        )
    return cur.lastrowid


def recent(db, limit: int = 20) -> list[tuple[int, str]]:
    """(id, question) of the latest questions, newest first; a question asked again shows once."""
    rows = db.execute(
        "SELECT MAX(id) AS id, question FROM questions GROUP BY lower(trim(question)) ORDER BY MAX(asked) DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [(r["id"], r["question"]) for r in rows]


def load(db, question_id: int) -> tuple[Answer, list[Result]] | None:
    """A saved answer and the passages shown with it, or None. Passages removed since are left out, and a
    sentence they backed shows without a link (it isn't called unbacked: it was, when asked)."""
    row = db.execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone()
    if row is None:
        return None
    ids = json.loads(row["passages"])
    shown = _results(db, ids)
    spans = store.highlights(db, row["question"], [r.chunk_id for r in shown])
    for r in shown:
        r.spans = spans.get(r.chunk_id, [])
    by_id = {r.chunk_id: r for r in shown}
    read = [by_id[c] for c in ids[:ANSWER_PASSAGES] if c in by_id]
    place = {r.chunk_id: i for i, r in enumerate(read)}
    out = []
    for start, end, cid, s, e, checked in json.loads(row["sources"]):
        sentence = Sentence(start, end, checked=bool(checked))
        if cid is not None:
            if cid in place:
                sentence.passage, sentence.span = place[cid], (s, e)
            else:
                sentence.checked = False
        out.append(sentence)
    return Answer(row["question"], row["answer"], read, out, asked_model=False), shown
