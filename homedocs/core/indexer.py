"""Keeping the index in step with the folders: finding new, changed, moved and removed files, reading them
into passages, and saving those. One pass at a time, on a background thread.

A pass, for each folder in the settings:
1. Scan: walk the folder (skipping hidden files and folders, links to folders, what's left out, and the
   kinds of file that aren't read) and compare what's there with the files table. A folder that can't be
   reached, or that has emptied completely (an unplugged drive's mount point looks just like that), marks its files
   offline and keeps them.
2. New files are added as pending; changed ones (other size or modified time) become pending.
3. Read every pending file, newest first: each is read, split and saved in one transaction, so stopping
   anywhere leaves every file either fully old or fully new. A file whose contents match one that
   vanished in this pass was moved or renamed: it takes over that file's passages instead of being read
   again. One whose contents didn't change (only touched) is just marked as seen.
4. Files that vanished and weren't found elsewhere are removed with their passages.

Files from folders no longer in the settings, or left out, are removed first. Nothing on disk is ever changed.

Then, once the embedding model is downloaded, embed_pass gives every passage without a vector one, a few at a
time (newest files first), so search by meaning covers it. Vectors made by another model are dropped first.

Only one HomeDocs indexes at a time: a pass first takes a lock file in the data folder, and if another
copy of the app holds it, this one leaves the indexing to it.
"""

import hashlib
import os
import stat
import sys
import threading
import time

from . import parsers, passages, store
from .config import EMBED_BATCH, EMBED_FETCH, FILE_TYPES, MAX_FILE_MB, data_dir
from .db import open_db
from .settings import folders, left_out
from .util import folder_prefix, inside, lower_priority, within


class Stopped(Exception):
    """The pass was asked to stop: it carries on from here next time."""


def _hidden(path: str, name: str) -> bool:
    if name.startswith((".", "~$")):  # (~$: the lock files Word leaves beside open documents)
        return True
    if os.name == "nt":
        try:
            return bool(os.stat(path, follow_symlinks=False).st_file_attributes & stat.FILE_ATTRIBUTE_HIDDEN)
        except OSError:
            return True
    return False


def scan(root: str, should_stop=lambda: False, left=()) -> dict | None:
    """Every readable kind of file under root, apart from what's left out (left: paths):
    path -> (size, modified time). None if root can't be reached. Raises Stopped if should_stop() says so
    (checked in every folder)."""
    left = set(left)
    try:
        if not os.path.isdir(root):
            return None
        os.listdir(root)
    except OSError:
        return None
    skip = os.path.normcase(str(data_dir().resolve()))
    found = {}
    for dirpath, dirnames, filenames in os.walk(root):  # (doesn't follow links to folders)
        if should_stop():
            raise Stopped
        dirnames[:] = [
            d
            for d in dirnames
            if not _hidden(os.path.join(dirpath, d), d)
            and os.path.normcase(os.path.join(dirpath, d)) != skip
            and not os.path.islink(os.path.join(dirpath, d))
            and os.path.join(dirpath, d) not in left
        ]
        for name in filenames:
            if os.path.splitext(name)[1].lower() not in FILE_TYPES:
                continue
            p = os.path.join(dirpath, name)
            if p in left or _hidden(p, name):
                continue
            try:
                st = os.stat(p)
            except OSError:
                continue  # gone already, or a broken link
            if stat.S_ISREG(st.st_mode):
                found[p] = (st.st_size, st.st_mtime)
    return found


def file_hash(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def index_pass(should_stop=lambda: False, report=lambda **info: None) -> dict:
    """One pass over every folder (see the top of this file). report(phase=, done=, todo=, file=) tells
    how it's going. Returns counts: read, failed, moved, removed, offline. Raises Stopped."""
    db = open_db()
    try:
        return _index_pass(db, folders(), should_stop, report, left_out())
    finally:
        db.close()


def _index_pass(db, roots, should_stop, report, left=()) -> dict:
    info = {"read": 0, "failed": 0, "moved": 0, "removed": 0, "offline": 0}
    report(phase="scanning", done=0, todo=0, file=None)
    prefixes = [folder_prefix(r) for r in roots]
    known = {r["path"]: r for r in db.execute("SELECT id, path, size, mtime, status, hash FROM files")}
    with db:  # files from folders that were removed, or left out
        for path, row in list(known.items()):
            if not inside(path, prefixes) or within(path, left):
                store.drop_file(db, row["id"])
                del known[path]
                info["removed"] += 1

    vanished = {}  # id -> row: gone from a folder that could be looked through (maybe moved)
    for root, prefix in zip(roots, prefixes, strict=True):
        found = scan(root, should_stop, left)
        mine = {p: r for p, r in known.items() if p.startswith(prefix)}
        if found is None or (not found and mine):
            with db:
                db.executemany("UPDATE files SET offline = 1 WHERE id = ?", [(r["id"],) for r in mine.values()])
            info["offline"] += len(mine)
            continue
        with db:
            db.executemany(
                "UPDATE files SET offline = 0 WHERE id = ? AND offline = 1", [(r["id"],) for r in mine.values()]
            )
            for path, (size, mtime) in found.items():
                row = mine.get(path)
                too_big = size > MAX_FILE_MB * 1024 * 1024
                if row is not None and row["size"] == size and row["mtime"] == mtime:
                    continue
                if row is None:
                    db.execute(
                        "INSERT INTO files (path, name, size, mtime, status, error) VALUES (?, ?, ?, ?, ?, ?)",
                        (path, os.path.basename(path), size, mtime, *_status(too_big)),
                    )
                elif too_big:
                    db.execute(
                        "UPDATE files SET size = ?, mtime = ?, status = ?, error = ? WHERE id = ?",
                        (size, mtime, *_status(True), row["id"]),
                    )
                else:  # changed: read again (its passages, and why it failed if it did, stay until then)
                    db.execute(
                        "UPDATE files SET size = ?, mtime = ?, status = 'pending' WHERE id = ?",
                        (size, mtime, row["id"]),
                    )
        vanished.update({r["id"]: r for p, r in mine.items() if p not in found})

    todo = db.execute(
        "SELECT id, path, size, mtime, hash FROM files WHERE status = 'pending' AND offline = 0 ORDER BY mtime DESC"
    ).fetchall()
    by_hash = {r["hash"]: r for r in vanished.values() if r["hash"]}
    for done, row in enumerate(todo):
        if should_stop():
            raise Stopped
        report(phase="reading", done=done, todo=len(todo), file=row["path"])
        if not inside(row["path"], [folder_prefix(r) for r in folders()]) or within(row["path"], left_out()):
            continue  # removed or left out meanwhile: the next pass drops it
        _read_one(db, row, by_hash, vanished, info)
    report(phase="reading", done=len(todo), todo=len(todo), file=None)

    with db:
        for row in vanished.values():
            store.drop_file(db, row["id"])
            info["removed"] += 1
    return info


def embed_pass(embedder, should_stop=lambda: False, report=lambda **info: None) -> int:
    """Give every passage without a vector one: a copy of a passage already embedded (the same file name,
    headings and text) reuses its vector. Returns how many passages got one; raises Stopped."""
    db = open_db()
    try:
        store.drop_other_vectors(db, embedder.key)
        # counted over the whole library, so a pass that starts over carries on from where the last one got to
        todo = store.count_passages(db)
        done = start = todo - store.count_without_vectors(db)
        while True:
            if should_stop():
                raise Stopped
            fetched = store.without_vectors(db, EMBED_FETCH)
            if not fetched:
                return done - start
            for at in range(0, len(fetched), EMBED_BATCH * 2):  # (saved as it goes; stopping waits for one)
                if should_stop():
                    raise Stopped
                rows = fetched[at : at + EMBED_BATCH * 2]
                report(phase="embedding", done=done, todo=todo, file=None)
                texts = [store.embed_text(r["name"], r["heading"], r["text"]) for r in rows]
                digests = [store.digest(t) for t in texts]
                vecs = store.known_vectors(db, embedder.key, digests)
                new = {d: t for d, t in zip(digests, texts, strict=True) if d not in vecs}  # (once each)
                if new:
                    made = embedder.embed(list(new.values()), EMBED_BATCH)
                    vecs.update((d, v.tobytes()) for d, v in zip(new, made, strict=True))
                ids = [r["id"] for r in rows]
                store.save_vectors(db, embedder.key, ids, [vecs[d] for d in digests], digests)
                done += len(rows)
                todo = max(todo, done)
    finally:
        db.close()


def _status(too_big: bool) -> tuple[str, str | None]:
    """A file's status and error when it's found new or changed."""
    if too_big:
        return "skipped", f"Larger than {MAX_FILE_MB} MB, so it wasn't read."
    return "pending", None


# What a file read before is once found unchanged (or moved): as it was, failed or not
_AS_BEFORE = "status = CASE WHEN error IS NULL THEN 'indexed' ELSE 'failed' END"


def _read_one(db, row, by_hash, vanished, info):
    path = row["path"]
    try:
        digest = file_hash(path)
    except OSError:
        return  # gone or locked since the scan: the next pass sees what became of it
    with db:
        if digest == row["hash"]:  # only touched: the passages are still right
            db.execute(f"UPDATE files SET {_AS_BEFORE} WHERE id = ?", (row["id"],))
            return
        old = by_hash.pop(digest, None)
        if old is not None and old["id"] in vanished:  # moved or renamed: it keeps its passages
            del vanished[old["id"]]
            db.execute("DELETE FROM files WHERE id = ?", (row["id"],))
            store.rename_file(db, old["id"], path, os.path.basename(path))
            db.execute(
                f"UPDATE files SET size = ?, mtime = ?, {_AS_BEFORE} WHERE id = ?",
                (row["size"], row["mtime"], old["id"]),
            )
            info["moved"] += 1
            return
    try:
        doc = parsers.read(path)
        parts = passages.split(doc)
    except parsers.ReadError as e:
        _failed(db, row["id"], digest, str(e))
        info["failed"] += 1
        return
    except Exception as e:  # a file no reader expected: say so, and carry on with the rest
        print(f"couldn't read {path}: {e!r}", file=sys.stderr)
        _failed(db, row["id"], digest, f"Couldn't read this file ({type(e).__name__}).")
        info["failed"] += 1
        return
    with db:
        store.save_passages(db, row["id"], parts)
        db.execute(
            "UPDATE files SET status = 'indexed', error = NULL, hash = ?, indexed_at = ? WHERE id = ?",
            (digest, time.time(), row["id"]),
        )
    info["read"] += 1


def _failed(db, file_id, digest, why):
    with db:  # its old passages (if it had any) no longer match the file
        store.drop_passages(db, file_id)
        db.execute(
            "UPDATE files SET status = 'failed', error = ?, hash = ?, indexed_at = ? WHERE id = ?",
            (why, digest, time.time(), file_id),
        )


# --- running passes in the background ---------------------------------------------------------------


class _IndexLock:
    """An exclusive lock on a file, held across processes; released if the process dies."""

    def __init__(self):
        self.f = None

    def acquire(self) -> bool:
        f = open(data_dir() / "indexing.lock", "a+b")  # noqa: SIM115 (held open while indexing: it's the lock)
        try:
            if os.name == "nt":
                import msvcrt

                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            return False
        self.f = f
        return True

    def release(self):
        if self.f is not None:
            if os.name == "nt":  # Windows wants it unlocked before closing; elsewhere closing is enough
                import msvcrt

                try:
                    self.f.seek(0)
                    msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            self.f.close()
            self.f = None


class Indexer:
    """Runs passes on a background thread.

    snapshot() returns the state as a dict: running, phase ("idle" | "scanning" | "reading" | "embedding" |
    "elsewhere"), done, todo, file (being read), error (a message to show, or None), changes (goes up by
    one after each pass that changed what's indexed or embedded: compare it to know when to reload), last_run.

    embedder: set it (from any thread) once the embedding model is loaded; passes then embed too.
    """

    def __init__(self, embedder=None):
        self.embedder = embedder
        self.lock = threading.Lock()
        self.thread = None
        self.stop_flag = False
        self.busy = False  # a pass is running or about to (set and cleared under the lock, unlike thread.is_alive())
        self.again = False  # start the running pass over
        self.later = False  # run one more pass once the running one is done
        self.state = {
            "running": False,
            "phase": "idle",
            "done": 0,
            "todo": 0,
            "file": None,
            "error": None,
            "changes": 0,
            "last_run": None,
        }

    def start(self, restart: bool = True):
        """Start a pass. If one is running: restart, it starts over (e.g. a folder was added or removed);
        otherwise it carries on and another follows it (e.g. back from another app, where files may have
        changed: that shouldn't throw away the passages being embedded)."""
        with self.lock:
            if self.busy:
                if restart:
                    self.again = True
                else:
                    self.later = True
                return
            self.busy = True
            self.stop_flag = self.again = self.later = False
            self.thread = threading.Thread(target=self._run, daemon=True, name="indexer")
            self.thread.start()

    def stop(self):
        self.stop_flag = True

    def stop_and_wait(self, timeout: float = 30):
        self.stop()
        t = self.thread
        if t is not None:
            t.join(timeout=timeout)

    def _report(self, **info):
        with self.lock:
            self.state.update(info, running=True)

    def _run(self):
        lower_priority()
        lock = _IndexLock()
        if not lock.acquire():
            with self.lock:
                self.busy = False
                self.state.update(running=False, phase="elsewhere", error=None)
            return
        try:
            while True:
                with self.lock:
                    self.again = self.later = False
                    self.state.update(running=True, error=None, phase="scanning", done=0, todo=0, file=None)
                stop = lambda: self.stop_flag or self.again  # noqa: E731
                try:
                    info = index_pass(stop, self._report)
                    changed = any(info[k] for k in ("read", "failed", "moved", "removed", "offline"))
                    if self.embedder is not None:
                        if changed:  # (keyword search can show the new files while they're embedded)
                            with self.lock:
                                self.state["changes"] += 1
                            changed = False
                        changed = bool(embed_pass(self.embedder, stop, self._report))
                except Stopped:
                    changed = True  # (some files may have been read)
                except Exception as e:
                    print(f"index error: {e!r}", file=sys.stderr)
                    with self.lock:
                        self.state["error"] = f"Something went wrong while reading your files: {e}"
                    changed = True
                with self.lock:
                    self.state["changes"] += changed
                    if self.stop_flag or not (self.again or self.later):
                        self.busy = False
                        self.state.update(running=False, phase="idle", file=None, last_run=time.time())
                        return
        finally:
            lock.release()

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.state)


def wait_for(indexer: Indexer, timeout: float = 60) -> dict:
    """Wait for the indexer to finish (for tests and the command line)."""
    end = time.time() + timeout
    while time.time() < end:
        t = indexer.thread
        if t is None or not t.is_alive():
            break
        t.join(0.05)
    return indexer.snapshot()
