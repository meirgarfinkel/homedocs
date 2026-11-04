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

VERSION = 1

# The whole schema, with a comment on every column. Empty until indexing arrives.
TABLES: dict[str, str] = {}

INDEXES: list[str] = []


class DatabaseVersionError(RuntimeError):
    """The database is from a newer HomeDocs, or from one too old to bring up to date."""


def columns(db, table) -> list[str]:
    return [r[1] for r in db.execute(f"PRAGMA table_info({table})")]


UPGRADES = {}  # version: the step that brings it up to the next one


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
