"""The folders being searched, and what's known about the files in them (for the Library page)."""

import os
import sqlite3
from dataclasses import dataclass

from .config import FILE_TYPES
from .settings import load_settings, save_settings
from .util import folder_prefix, inside


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
    """Stop searching folder. Its files are dropped from the index by the next pass (nothing on disk)."""
    s = load_settings()
    s["folders"] = [f for f in s.get("folders", []) if not _same(f, folder)]
    save_settings(s)


@dataclass
class FileRow:
    path: str
    name: str
    folder: str  # the searched folder it's in
    where: str  # its own folder, relative to that ("" for the folder itself)
    kind: str  # "PDF", "Word"...
    status: str  # "indexed", "pending", "failed", "skipped"
    error: str | None
    offline: bool
    passages: int
    size: int
    mtime: float


def files(db: sqlite3.Connection, roots: list[str]) -> list[FileRow]:
    """Every file known, with its passage count."""
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
    return out
