"""Finding the passages that answer a question.

1. Keyword: the passages with any of the question's words (in any of their forms), best first by BM25.
2. Meaning: the passages whose vectors are nearest the question's.
3. Merge: reciprocal rank fusion. Each passage scores 1 / (RRF_K + its place) in each list it's in, summed,
   so one near the top of either list, or fairly high in both, comes first. (Scores from the two searches
   can't be compared directly; places can.)
4. Re-rank: the re-ranker reads the question with each of the best RERANK_CANDIDATES and scores how well it
   answers it. That order is final.
5. The same passage from identical files in two places is shown once.

Without the models (still downloading), it's keyword search alone, in BM25's order.
"""

import threading
import time
from dataclasses import dataclass, field

from . import store
from .config import KEYWORD_CANDIDATES, RERANK_CANDIDATES, RRF_K, SHOW_RESULTS, VECTOR_CANDIDATES
from .db import open_db


@dataclass
class Result:
    chunk_id: int
    path: str
    name: str
    page: int | None
    page_end: int | None
    heading: str | None
    text: str
    offline: bool
    score: float  # the re-ranker's (higher is better), or the merged rank's without it
    spans: list[tuple[int, int]] = field(default_factory=list)  # the question's words in text, to highlight

    def where(self) -> str:
        """Its place in the file, in words: "Page 4", "Pages 4-5", or its headings."""
        if self.page:
            return f"Page {self.page}" if self.page_end in (None, self.page) else f"Pages {self.page}-{self.page_end}"
        return self.heading or ""


@dataclass
class Found:
    results: list[Result]
    seconds: float
    by_meaning: bool  # False: keyword search only (the models aren't ready)


def fuse(*ranked: list[int], k: int = RRF_K) -> list[int]:
    """Reciprocal rank fusion of ranked lists of ids, best first."""
    scores: dict[int, float] = {}
    for ids in ranked:
        for place, cid in enumerate(ids):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + place + 1)
    return sorted(scores, key=lambda c: -scores[c])


class Searcher:
    """Answers search() with whatever's ready: the embedder and re-ranker once they're loaded, and the vector
    index, reloaded by refresh() after indexing changes it. Safe to use from several threads."""

    def __init__(self, embedder=None, reranker=None):
        self.embedder, self.reranker = embedder, reranker
        self.index = None
        self._lock = threading.Lock()

    def set_models(self, embedder, reranker):
        self.embedder, self.reranker = embedder, reranker
        self.refresh()

    def refresh(self):
        """Load the vectors again (after indexing added or removed some)."""
        if self.embedder is None:
            return
        db = open_db()
        try:
            index = store.VectorIndex.load(db, self.embedder.key, self.embedder.dim)
        finally:
            db.close()
        with self._lock:
            self.index = index

    def search(self, question: str) -> Found:
        start = time.perf_counter()
        embedder, reranker, index = self.embedder, self.reranker, self.index
        db = open_db()
        try:
            keyword = [r["id"] for r in store.keyword_search(db, question, KEYWORD_CANDIDATES, every=False)]
            by_meaning = embedder is not None and index is not None and len(index) > 0
            meaning = (
                [c for c, _ in index.search(embedder.embed_question(question), VECTOR_CANDIDATES)] if by_meaning else []
            )
            ids = fuse(keyword, meaning)[:RERANK_CANDIDATES]
            results = _results(db, ids)
            if reranker is not None and by_meaning and results:
                for r, s in zip(results, reranker.score(question, [r.text for r in results]), strict=True):
                    r.score = s
                results.sort(key=lambda r: -r.score)
            results = _unique(results)[:SHOW_RESULTS]
            spans = store.highlights(db, question, [r.chunk_id for r in results])
            for r in results:
                r.spans = spans.get(r.chunk_id, [])
        finally:
            db.close()
        return Found(results, time.perf_counter() - start, by_meaning)


def _results(db, ids: list[int]) -> list[Result]:
    """The passages with these ids, in this order (any removed meanwhile left out)."""
    if not ids:
        return []
    rows = db.execute(
        "SELECT c.id, f.path, f.name, c.page, c.page_end, c.heading, c.text, f.offline FROM chunks c "
        f"JOIN files f ON f.id = c.file_id WHERE c.id IN ({','.join('?' * len(ids))})",
        ids,
    ).fetchall()
    by_id = {r["id"]: r for r in rows}
    out = []
    for place, cid in enumerate(ids):
        r = by_id.get(cid)
        if r is not None:
            out.append(
                Result(
                    cid,
                    r["path"],
                    r["name"],
                    r["page"],
                    r["page_end"],
                    r["heading"],
                    r["text"],
                    bool(r["offline"]),
                    -place,
                )
            )
    return out


def _unique(results: list[Result]) -> list[Result]:
    seen, out = set(), []
    for r in results:
        if r.text not in seen:
            seen.add(r.text)
            out.append(r)
    return out
