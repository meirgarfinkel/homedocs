"""Database upgrades: a new database gets the schema, an older one is backed up and brought up to date
in one transaction, and one from a newer HomeDocs is refused without being touched. Most try the
machinery on a made-up table; the last ones, the real schema."""

import sqlite3

import pytest

from homedocs.core import db as dbmod


@pytest.fixture
def two_versions(monkeypatch):
    """A made-up schema at version 2, with the step from version 1."""

    def to_2(db):
        db.execute("ALTER TABLE notes ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")

    monkeypatch.setattr(dbmod, "VERSION", 2)
    monkeypatch.setattr(
        dbmod, "TABLES", {"notes": "CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT, pinned INTEGER)"}
    )
    monkeypatch.setattr(dbmod, "INDEXES", [])
    monkeypatch.setattr(dbmod, "UPGRADES", {1: to_2})
    monkeypatch.setattr(dbmod, "_ready_dbs", set())


def _version_1(path):
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT)")
        db.execute("INSERT INTO notes (text) VALUES ('keep me')")
        db.execute("PRAGMA user_version = 1")
    db.close()


def test_new_database_gets_the_schema_and_no_backup(hd, two_versions):
    db = hd.open_db()
    assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    assert dbmod.columns(db, "notes") == ["id", "text", "pinned"]
    db.close()
    assert not (hd.data_dir() / "backups").exists()


def test_older_database_is_backed_up_then_upgraded(hd, two_versions):
    _version_1(dbmod.db_file())
    db = hd.open_db()
    assert [tuple(r) for r in db.execute("SELECT text, pinned FROM notes")] == [("keep me", 0)]
    assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    db.close()
    (backup,) = (hd.data_dir() / "backups").iterdir()
    assert backup.name.startswith("homedocs-v1-")


def test_failed_upgrade_leaves_database_untouched(hd, two_versions, monkeypatch):
    _version_1(dbmod.db_file())

    def broken(db):
        db.execute("ALTER TABLE notes ADD COLUMN half INTEGER")
        raise RuntimeError("halfway")

    monkeypatch.setattr(dbmod, "UPGRADES", {1: broken})
    with pytest.raises(RuntimeError, match="halfway"):
        hd.open_db()
    with sqlite3.connect(dbmod.db_file()) as raw:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == 1
        assert dbmod.columns(raw, "notes") == ["id", "text"]
    raw.close()


def test_newer_database_is_refused_and_not_touched(hd, two_versions):
    with sqlite3.connect(dbmod.db_file()) as raw:
        raw.execute("CREATE TABLE future (x)")
        raw.execute("PRAGMA user_version = 9")
    raw.close()
    with pytest.raises(hd.DatabaseVersionError, match="newer HomeDocs"):
        hd.open_db()
    with sqlite3.connect(dbmod.db_file()) as raw:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == 9
    raw.close()


def test_the_real_schema_opens(hd):
    db = hd.open_db()
    assert db.execute("PRAGMA user_version").fetchone()[0] == dbmod.VERSION
    db.close()


def test_a_database_from_0_1_0_gets_the_indexing_tables(hd, monkeypatch):
    monkeypatch.setattr(dbmod, "_ready_dbs", set())
    with sqlite3.connect(dbmod.db_file()) as raw:  # what 0.1.0 made: no tables, version 1
        raw.execute("PRAGMA user_version = 1")
    raw.close()
    db = hd.open_db()
    assert db.execute("PRAGMA user_version").fetchone()[0] == dbmod.VERSION
    assert "char_start" in dbmod.columns(db, "chunks") and "offline" in dbmod.columns(db, "files")
    db.execute("SELECT * FROM chunks_fts WHERE chunks_fts MATCH 'x'").fetchall()
    db.close()
    assert not (hd.data_dir() / "backups").exists()  # (nothing in it to back up)


def test_a_database_from_0_2_0_keeps_its_passages_and_gains_vectors(hd, monkeypatch):
    monkeypatch.setattr(dbmod, "_ready_dbs", set())
    with sqlite3.connect(dbmod.db_file()) as raw:  # what 0.2.0 made: version 2, with a file read
        for create in dbmod.V2_TABLES.values():
            raw.execute(create)
        raw.execute("INSERT INTO files (path, name, size, mtime, status) VALUES ('/a.txt', 'a.txt', 1, 1, 'indexed')")
        raw.execute("INSERT INTO chunks (file_id, seq, char_start, char_end, text) VALUES (1, 0, 0, 5, 'hello')")
        raw.execute("PRAGMA user_version = 2")
    raw.close()
    db = hd.open_db()
    assert db.execute("PRAGMA user_version").fetchone()[0] == dbmod.VERSION
    assert db.execute("SELECT text FROM chunks").fetchone()[0] == "hello"
    assert dbmod.columns(db, "vectors") == ["chunk_id", "model", "vec"]
    db.close()
    (backup,) = (hd.data_dir() / "backups").iterdir()
    assert backup.name.startswith("homedocs-v2-")


def test_a_database_from_0_3_0_keeps_its_vectors_and_gains_questions(hd, monkeypatch):
    monkeypatch.setattr(dbmod, "_ready_dbs", set())
    with sqlite3.connect(dbmod.db_file()) as raw:  # what 0.3.0 made: version 3, with a passage embedded
        for create in [*dbmod.V2_TABLES.values(), dbmod.TABLES["vectors"]]:
            raw.execute(create)
        raw.execute("INSERT INTO files (path, name, size, mtime, status) VALUES ('/a.txt', 'a.txt', 1, 1, 'indexed')")
        raw.execute("INSERT INTO chunks (file_id, seq, char_start, char_end, text) VALUES (1, 0, 0, 5, 'hello')")
        raw.execute("INSERT INTO vectors (chunk_id, model, vec) VALUES (1, 'm', x'00000000')")
        raw.execute("PRAGMA user_version = 3")
    raw.close()
    db = hd.open_db()
    assert db.execute("PRAGMA user_version").fetchone()[0] == dbmod.VERSION
    assert db.execute("SELECT model FROM vectors").fetchone()[0] == "m"
    assert dbmod.columns(db, "questions") == ["id", "asked", "question", "answer", "passages", "sources"]
    db.close()
    (backup,) = (hd.data_dir() / "backups").iterdir()
    assert backup.name.startswith("homedocs-v3-")


def test_a_database_from_0_4_reads_its_files_again_for_the_shorter_passages(hd, monkeypatch):
    monkeypatch.setattr(dbmod, "_ready_dbs", set())
    with sqlite3.connect(dbmod.db_file()) as raw:  # what 0.4 made: version 4, a file read and one skipped
        for create in [*dbmod.V2_TABLES.values(), dbmod.TABLES["vectors"], dbmod.TABLES["questions"]]:
            raw.execute(create)
        raw.execute(
            "INSERT INTO files (path, name, size, mtime, hash, status) VALUES ('/a.txt', 'a.txt', 1, 1, 'h', 'indexed')"
        )
        raw.execute("INSERT INTO files (path, name, size, mtime, status) VALUES ('/b.pdf', 'b.pdf', 1, 1, 'skipped')")
        raw.execute("INSERT INTO chunks (file_id, seq, char_start, char_end, text) VALUES (1, 0, 0, 5, 'hello')")
        raw.execute("PRAGMA user_version = 4")
    raw.close()
    db = hd.open_db()
    rows = db.execute("SELECT status, hash FROM files ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [("pending", None), ("skipped", None)]
    assert db.execute("SELECT text FROM chunks").fetchone()[0] == "hello"  # (searched until it's read again)
    db.close()
