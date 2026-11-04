"""Indexing: files found, read, split and saved; unchanged ones skipped, changed ones read again, moved ones
keeping their passages, deleted ones dropped; unreachable folders kept as offline; stopping and crashing
partway losing nothing."""

import os
import sqlite3
import time

import pytest
from samples import make_docx, make_pdf, para

from homedocs.core import indexer, library, settings, store
from homedocs.core.indexer import Stopped, index_pass


@pytest.fixture
def docs(hd, tmp_path):
    """A folder being searched, with a file of each kind."""
    root = tmp_path / "docs"
    (root / "insurance").mkdir(parents=True)
    make_pdf(root / "insurance" / "Car insurance.pdf", ["The deductible is five hundred dollars per claim."])
    make_docx(root / "lease.docx", para("Rent is due on the first of the month."))
    (root / "notes.md").write_text("# Wifi\n\nThe password is on the router.\n", encoding="utf-8")
    (root / "todo.txt").write_text("Renew the passport before June.\n", encoding="utf-8")
    assert library.add_folder(root) is None
    return root


@pytest.fixture
def db(hd):
    d = hd.open_db()
    yield d
    d.close()


def found(db, words):
    return sorted({os.path.basename(r["path"]) for r in _search(db, words)})


def _search(db, words):
    return db.execute(
        "SELECT f.path FROM files f JOIN chunks c ON c.file_id = f.id WHERE c.id IN "
        f"({','.join(str(r['id']) for r in store.keyword_search(db, words)) or 'NULL'})"
    ).fetchall()


def statuses(db):
    return {os.path.basename(r["path"]): r["status"] for r in db.execute("SELECT path, status FROM files")}


def index_is_sound(db):
    with db:  # (an INSERT, for sqlite3: committed, so it doesn't hold the write lock)
        db.execute("INSERT INTO chunks_fts(chunks_fts, rank) VALUES ('integrity-check', 1)")  # raises if not
    return True


def touch(path, later=10):
    st = os.stat(path)
    os.utime(path, (st.st_atime, st.st_mtime + later))


def test_every_kind_of_file_is_read_and_searchable(docs, db):
    info = index_pass()
    assert info["read"] == 4 and info["failed"] == 0
    assert set(statuses(db).values()) == {"indexed"}
    assert found(db, "deductible") == ["Car insurance.pdf"]
    assert found(db, "rent") == ["lease.docx"]
    assert found(db, "router password") == ["notes.md"]
    assert found(db, "car") == ["Car insurance.pdf"]  # the file name is searched too
    assert found(db, "renewing") == ["todo.txt"]  # (words match their other forms: renew, renewing)
    assert index_is_sound(db)


def test_hidden_unsupported_and_lock_files_are_left_alone(docs, db):
    (docs / ".secret.txt").write_text("hidden file")
    (docs / ".git").mkdir()
    (docs / ".git" / "readme.md").write_text("hidden folder")
    (docs / "photo.jpg").write_bytes(b"\xff\xd8")
    (docs / "~$lease.docx").write_bytes(b"Word's lock file")
    index_pass()
    assert sorted(statuses(db)) == ["Car insurance.pdf", "lease.docx", "notes.md", "todo.txt"]


def test_unchanged_files_are_not_read_again(docs, db, monkeypatch):
    index_pass()
    read = []
    real = indexer.parsers.read
    monkeypatch.setattr(indexer.parsers, "read", lambda p: read.append(p) or real(p))
    assert index_pass()["read"] == 0 and read == []
    touch(docs / "todo.txt")  # touched, same contents: still not read
    assert index_pass()["read"] == 0 and read == []


def test_a_changed_file_is_read_again(docs, db):
    index_pass()
    (docs / "todo.txt").write_text("Book the dentist.\n")
    touch(docs / "todo.txt")
    assert index_pass()["read"] == 1
    assert found(db, "passport") == [] and found(db, "dentist") == ["todo.txt"]
    assert index_is_sound(db)


def test_a_moved_file_keeps_its_passages(docs, db, monkeypatch):
    index_pass()
    before = db.execute("SELECT id FROM chunks ORDER BY id").fetchall()
    (docs / "archive").mkdir()
    os.rename(docs / "lease.docx", docs / "archive" / "flat lease.docx")
    monkeypatch.setattr(indexer.parsers, "read", lambda p: pytest.fail("read again"))
    info = index_pass()
    assert info["moved"] == 1 and info["read"] == 0 and info["removed"] == 0
    assert db.execute("SELECT id FROM chunks ORDER BY id").fetchall() == before
    assert found(db, "flat") == ["flat lease.docx"]  # found by its new name
    assert index_is_sound(db)


def test_a_deleted_file_is_dropped_with_its_passages(docs, db):
    index_pass()
    os.remove(docs / "notes.md")
    assert index_pass()["removed"] == 1
    assert "notes.md" not in statuses(db) and found(db, "router") == []
    assert db.execute("SELECT COUNT(*) FROM chunks WHERE file_id NOT IN (SELECT id FROM files)").fetchone()[0] == 0
    assert index_is_sound(db)


def test_a_folder_out_of_reach_is_kept_as_offline(docs, db, tmp_path):
    index_pass()
    away = tmp_path / "unplugged"
    os.rename(docs, away)
    assert index_pass()["offline"] == 4
    assert db.execute("SELECT COUNT(*) FROM files WHERE offline = 1").fetchone()[0] == 4
    assert found(db, "deductible") == ["Car insurance.pdf"]  # still searchable
    docs.mkdir()  # an empty mount point, as an unplugged drive leaves: still offline, nothing deleted
    assert index_pass()["offline"] == 4 and len(statuses(db)) == 4
    docs.rmdir()
    os.rename(away, docs)
    index_pass()
    assert db.execute("SELECT COUNT(*) FROM files WHERE offline = 1").fetchone()[0] == 0


def test_a_removed_folder_drops_its_files(docs, db):
    index_pass()
    library.remove_folder(str(docs))
    assert index_pass()["removed"] == 4
    assert statuses(db) == {} and db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0
    assert index_is_sound(db)
    assert docs.joinpath("todo.txt").exists()  # nothing on disk touched


def test_a_file_that_cant_be_read_says_why_and_waits_until_it_changes(docs, db, monkeypatch):
    make_pdf(docs / "scan.pdf", [""])
    (docs / "broken.docx").write_bytes(b"PK broken")
    info = index_pass()
    assert info["failed"] == 2
    errors = dict(db.execute("SELECT name, error FROM files WHERE status = 'failed'").fetchall())
    assert "scanned image" in errors["scan.pdf"] and "damaged" in errors["broken.docx"]
    touch(docs / "scan.pdf")  # touched only: still failed, not tried again
    monkeypatch.setattr(indexer.parsers, "read", lambda p: pytest.fail("tried again"))
    index_pass()
    assert statuses(db)["scan.pdf"] == "failed"


def test_a_file_too_big_is_skipped(docs, db, monkeypatch):
    monkeypatch.setattr(indexer, "MAX_FILE_MB", 40 / (1024 * 1024))  # 40 bytes
    index_pass()
    assert statuses(db)["Car insurance.pdf"] == "skipped"
    assert "wasn't read" in db.execute("SELECT error FROM files WHERE status = 'skipped'").fetchone()[0]


def test_stopping_partway_carries_on_next_time(docs, db):
    seen = []
    with pytest.raises(Stopped):
        index_pass(should_stop=lambda: len(seen) > 2, report=lambda **i: seen.append(i))
    assert "pending" in statuses(db).values()
    assert index_pass()["read"] >= 1
    assert set(statuses(db).values()) == {"indexed"}


def test_a_crash_while_saving_leaves_the_file_as_it_was(docs, db, monkeypatch):
    index_pass()
    (docs / "todo.txt").write_text("Book the dentist.\n")
    touch(docs / "todo.txt")
    real = store.save_passages

    def crash(db, file_id, parts):
        real(db, file_id, parts)
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(store, "save_passages", crash)
    with pytest.raises(sqlite3.OperationalError):
        index_pass()
    assert found(db, "passport") == ["todo.txt"] and found(db, "dentist") == []  # the old passages, whole
    assert statuses(db)["todo.txt"] == "pending" and index_is_sound(db)
    monkeypatch.setattr(store, "save_passages", real)
    index_pass()
    assert found(db, "dentist") == ["todo.txt"]


def test_newest_files_are_read_first(docs, db):
    for i, name in enumerate(["todo.txt", "notes.md", "lease.docx"]):
        os.utime(docs / name, (time.time(), time.time() + 100 * (i + 1)))
    order = []
    index_pass(report=lambda **i: i.get("file") and order.append(os.path.basename(i["file"])))
    assert order[:3] == ["lease.docx", "notes.md", "todo.txt"]


def test_folders_inside_each_other(hd, tmp_path):
    outer, inner = tmp_path / "a", tmp_path / "a" / "b"
    inner.mkdir(parents=True)
    assert library.add_folder(inner) is None
    assert library.add_folder(inner) is None  # again: nothing changes
    assert library.add_folder(outer) is None  # takes the inner one's place
    assert settings.folders() == [str(outer)]
    assert "already searched" in library.add_folder(inner)
    assert "can't be found" in library.add_folder(tmp_path / "nope")


def test_only_one_copy_indexes_at_a_time(docs):
    held = indexer._IndexLock()
    assert held.acquire()
    try:
        ix = indexer.Indexer()
        ix.start()
        assert indexer.wait_for(ix)["phase"] == "elsewhere"
    finally:
        held.release()
    ix.start()
    s = indexer.wait_for(ix)
    assert s["phase"] == "idle" and s["changes"] == 1 and s["error"] is None
