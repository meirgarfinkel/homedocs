"""The passages in the database: saving a file's passages, removing them, and reading them back.

The keyword index (chunks_fts) keeps no copy of the text: it reads it from the chunks. So its rows are
added and removed here, alongside the passages, never left to triggers: removing a row needs exactly the
text it was added with, which must still be there when it's removed.
"""

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


def keyword_search(db: sqlite3.Connection, query: str, limit: int = 20) -> list[sqlite3.Row]:
    """Passages matching every word of query, best first (BM25). A simple search for now; search.py
    builds the real one."""
    words = [w.replace('"', "") for w in query.split()]
    match = " ".join(f'"{w}"' for w in words if w)
    if not match:
        return []
    return db.execute(
        "SELECT c.id, c.file_id, c.page, c.heading, c.text FROM chunks_fts "
        "JOIN chunks c ON c.id = chunks_fts.rowid WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?",
        (match, limit),
    ).fetchall()
