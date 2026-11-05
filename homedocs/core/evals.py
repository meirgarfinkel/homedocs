"""How accurate HomeDocs is, and which step loses the questions it gets wrong (a development tool: see
"Evals and tuning" in ARCHITECTURE.md).

    python -m homedocs.core.evals [--search] [--held-back] [--only TYPE] [--set NAME=VALUE ...] [--verbose]

The documents (tests/evals: government PDFs downloaded once, household PDFs made from text) are indexed into
a data folder under build/evals, kept for the next run while the splitting settings and code are the same.
Each question then goes through the steps, and a wrong one is blamed on the first step that lost it:

1. split: each passage the question needs (its quote) is whole inside one passage
2. search: all of them are among the passages given to the re-ranker
3. pick: all of them are among the passages the model reads (answer.pick)
4. answer: the answer states every fact (or says "Not in your files." when it should)

--set changes a setting in core/config.py for this run (in every module that uses it), to try it.
"""

import argparse
import hashlib
import json
import os
import re
import statistics
import sys
import time
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVALS = ROOT / "tests" / "evals"
BUILD = ROOT / "build" / "evals"
STEPS = ("split", "search", "pick", "answer")
# what decides how files are split into passages: a change to any of these means indexing again
SPLIT_SETTINGS = ("PASSAGE_WORDS", "OVERLAP_WORDS", "MIN_PASSAGE_WORDS", "MIN_SECTION_WORDS", "MAX_PASSAGE_CHARS")
SPLIT_CODE = ("parsers.py", "passages.py", "indexer.py", "store.py")


def norm(text: str) -> str:
    """Text to compare: lower case, one kind of quote mark, spaces collapsed, no commas inside numbers."""
    text = text.lower().replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"(?<=\d),(?=\d)", "", text)
    return " ".join(text.split())


# --- the questions ---------------------------------------------------------------------------------------


@dataclass
class Question:
    id: int
    question: str
    type: str
    needs: list[dict]  # {"file", "quote"}: a quote, or a list of quotes saying the same thing in different places
    facts: list[str]  # patterns a right answer matches (all of them)
    held_back: bool

    @property
    def answerable(self) -> bool:
        return self.type != "not-found"


def load_questions(path=EVALS / "questions.jsonl", held_back=False, only=None) -> list[Question]:
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            q = Question(**json.loads(line))
            if q.held_back == held_back and (only is None or q.type == only):
                out.append(q)
    return out


# --- the documents ---------------------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch_government(dest: Path, sources=EVALS / "sources.json") -> list[Path]:
    """The government PDFs, downloaded once and checked."""
    from .util import open_url

    dest.mkdir(parents=True, exist_ok=True)
    out = []
    for s in json.loads(Path(sources).read_text())["files"]:
        path = dest / s["file"]
        if not path.exists():
            print(f"Downloading {s['url']}", flush=True)
            req = urllib.request.Request(s["url"], headers={"User-Agent": "homedocs-evals"})
            with open_url(req, 120) as r:
                data = r.read()
            path.with_suffix(".part").write_bytes(data)
            path.with_suffix(".part").replace(path)
        if _sha256(path) != s["sha256"]:
            raise SystemExit(f"{path} isn't the file the questions were written for (sha256 differs): {s['title']}")
        out.append(path)
    return out


def make_documents(dest: Path, government=True) -> Path:
    """All the eval's documents in dest (household made fresh each time; it's quick)."""
    sys.path.insert(0, str(ROOT))
    from tests.evals.make_docs import make_all

    make_all(dest / "household")
    if government:
        fetch_government(dest / "gov")
    return dest


# --- settings ----------------------------------------------------------------------------------------------


def set_setting(name: str, value):
    """Change a setting of core/config.py in config and in every module that imported it by name."""
    from . import config

    if not hasattr(config, name):
        raise SystemExit(f"There's no setting called {name} in core/config.py.")
    old = getattr(config, name)
    value = type(old)(value) if not isinstance(value, type(old)) else value
    for mod in list(sys.modules.values()):
        if getattr(mod, "__name__", "").startswith("homedocs.core") and getattr(mod, name, None) is old:
            setattr(mod, name, value)
    return value


def index_key(docs: Path) -> str:
    """What the index depends on: the documents, the splitting settings and code, and the embedder."""
    from . import config

    h = hashlib.sha256()
    for p in sorted(docs.rglob("*")):
        if p.is_file():
            h.update(f"{p.relative_to(docs)}:{_sha256(p)}".encode())
    for name in SPLIT_SETTINGS:
        h.update(f"{name}={getattr(config, name)}".encode())
    for name in SPLIT_CODE:
        h.update((Path(__file__).parent / name).read_bytes())
    return h.hexdigest()[:12]


def build_index(docs: Path, embedder, data: Path, models_from: Path | None = None):
    """Index docs into the data folder data (HOMEDOCS_HOME from now on), unless it was already done."""
    os.environ["HOMEDOCS_HOME"] = str(data)
    data.mkdir(parents=True, exist_ok=True)
    if models_from is not None and not (data / "models").exists():
        (data / "models").symlink_to(models_from, target_is_directory=True)
    done = data / "indexed"
    if done.exists():
        return
    from . import indexer, library

    library.add_folder(docs)
    start = time.perf_counter()
    info = indexer.index_pass()
    embedded = indexer.embed_pass(embedder)
    print(f"Indexed {info['read']} files ({embedded} passages) in {time.perf_counter() - start:.0f} s", flush=True)
    done.write_text("yes")


# --- one question ---------------------------------------------------------------------------------------


@dataclass
class Outcome:
    q: Question
    lost_at: str | None = None  # the first step that lost it, or None (right)
    steps: dict = field(default_factory=dict)  # step -> passed
    answer: str = ""
    first_word: float | None = None  # seconds from asking to the first word of the answer
    search_seconds: float = 0.0
    sentences: int = 0  # the answer's sentences with something to check
    sourced: int = 0  # of those, the ones a passage backs
    wrong_file: int = 0  # backed by a passage from a file the question doesn't need

    @property
    def right(self) -> bool:
        return self.lost_at is None


class Passages:
    """The passages holding each quote (normalised text, by file name), from the database."""

    def __init__(self, db):
        self.by_file: dict[str, list[tuple[int, str]]] = {}
        for r in db.execute("SELECT c.id, c.text, f.name FROM chunks c JOIN files f ON f.id = c.file_id"):
            self.by_file.setdefault(r["name"], []).append((r["id"], norm(r["text"])))

    def holding(self, need: dict) -> set[int]:
        """The passages holding the quote (or any of them, when it's a list: the same fact in more than one
        place)."""
        quotes = [norm(x) for x in _quotes(need)]
        return {cid for cid, text in self.by_file.get(need["file"], []) if any(x in text for x in quotes)}


def _quotes(need: dict) -> list[str]:
    return need["quote"] if isinstance(need["quote"], list) else [need["quote"]]


def right_answer(q: Question, text: str) -> bool:
    from .answer import is_not_found

    if not q.answerable:
        return is_not_found(text)
    if is_not_found(text):
        return False
    said = norm(text)
    return all(re.search(f, said) for f in q.facts)


def ask(q: Question, searcher, answerer, embedder, passages: Passages) -> Outcome:
    """One question through the steps (answerer None: stop after pick)."""
    from .answer import pick

    out = Outcome(q)
    holding = [passages.holding(n) for n in q.needs]
    start = time.perf_counter()
    found = searcher.search(q.question)
    out.search_seconds = found.seconds
    candidates = set(found.candidates)
    picked = {r.chunk_id for r in pick(found.results)}
    out.steps = {
        "split": all(holding),
        "search": all(h & candidates for h in holding),
        "pick": all(h & picked for h in holding),
    }
    if answerer is not None:
        first = []

        def on_text(text):
            if not first and text.strip():
                first.append(time.perf_counter() - start)

        a = answerer.answer(q.question, found, embedder, on_text)
        out.answer = a.text
        out.first_word = first[0] if first else None
        out.steps["answer"] = right_answer(q, a.text)
        needed_files = {n["file"] for n in q.needs}
        checked = [s for s in a.sentences if s.checked]
        out.sentences = len(checked)
        out.sourced = sum(s.backed for s in checked)
        out.wrong_file = sum(
            1 for s in checked if s.backed and q.answerable and a.passages[s.passage].name not in needed_files
        )
    if q.answerable:
        out.lost_at = next((s for s in STEPS if s in out.steps and not out.steps[s]), None)
    else:
        out.lost_at = None if out.steps.get("answer", True) else "answer"
    return out


# --- the report -----------------------------------------------------------------------------------------


def report(outcomes: list[Outcome], answered: bool) -> str:
    n = len(outcomes)
    lines = []
    if answered:
        right = sum(o.right for o in outcomes)
        lines.append(f"Right: {right}/{n} ({100 * right / max(n, 1):.0f}%)")
    else:
        found = sum(o.steps.get("pick", False) for o in outcomes if o.q.answerable)
        answerable = sum(o.q.answerable for o in outcomes)
        lines.append(f"Needed passages read by the model: {found}/{answerable} answerable questions")
    lost = Counter(o.lost_at for o in outcomes if o.lost_at and (answered or o.lost_at != "answer"))
    lines.append("Lost at: " + ", ".join(f"{s} {lost.get(s, 0)}" for s in STEPS if answered or s != "answer"))
    lines.append("")
    lines.append(f"{'type':<14}{'n':>4}{'right':>7}  lost at")
    for kind in sorted({o.q.type for o in outcomes}):
        mine = [o for o in outcomes if o.q.type == kind]
        ok = sum(o.right if answered else (o.lost_at in (None, "answer")) for o in mine)
        where = Counter(o.lost_at for o in mine if o.lost_at and (answered or o.lost_at != "answer"))
        lines.append(f"{kind:<14}{len(mine):>4}{ok:>7}  " + ", ".join(f"{s} {c}" for s, c in where.items()))
    lines.append("")
    took = sorted(o.search_seconds for o in outcomes)
    lines.append(f"Search: median {statistics.median(took):.2f} s, slowest {took[-1]:.2f} s")
    if answered:
        nf = [o for o in outcomes if not o.q.answerable]
        lines.append(f"'Not in your files' when it should: {sum(o.right for o in nf)}/{len(nf)}")
        wrongly = sum(1 for o in outcomes if o.q.answerable and o.answer and _not_found(o.answer))
        lines.append(f"'Not in your files' when the answer was there: {wrongly}")
        sentences = sum(o.sentences for o in outcomes)
        lines.append(f"Sentences with a source: {sum(o.sourced for o in outcomes)}/{sentences}")
        lines.append(f"Sentences sourced from a file the question doesn't need: {sum(o.wrong_file for o in outcomes)}")
        times = sorted(o.first_word for o in outcomes if o.first_word is not None)
        if times:
            lines.append(f"First word: median {statistics.median(times):.1f} s, slowest {times[-1]:.1f} s")
    return "\n".join(lines)


def _not_found(text: str) -> bool:
    from .answer import is_not_found

    return is_not_found(text)


def failures(outcomes: list[Outcome]) -> str:
    lines = []
    for o in outcomes:
        if not o.right and (o.lost_at != "answer" or o.answer):
            lines.append(f"[{o.q.id}] {o.q.type}, lost at {o.lost_at}: {o.q.question}")
            if o.answer:
                lines.append(f"      answer: {' '.join(o.answer.split())[:300]}")
    return "\n".join(lines)


# --- running it -----------------------------------------------------------------------------------------


def check_quotes(questions: list[Question], docs: Path) -> list[str]:
    """Quotes that aren't in their file at all (mistakes in the questions, not in HomeDocs)."""
    from .parsers import read

    texts, problems = {}, []
    for q in questions:
        for n in q.needs:
            if n["file"] not in texts:
                path = next(docs.rglob(n["file"]), None)
                texts[n["file"]] = norm(read(path).text) if path else None
            text = texts[n["file"]]
            if text is None:
                problems.append(f"[{q.id}] there's no file {n['file']}")
            else:
                problems += [f"[{q.id}] {n['file']} doesn't say: {x}" for x in _quotes(n) if norm(x) not in text]
    return problems


def run(questions, docs: Path, data: Path, embedder, reranker, load_chat=None, models_from=None, verbose=False):
    """Every question through the steps; load_chat None: no answers (search only)."""
    from .answer import Answerer
    from .db import open_db
    from .search import Searcher

    build_index(docs, embedder, data, models_from)
    searcher = Searcher(embedder, reranker)
    searcher.refresh()
    answerer = Answerer(load_chat) if load_chat else None
    db = open_db()
    try:
        passages = Passages(db)
    finally:
        db.close()
    outcomes = []
    for i, q in enumerate(questions, 1):
        o = ask(q, searcher, answerer, embedder, passages)
        outcomes.append(o)
        if verbose:
            mark = "ok" if o.right or (not load_chat and o.lost_at in (None, "answer")) else o.lost_at
            print(f"{i}/{len(questions)} [{q.id}] {mark}", flush=True)
    if answerer is not None:
        answerer.close()
    return outcomes


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m homedocs.core.evals", description=__doc__.split("\n")[0])
    p.add_argument("--search", action="store_true", help="stop after picking passages (no chat model)")
    p.add_argument("--held-back", action="store_true", help="the held-back questions instead of the tuning ones")
    p.add_argument("--only", metavar="TYPE", help="questions of this type only")
    p.add_argument("--set", action="append", default=[], metavar="NAME=VALUE", help="change a setting")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args(argv)

    from . import config, models

    models_from = config.data_dir() / "models"  # (before HOMEDOCS_HOME points at the eval's folder)
    for item in args.set:
        name, _, value = item.partition("=")
        print(f"{name} = {set_setting(name, value)}")
    questions = load_questions(held_back=args.held_back, only=args.only)
    docs = make_documents(BUILD / "documents")
    problems = check_quotes(questions, docs)
    if problems:
        raise SystemExit("The questions quote text their files don't have:\n" + "\n".join(problems))
    data = BUILD / f"data-{index_key(docs)}"
    if not (models.EMBEDDER.ready() and models.RERANKER.ready()):
        raise SystemExit("The search models aren't downloaded: open HomeDocs once first.")
    embedder, reranker = models.Embedder(), models.Reranker()
    outcomes = run(
        questions,
        docs,
        data,
        embedder,
        reranker,
        None if args.search else models.load_chat,
        models_from,
        args.verbose,
    )
    print()
    print(report(outcomes, answered=not args.search))
    print()
    print(failures(outcomes))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    saved = BUILD / f"results-{stamp}.json"
    saved.write_text(
        json.dumps(
            {
                "settings": args.set,
                "held_back": args.held_back,
                "search_only": args.search,
                "outcomes": [
                    {
                        "id": o.q.id,
                        "lost_at": o.lost_at,
                        "steps": o.steps,
                        "answer": o.answer,
                        "first_word": o.first_word,
                    }
                    for o in outcomes
                ],
            },
            indent=1,
        )
    )
    print(f"\nSaved: {saved}")


if __name__ == "__main__":
    main()
