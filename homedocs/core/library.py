"""The folders being searched, what's left out of them, and what's known about the files in them (for the
Library page)."""

import os
import sqlite3
from dataclasses import dataclass

from .config import FILE_TYPES
from .settings import folders, load_settings, save_settings
from .util import folder_prefix, inside, within


def _norm(folder) -> str:
    return os.path.normpath(os.path.abspath(os.path.expanduser(str(folder))))


def _same(a: str, b: str) -> bool:
    return os.path.normcase(a) == os.path.normcase(b)


def add_folder(folder) -> str | None:
    """Search folder too. Returns why not, or None. A folder inside one already searched adds nothing;
    one holding folders already searched takes their place (their files stay indexed)."""
    folder = _norm(folder)
    if not os.path.isdir(folder):
        return "That folder can't be found."
    s = load_settings()
    current = s.get("folders", [])
    for f in current:
        if _same(f, folder) or inside(os.path.normcase(folder), [os.path.normcase(folder_prefix(f))]):
            return f"{folder} is already searched, as part of {f}." if not _same(f, folder) else None
    keep = [f for f in current if not inside(os.path.normcase(f), [os.path.normcase(folder_prefix(folder))])]
    s["folders"] = [*keep, folder]
    save_settings(s)
    return None


def remove_folder(folder):
    """Stop searching folder. Its files are dropped from the index by the next pass (nothing on disk), and
    what was left out inside it is forgotten."""
    s = load_settings()
    s["folders"] = [f for f in s.get("folders", []) if not _same(f, folder)]
    s["left_out"] = [p for p in s.get("left_out", []) if not within(p, [folder])]
    save_settings(s)


def leave_out(path) -> str | None:
    """Stop reading and searching path (a subfolder or a file inside a searched folder). Returns why not,
    or None. Something inside a place already left out needs nothing more; leaving out a folder that holds
    places already left out takes their place."""
    path = _norm(path)
    roots = folders()
    if not any(inside(path, [folder_prefix(f)]) for f in roots):
        if any(_same(path, f) for f in roots):
            return "That's a whole folder being searched: remove it instead."
        return "That isn't inside a folder being searched."
    s = load_settings()
    current = s.get("left_out", [])
    if within(path, current):
        return None
    s["left_out"] = [p for p in current if not within(p, [path])] + [path]
    save_settings(s)
    return None


def take_back(path):
    """Read and search path again."""
    s = load_settings()
    s["left_out"] = [p for p in s.get("left_out", []) if not _same(p, path)]
    save_settings(s)


def read_again(db: sqlite3.Connection, path: str):
    """Read this file again on the next pass, even if it hasn't changed."""
    with db:
        db.execute("UPDATE files SET status = 'pending', hash = NULL WHERE path = ?", (path,))


@dataclass
class FileRow:
    path: str
    name: str
    folder: str  # the searched folder it's in
    where: str  # its own folder, relative to that ("" for the folder itself)
    kind: str  # "PDF", "Word"...
    status: str  # "indexed", "pending", "failed", "skipped", "left_out"
    error: str | None
    offline: bool
    passages: int
    size: int
    mtime: float


def files(db: sqlite3.Connection, roots: list[str], left: list[str] = ()) -> list[FileRow]:
    """Every file known, with its passage count, then each place left out (a folder's kind is "Folder")."""
    counts = dict(db.execute("SELECT file_id, COUNT(*) FROM chunks GROUP BY file_id").fetchall())
    prefixes = [(r, folder_prefix(r)) for r in sorted(roots, key=len, reverse=True)]
    out = []
    for r in db.execute("SELECT id, path, name, status, error, offline, size, mtime FROM files"):
        root = next((f for f, p in prefixes if r["path"].startswith(p)), "")
        where = os.path.dirname(r["path"])[len(root) + 1 :] if root else os.path.dirname(r["path"])
        out.append(
            FileRow(
                r["path"],
                r["name"],
                root,
                where,
                FILE_TYPES.get(os.path.splitext(r["name"])[1].lower(), ""),
                r["status"],
                r["error"],
                bool(r["offline"]),
                counts.get(r["id"], 0),
                r["size"],
                r["mtime"],
            )
        )
    for p in left:
        root = next((f for f, pre in prefixes if p.startswith(pre)), "")
        name = os.path.basename(p)
        is_dir = os.path.isdir(p)
        kind = "Folder" if is_dir else FILE_TYPES.get(os.path.splitext(name)[1].lower(), "")
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            mtime = 0.0
        where = os.path.dirname(p)[len(root) + 1 :] if root else os.path.dirname(p)
        out.append(FileRow(p, name, root, where, kind, "left_out", None, False, 0, 0, mtime))
    return out


def folder_counts(db: sqlite3.Connection) -> dict[str, int]:
    """{folder: how many files are directly in it}, for every folder holding a file HomeDocs knows."""
    counts: dict[str, int] = {}
    for (path,) in db.execute("SELECT path FROM files"):
        d = os.path.dirname(path)
        counts[d] = counts.get(d, 0) + 1
    return counts


def folder_tree(roots: list[str], counts: dict[str, int]) -> dict:
    """{root: node}, node = {"path", "name", "total" (files in it and below), "children": {name: node}}.
    Folders without files (anywhere below) are left out, except the roots themselves. (As in HomePix.)"""
    tree = {}
    for root in roots:
        node = {"path": root, "name": os.path.basename(root.rstrip("/\\")) or root, "total": 0, "children": {}}
        prefix = folder_prefix(root)
        for folder, n in counts.items():
            if folder == root:
                node["total"] += n
                continue
            if not folder.startswith(prefix):
                continue
            node["total"] += n
            cur = node
            for part in folder[len(prefix) :].split(os.sep):
                child = cur["children"].get(part)
                if child is None:
                    child = cur["children"][part] = {
                        "path": os.path.join(cur["path"], part),
                        "name": part,
                        "total": 0,
                        "children": {},
                    }
                child["total"] += n
                cur = child
        tree[root] = node
    return tree
