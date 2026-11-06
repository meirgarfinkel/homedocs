"""Answering a question from the passages search found, and finding what backs each sentence of the answer.

1. Ask the model at all? Not when nothing was found, or when even the best passage's re-ranker score is below
   NOT_FOUND_SCORE: then the answer is "Not in your files." at once.
2. The prompt: the best passages that fit in ANSWER_CHARS (pick()), numbered, with their file and place. The
   model is told to answer from them alone in a few sentences, or say "Not in your files." It isn't asked to
   cite: in the test (REQUIREMENTS.md), small models cited the wrong passage or ignored the instruction.
3. The answer streams in, a piece at a time.
4. Sources: each sentence of the answer is matched against every sentence of those passages, by the share of
   its words found there and by meaning (the embedder). The best match, if close enough, backs it; a sentence
   nothing backs is marked as not found in the files.

The chat model sits behind generate(instructions, rest, max_new, should_stop) -> text pieces, and stop(), so
tests use a fake (tests/fakes.py).
"""

import ast
import json
import re
import threading
import time
from dataclasses import dataclass, field

from . import store
from .config import (
    ANSWER_CHARS,
    ANSWER_TOKENS,
    NOT_FOUND_SCORE,
    SUPPORT_MEANING,
    SUPPORT_WORDS,
)
from .search import Found, Result, _results

NOT_FOUND = "Not in your files."

# Kept plain: each further rule was tried on its own with Qwen3.5-2B and kept only if the eval's tuning score
# rose (only the arithmetic one did; "a rule or a table's row settles it" and "ignore passages about other
# things", which helped Qwen3-1.7B, each cost it 2).
INSTRUCTIONS = (
    "You answer questions using only the numbered passages below, which come from the user's own files. "
    "Answer in one to three short sentences, using only facts stated in the passages. "
    "If the question needs arithmetic, work it out from the passages' numbers and give the result. "
    f'If the passages don\'t answer the question, reply exactly: "{NOT_FOUND}"'
)


@dataclass
class Sentence:
    """One sentence of an answer and what backs it."""

    start: int  # where it is in the answer's text
    end: int
    passage: int | None = None  # the backing passage: its place in Answer.passages (0 = the first), or None
    span: tuple[int, int] | None = None  # the backing sentence's place in that passage's text
    checked: bool = True  # False: nothing in it to check (like "Yes.")
    strength: float = 0.0  # how well the backing sentence matches: its share of the words plus their cosine

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


# the model saying "Not in your files." in its own words, about the passages (or files, documents) themselves:
# "...is not provided in the passages", "The provided passages do not state...". ("The policy does not cover
# it" is an answer, not this.)
_SOURCES = r"(?:provided\s+|given\s+)?(?:passages?|files?|documents?)"
_NO_ANSWER = re.compile(
    rf"\b(?:not|n't)\s+(?:\w+\s+)?(?:provided|specified|mentioned|stated|given|included|found|listed)\s+in\s+"
    rf"(?:the|your)\s+{_SOURCES}|\b(?:the|your)\s+{_SOURCES}\s+(?:do\s+not|does\s+not|don't|doesn't)\s+"
    r"(?:say|mention|state|specify|contain|include|provide|give|list)",
    re.I,
)


def is_not_found(text: str) -> bool:
    """The answer is "Not in your files.": it starts with that, ends with it after a hedge, or its first
    sentence says the passages don't hold the answer (a partial answer followed by one is kept)."""
    text = text.strip()
    key = NOT_FOUND.lower().rstrip(".")
    if text.lower().startswith(key) or text.lower().rstrip(". ").endswith(key):
        return True
    first = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0]
    return bool(_NO_ANSWER.search(first))


def should_ask(found: Found) -> bool:
    """Whether the model could find an answer in these passages (else it's "Not in your files." at once)."""
    if not found.results:
        return False
    if found.by_meaning:  # (the scores are the re-ranker's only then)
        # (the best of them: blending with the search's order can put a lower one first)
        return max(r.score for r in found.results) >= NOT_FOUND_SCORE
    return True


def pick(results: list[Result]) -> list[Result]:
    """The passages the model reads: the best ones that fit in ANSWER_CHARS, in rank order. One that doesn't fit
    is passed over (a shorter one after it may still fit); the best is always read, cut to fit if it must be."""
    picked, room = [], ANSWER_CHARS
    for r in results:
        if not picked or len(r.text) <= room:
            picked.append(r)
            room -= len(r.text)
    return picked


def prompt(question: str, passages: list[Result]) -> tuple[str, str]:
    """(instructions, rest) in Qwen's chat format, thinking off. The instructions are the same every time, so
    the model reads them once and keeps them."""
    instructions = f"<|im_start|>system\n{INSTRUCTIONS}<|im_end|>\n<|im_start|>user\nPassages:\n\n"
    blocks = []
    for i, r in enumerate(passages, 1):
        where = f" — {r.where()}" if r.where() else ""
        text = r.text if len(r.text) <= ANSWER_CHARS else r.text[:ANSWER_CHARS] + " …"
        blocks.append(f"[{i}] {r.name}{where}\n{text}")
    rest = (
        "\n\n".join(blocks)
        + f"\n\nQuestion: {question.strip()}<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
    )
    return instructions, rest


def tidy(text: str) -> str:
    """The model's answer, cleaned: a "Not in your files." with anything after it is just that."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    return NOT_FOUND if is_not_found(text) else fix_sums(text)


# --- arithmetic -----------------------------------------------------------------------------------------
# Small models find the right numbers but add them up wrong ("$300 + ($35 × 12) = $460"), so the app does the
# arithmetic the model writes out, and puts its own result in place of the model's.

_NUM = r"\$?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?"
_OP = r"\s*[+×x*/÷]\s*|\s+[-−–]\s+"  # (a minus needs spaces: "2022-2025" and "555-0175" aren't sums)
_SUM = re.compile(rf"(?<![\w$.,])(?P<expr>\(*\s*{_NUM}(?:\s*\)*(?:{_OP})\(*\s*{_NUM})+\s*\)*)\s*=\s*(?P<result>{_NUM})")
_TO_PY = str.maketrans({"×": "*", "x": "*", "÷": "/", "−": "-", "–": "-"})


def _value(expr: str, percent_of: bool) -> float | None:
    """What the written-out sum comes to (None if it isn't one Python can work out safely). percent_of:
    "10%" is a tenth (of an amount), not ten (percents added up)."""
    expr = re.sub(r"(\d+(?:\.\d+)?)%", r"(\1/100)" if percent_of else r"\1", expr.replace("$", "").replace(",", ""))
    expr = expr.translate(_TO_PY)
    while expr.count("(") > expr.count(")") and expr.lstrip().startswith("("):  # ("(1 + 2 = 3")
        expr = expr.lstrip()[1:]
    while expr.count(")") > expr.count("(") and expr.rstrip().endswith(")"):  # ("(that's 1 + 2) = 3")
        expr = expr.rstrip()[:-1]
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub)
    if not all(isinstance(n, allowed) for n in ast.walk(tree)):
        return None
    try:
        return float(eval(compile(tree, "sum", "eval"), {"__builtins__": {}}))  # (numbers and + - × / only)
    except ZeroDivisionError:
        return None


def _written(value: float, like: str, commas: bool) -> str:
    """value written the way the model wrote its result (like): "$" and "%" kept, cents for money."""
    money, pct = like.startswith("$"), like.endswith("%")
    rounded = round(value, 2)
    if rounded == int(rounded):
        digits = f"{int(rounded):,}" if commas else str(int(rounded))
    elif money:
        digits = f"{rounded:,.2f}" if commas else f"{rounded:.2f}"
    else:
        digits = (f"{rounded:,.2f}" if commas else f"{rounded:.2f}").rstrip("0")
    sign, digits = ("-", digits[1:]) if digits.startswith("-") else ("", digits)
    return f"{sign}{'$' if money else ''}{digits}{'%' if pct else ''}"


def fix_sums(text: str) -> str:
    """text with each written-out sum's result ("... = $460") replaced by what it comes to, in the same style;
    a later mention of the same wrong result is corrected too. Each sum sees the ones before it corrected."""
    at = 0
    while m := _SUM.search(text, at):
        old = m["result"]
        value = _value(m["expr"], percent_of=not old.endswith("%"))
        if value is None:
            at = m.end()
            continue
        new = _written(value, old, commas="," in m[0] or abs(value) >= 10_000)
        if new == old:
            at = m.end()
            continue
        head, tail = text[: m.start("result")], text[m.end("result") :]
        tail = re.sub(rf"(?<![\w$.,]){re.escape(old)}(?![\d%]|[.,]\d)", new, tail)  # (new has no backslashes)
        text = head + new + tail
        at = len(head) + len(new)
    return text


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
            claim.passage, claim.span, claim.strength = best[0], (best[1], best[2]), best_key
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
        passages = pick(found.results)
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
                on_text(fix_sums("".join(parts)))
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
    read = shown  # (the passages read were among those shown; sources are matched by passage)
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
