"""The passages in the database: saving a file's passages, removing them, and reading them back.

The keyword index (chunks_fts) keeps no copy of the text: it reads it from the chunks. So its rows are
added and removed here, alongside the passages, never left to triggers: removing a row needs exactly the
text it was added with, which must still be there when it's removed.
"""

import os
import re
import sqlite3

from .passages import Passage


def _unindex(db: sqlite3.Connection, file_id: int):
    """Take a file's passages out of the keyword index (before the passages themselves go)."""
    db.execute(
        "INSERT INTO chunks_fts(chunks_fts, rowid, name, heading, text) "
        "SELECT 'delete', id, name, heading, text FROM chunk_search WHERE file_id = ?",
        (file_id,),
    )


def drop_passages(db: sqlite3.Connection, file_id: int):
    _unindex(db, file_id)
    db.execute("DELETE FROM vectors WHERE chunk_id IN (SELECT id FROM chunks WHERE file_id = ?)", (file_id,))
    db.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))


def save_passages(db: sqlite3.Connection, file_id: int, passages: list[Passage]):
    """Replace a file's passages (call inside the transaction that marks the file indexed)."""
    drop_passages(db, file_id)
    db.executemany(
        "INSERT INTO chunks (file_id, seq, page, page_end, heading, char_start, char_end, text) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(file_id, i, p.page, p.page_end, p.heading, p.start, p.end, p.text) for i, p in enumerate(passages)],
    )
    db.execute(
        "INSERT INTO chunks_fts(rowid, name, heading, text) "
        "SELECT id, name, heading, text FROM chunk_search WHERE file_id = ?",
        (file_id,),
    )


def drop_file(db: sqlite3.Connection, file_id: int):
    drop_passages(db, file_id)
    db.execute("DELETE FROM files WHERE id = ?", (file_id,))


def rename_file(db: sqlite3.Connection, file_id: int, path: str, name: str):
    """A file's new path. Its name is in the keyword index, so its passages are indexed again under it."""
    _unindex(db, file_id)
    db.execute("UPDATE files SET path = ?, name = ? WHERE id = ?", (path, name, file_id))
    db.execute(
        "INSERT INTO chunks_fts(rowid, name, heading, text) "
        "SELECT id, name, heading, text FROM chunk_search WHERE file_id = ?",
        (file_id,),
    )


def passages_of(db: sqlite3.Connection, file_id: int) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT id, seq, page, page_end, heading, char_start, char_end, text FROM chunks "
        "WHERE file_id = ? ORDER BY seq",
        (file_id,),
    ).fetchall()


def _match(words: list[str], every: bool) -> str:
    """An FTS5 query for words: each quoted (so nothing in them reads as query syntax), joined by AND or OR."""
    quoted = [f'"{w}"' for w in (w.replace('"', "") for w in words) if w]
    return (" AND " if every else " OR ").join(quoted)


# Words too common to search for or highlight ("what is the deductible" looks for "deductible"). Search by
# meaning still reads the whole question.
_COMMON_WORDS = """a about above after again all am an and any are as at be because been before being below
between both but by can could did do does doing down during each few for from further had has have having he
her here hers him his how i if in into is it its itself just me more most my no nor not now of off on once only
or other our ours out over own same she should so some such than that the their theirs them then there these
they this those through to too under until up very was we were what when where which while who whom why will
with would you your yours"""
COMMON = frozenset(_COMMON_WORDS.split())


def query_words(text: str) -> list[str]:
    """The words of text to search for: all but the common ones (all of them, if that's all there is)."""
    words = re.findall(r"\w+", text.lower())
    return [w for w in words if w not in COMMON] or words


def keyword_search(db: sqlite3.Connection, text: str, limit: int = 20, every: bool = True) -> list[sqlite3.Row]:
    """Passages matching text's words (every one, or any: a question's), best first (BM25)."""
    match = _match(query_words(text), every)
    if not match:
        return []
    return db.execute(
        "SELECT c.id, c.file_id, c.page, c.heading, c.text FROM chunks_fts "
        "JOIN chunks c ON c.id = chunks_fts.rowid WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?",
        (match, limit),
    ).fetchall()


def highlights(db: sqlite3.Connection, text: str, chunk_ids: list[int]) -> dict[int, list[tuple[int, int]]]:
    """Where text's words (in any of their forms: the keyword index's own matching) are in each passage:
    chunk id -> [(start, end)] in its text."""
    match = _match(query_words(text), every=False)
    if not match or not chunk_ids:
        return {}
    out = {}
    rows = db.execute(
        f"SELECT rowid, highlight(chunks_fts, 2, char(1), char(2)) FROM chunks_fts WHERE chunks_fts MATCH ? "
        f"AND rowid IN ({','.join('?' * len(chunk_ids))})",
        (match, *chunk_ids),
    )
    for rowid, marked in rows:
        spans, pos, start = [], 0, None
        for ch in marked:
            if ch == "\x01":
                start = pos
            elif ch == "\x02":
                spans.append((start, pos))
            else:
                pos += 1
        if spans:
            out[rowid] = spans
    return out


# --- vectors -----------------------------------------------------------------------------------------


def embed_text(name: str, heading: str | None, text: str) -> str:
    """What the embedding model reads for a passage: where it's from, then the passage, so a passage
    knows what it's about even when its own words don't say ("the deductible is $500")."""
    title = os.path.splitext(name)[0]
    return f"{title} > {heading}\n\n{text}" if heading else f"{title}\n\n{text}"


def drop_other_vectors(db: sqlite3.Connection, model: str) -> int:
    """Remove vectors another model made (they can't be compared with this one's)."""
    with db:
        return db.execute("DELETE FROM vectors WHERE model != ?", (model,)).rowcount


def without_vectors(db: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    """Passages with no vector yet, newest files first: id, and the text to embed."""
    return db.execute(
        "SELECT c.id, f.name, c.heading, c.text FROM chunks c JOIN files f ON f.id = c.file_id "
        "WHERE c.id NOT IN (SELECT chunk_id FROM vectors) ORDER BY f.mtime DESC, c.file_id, c.seq LIMIT ?",
        (limit,),
    ).fetchall()


def count_passages(db: sqlite3.Connection) -> int:
    return db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]


def count_without_vectors(db: sqlite3.Connection) -> int:
    return db.execute("SELECT COUNT(*) FROM chunks WHERE id NOT IN (SELECT chunk_id FROM vectors)").fetchone()[0]


def digest(text: str) -> bytes:
    """A fingerprint of the text a passage's vector is made from: the same text, the same vector."""
    import hashlib

    return hashlib.blake2b(text.encode("utf-8"), digest_size=16).digest()


def known_vectors(db: sqlite3.Connection, model: str, digests) -> dict[bytes, bytes]:
    """{digest: vector} for the texts this model already made a vector of (copies of a passage)."""
    digests = list(set(digests))
    marks = ",".join("?" * len(digests))
    return dict(
        db.execute(f"SELECT digest, vec FROM vectors WHERE model = ? AND digest IN ({marks})", (model, *digests))
    )


def save_vectors(db: sqlite3.Connection, model: str, chunk_ids: list[int], vecs, digests: list[bytes]):
    """Save vectors for passages (any since removed are skipped). vecs: float32 rows, or their bytes."""
    import numpy as np

    rows = [v if isinstance(v, bytes) else np.asarray(v, dtype=np.float32).tobytes() for v in vecs]
    with db:
        db.executemany(
            "INSERT OR REPLACE INTO vectors (chunk_id, model, vec, digest) SELECT ?, ?, ?, ? WHERE EXISTS "
            "(SELECT 1 FROM chunks WHERE id = ?)",
            [(cid, model, rows[i], digests[i], cid) for i, cid in enumerate(chunk_ids)],
        )


class VectorIndex:
    """Every passage's vector in one matrix, for searching by meaning: one matrix product per question."""

    def __init__(self, ids, matrix):
        self.ids, self.matrix = ids, matrix

    @classmethod
    def load(cls, db: sqlite3.Connection, model: str, dim: int):
        import numpy as np

        rows = db.execute("SELECT chunk_id, vec FROM vectors WHERE model = ? ORDER BY chunk_id", (model,)).fetchall()
        ids = np.fromiter((r[0] for r in rows), dtype=np.int64, count=len(rows))
        matrix = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float32).reshape(len(rows), dim)
        return cls(ids, matrix)

    def __len__(self):
        return len(self.ids)

    def search(self, vec, k: int) -> list[tuple[int, float]]:
        """The k passages nearest vec: (chunk id, cosine similarity), nearest first."""
        import numpy as np

        if not len(self.ids):
            return []
        scores = self.matrix @ vec
        k = min(k, len(scores))
        best = np.argpartition(-scores, k - 1)[:k]
        best = best[np.argsort(-scores[best])]
        return [(int(self.ids[i]), float(scores[i])) for i in best]
