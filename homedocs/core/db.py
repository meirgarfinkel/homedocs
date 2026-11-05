"""The database (one SQLite file): its schema, and bringing an older one up to date.

The schema version is SQLite's user_version. A new database gets TABLES and INDEXES. An older one is
first backed up to backups/, then upgraded inside a single locked transaction, so a failed upgrade
leaves it untouched. A database from a newer HomeDocs is refused, never modified.

To change the schema: change TABLES (or INDEXES), bump VERSION, and add to UPGRADES the step that
brings a database of the version before up to it. Never edit a step that has shipped.
"""

import datetime as dt
import sqlite3

from .config import data_dir, db_file

VERSION = 6

# The whole schema, with a comment on every column. (Only SQL that SQLite 3.34 knows: the Linux build's.)
TABLES: dict[str, str] = {
    "files": """
        CREATE TABLE files (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,      -- the full path
            name TEXT NOT NULL,             -- the file name: searched along with each passage
            size INTEGER NOT NULL,          -- bytes, and
            mtime REAL NOT NULL,            -- modified time, when last looked at: unchanged means skip it
            hash TEXT,                      -- sha256 of the contents when last read (NULL: never read)
            status TEXT NOT NULL,           -- 'pending' (to read), 'indexed', 'failed' or 'skipped'
            error TEXT,                     -- why it failed or was skipped
            offline INTEGER NOT NULL DEFAULT 0,  -- 1: its folder can't be reached now (the passages stay)
            indexed_at REAL                 -- when it was last read
        )""",
    "chunks": """
        CREATE TABLE chunks (
            id INTEGER PRIMARY KEY,
            file_id INTEGER NOT NULL REFERENCES files(id),
            seq INTEGER NOT NULL,           -- its place in the file: 0, 1, 2...
            page INTEGER,                   -- the PDF pages it starts and ends on (from 1); NULL for other
            page_end INTEGER,               -- files
            heading TEXT,                   -- the headings it's under, like "Coverage > Deductible", or NULL
            char_start INTEGER NOT NULL,    -- where it starts and ends in the file's text (as parsers.py
            char_end INTEGER NOT NULL,      -- reads it)
            text TEXT NOT NULL
        )""",
    # What keyword search reads: each passage with its file name and headings
    "chunk_search": """
        CREATE VIEW chunk_search AS
        SELECT c.id, c.file_id, f.name, COALESCE(c.heading, '') AS heading, c.text
        FROM chunks c JOIN files f ON f.id = c.file_id""",
    # The keyword index, its text kept in chunks (not copied). Its rows are added and removed with the
    # passages, in the same transaction, by store.py: never with triggers, since removing a row needs the
    # exact text it was added with.
    "chunks_fts": """
        CREATE VIRTUAL TABLE chunks_fts USING fts5(
            name, heading, text, content='chunk_search', content_rowid='id',
            tokenize='porter unicode61 remove_diacritics 2'
        )""",
    "vectors": """
        CREATE TABLE vectors (
            chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id),
            model TEXT NOT NULL,            -- the model that made it (models.Model.key): another model's are
                                            -- made again, never mixed
            vec BLOB NOT NULL,              -- float32 numbers, unit length
            digest BLOB NOT NULL            -- a fingerprint of the text the model read (store.digest): a
                                            -- passage with the same text (a copy of a file) reuses the vector
        )""",
    "questions": """
        CREATE TABLE questions (
            id INTEGER PRIMARY KEY,
            asked REAL NOT NULL,            -- when (Unix time)
            question TEXT NOT NULL,
            answer TEXT NOT NULL,           -- the model's answer, or "Not in your files."
            passages TEXT NOT NULL,         -- the passages shown, best first: a JSON list of chunk ids
            sources TEXT NOT NULL           -- each sentence of the answer and what backs it: a JSON list of
                                            -- [start, end, chunk id or null, start, end, checked]: the
                                            -- sentence in the answer, the passage backing it and where in
                                            -- that passage, and 0 if it had nothing to check (like "Yes.")
        )""",
}

INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS chunks_file ON chunks(file_id, seq)",
    "CREATE INDEX IF NOT EXISTS files_status ON files(status)",
    "CREATE INDEX IF NOT EXISTS questions_asked ON questions(asked)",
    "CREATE INDEX IF NOT EXISTS vectors_digest ON vectors(model, digest)",
]


class DatabaseVersionError(RuntimeError):
    """The database is from a newer HomeDocs, or from one too old to bring up to date."""


def columns(db, table) -> list[str]:
    return [r[1] for r in db.execute(f"PRAGMA table_info({table})")]


def _to_2(db):
    """Version 1 (0.1.0) had no tables: indexing's arrive."""
    for create in V2_TABLES.values():
        db.execute(create)


def _to_3(db):
    """Vectors, for searching by meaning."""
    db.execute(V3_VECTORS)


def _to_4(db):
    """Questions asked and their answers."""
    db.execute(TABLES["questions"])


def _to_5(db):
    """Passages are shorter now (0.5.0): every file read is read again, its old passages searched until then."""
    db.execute("UPDATE files SET status = 'pending', hash = NULL WHERE status = 'indexed'")


def _to_6(db):
    """Each vector keeps a fingerprint of its text, so copies reuse it (0.9.0). The old ones are another model's
    (arctic-embed-xs replaced bge-small), so they're dropped: every passage is embedded again."""
    db.execute("DROP TABLE vectors")
    db.execute(TABLES["vectors"])


V3_VECTORS = """
        CREATE TABLE vectors (
            chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id),
            model TEXT NOT NULL,
            vec BLOB NOT NULL
        )"""
# As version 2 had them. (If one of these tables changes, its old CREATE goes here, written out.)
V2_TABLES = {name: TABLES[name] for name in ("files", "chunks", "chunk_search", "chunks_fts")}

UPGRADES = {1: _to_2, 2: _to_3, 3: _to_4, 4: _to_5, 5: _to_6}  # version: the step that brings it up to the next one


def migrate(db: sqlite3.Connection, path):
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version == VERSION:
        return
    if version > VERSION:
        raise DatabaseVersionError(
            f"{path} was upgraded by a newer HomeDocs (database version {version}; this one knows up to "
            f"{VERSION}). Use the newer version, or restore a copy from {path.parent / 'backups'}."
        )
    if db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]:
        backup(db, version)
    upgrade(db)


def upgrade(db: sqlite3.Connection):
    """Bring a database up to VERSION, all in one transaction (no backup: see migrate)."""
    keys = db.execute("PRAGMA foreign_keys").fetchone()[0]
    db.execute("PRAGMA foreign_keys = OFF")  # (tables are rebuilt; only possible outside a transaction)
    db.execute("BEGIN IMMEDIATE")  # one upgrader at a time: two windows may open it together
    try:
        version = db.execute("PRAGMA user_version").fetchone()[0]  # someone may have upgraded meanwhile
        empty = not db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        if version == 0 and empty:
            for create in TABLES.values():
                db.execute(create)
        elif version < VERSION:
            if version not in UPGRADES:
                raise DatabaseVersionError(
                    f"This database (version {version}) is too old for this HomeDocs to bring up to date."
                )
            while version < VERSION:
                UPGRADES[version](db)
                version += 1
        for create in INDEXES:
            db.execute(create)
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise DatabaseVersionError("The upgrade would leave rows pointing at others that aren't there.")
        db.execute(f"PRAGMA user_version = {VERSION}")
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.execute(f"PRAGMA foreign_keys = {keys}")


def backup(db: sqlite3.Connection, version: int, why: str = ""):
    """A copy of the database in the backups folder (why: a word for its name, like before-reset)."""
    folder = data_dir() / "backups"
    folder.mkdir(exist_ok=True)
    out = folder / f"homedocs-v{version}{'-' + why if why else ''}-{dt.datetime.now():%Y%m%d-%H%M%S}.db"
    with sqlite3.connect(out) as copy:
        db.backup(copy)
    copy.close()
    return out


_ready_dbs = set()


def open_db() -> sqlite3.Connection:
    path = db_file()
    db = sqlite3.connect(path, timeout=30, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")  # per connection
    if path not in _ready_dbs:  # WAL mode and the schema persist in the file: check them once per run
        db.execute("PRAGMA journal_mode = WAL")
        migrate(db, path)
        _ready_dbs.add(path)
    return db
