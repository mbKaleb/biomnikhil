"""The User Files library: what the app knows about each file in the uploads folder.

The files themselves stay in one flat folder (Config.UPLOAD_PATH), because a
run is attached to them by name. This module keeps the rest in its own SQLite
file next to the chat history: where a file came from (uploaded, or saved from
a chat's output), when it arrived, and the tags the user gave it. Tags belong
to the app rather than to a chat, so every chat sees the same ones.

A file in the folder with no row here (put there by hand, or uploaded before
this existed) still lists, as an upload dated by its modified time.
"""
from __future__ import annotations

import shutil
import sqlite3
import threading
import time
from pathlib import Path

from werkzeug.utils import secure_filename

from ..config import Config
from . import chat_store

SOURCE_UPLOAD = "upload"
SOURCE_CHAT = "chat"
MAX_TAG_CHARS = 30
MAX_TAGS_PER_FILE = 12
FALLBACK_NAME = "output-file"

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


class LibraryError(ValueError):
    """A request the library can't act on; the message says why."""


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        Config.FILES_DB.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(Config.FILES_DB, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA foreign_keys = ON")
        _conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS files (
                name TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                chat_id TEXT,
                task_id TEXT,
                origin_path TEXT,
                added REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS file_tags (
                name TEXT NOT NULL REFERENCES files(name) ON DELETE CASCADE,
                tag TEXT NOT NULL,
                PRIMARY KEY (name, tag)
            );
            """
        )
        _conn.commit()
    return _conn


# ---- tags -------------------------------------------------------------------

def clean_tags(raw) -> list[str]:
    """The tags in `raw` as stored: trimmed, lower case, no repeats."""
    if not isinstance(raw, list):
        raise LibraryError("tags must be a list")
    cleaned = []
    for item in raw:
        if not isinstance(item, str):
            raise LibraryError("each tag must be text")
        tag = " ".join(item.replace(",", " ").split()).lower()
        if not tag:
            continue
        if len(tag) > MAX_TAG_CHARS:
            raise LibraryError(f"a tag can be at most {MAX_TAG_CHARS} characters")
        if tag not in cleaned:
            cleaned.append(tag)
    if len(cleaned) > MAX_TAGS_PER_FILE:
        raise LibraryError(f"a file can have at most {MAX_TAGS_PER_FILE} tags")
    return cleaned


def _tags_of(name: str) -> list[str]:
    rows = _db().execute(
        "SELECT tag FROM file_tags WHERE name = ? ORDER BY tag", (name,)
    ).fetchall()
    tags = []
    for row in rows:
        tags.append(row["tag"])
    return tags


def _ensure_row(name: str) -> None:
    """A row for a file that only exists on disk, so it can carry tags."""
    existing = _db().execute("SELECT 1 FROM files WHERE name = ?", (name,)).fetchone()
    if existing:
        return
    modified = (Config.UPLOAD_PATH / name).stat().st_mtime
    _db().execute(
        "INSERT INTO files (name, source, added) VALUES (?, ?, ?)",
        (name, SOURCE_UPLOAD, modified),
    )


def set_tags(name: str, raw_tags) -> list[str] | None:
    """Replaces the file's tags and returns them, or None for an unknown file."""
    tags = clean_tags(raw_tags)
    if not (Config.UPLOAD_PATH / name).is_file():
        return None
    with _lock:
        _ensure_row(name)
        _db().execute("DELETE FROM file_tags WHERE name = ?", (name,))
        for tag in tags:
            _db().execute("INSERT INTO file_tags (name, tag) VALUES (?, ?)", (name, tag))
        _db().commit()
    return tags


# ---- recording files --------------------------------------------------------

def record_upload(name: str) -> None:
    """Notes a file uploaded by hand. Uploading over a name that's already
    there replaces the file, so its source and date are reset but its tags stay."""
    now = time.time()
    with _lock:
        _db().execute(
            "INSERT INTO files (name, source, added) VALUES (?, ?, ?)"
            " ON CONFLICT(name) DO UPDATE SET source = excluded.source,"
            " chat_id = NULL, task_id = NULL, origin_path = NULL, added = excluded.added",
            (name, SOURCE_UPLOAD, now),
        )
        _db().commit()


def forget(name: str) -> None:
    with _lock:
        _db().execute("DELETE FROM files WHERE name = ?", (name,))
        _db().commit()


def _free_name(wanted: str) -> str:
    """`wanted`, or the first of wanted-2, wanted-3... that isn't taken."""
    if not (Config.UPLOAD_PATH / wanted).exists():
        return wanted
    stem = Path(wanted).stem
    suffix = Path(wanted).suffix
    number = 2
    while True:
        candidate = f"{stem}-{number}{suffix}"
        if not (Config.UPLOAD_PATH / candidate).exists():
            return candidate
        number += 1


def save_output(source: Path, chat_id: str | None, task_id: str, origin_path: str) -> tuple[str, bool]:
    """Copies a file a run wrote into the library and returns its name there
    with whether it is new. Saving the same output again returns the copy
    already there, so a double click doesn't make two."""
    with _lock:
        saved = _db().execute(
            "SELECT name FROM files WHERE task_id = ? AND origin_path = ?",
            (task_id, origin_path),
        ).fetchone()
        if saved and (Config.UPLOAD_PATH / saved["name"]).is_file():
            return saved["name"], False

        Config.UPLOAD_PATH.mkdir(parents=True, exist_ok=True)
        wanted = secure_filename(source.name) or FALLBACK_NAME
        name = _free_name(wanted)
        shutil.copy2(source, Config.UPLOAD_PATH / name)
        _db().execute(
            "INSERT OR REPLACE INTO files (name, source, chat_id, task_id, origin_path, added)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (name, SOURCE_CHAT, chat_id or None, task_id, origin_path, time.time()),
        )
        _db().commit()
    return name, True


# ---- listing ----------------------------------------------------------------

def list_files() -> list[dict]:
    """Every file in the uploads folder with what the library knows about it.
    A row whose file has gone from the folder is dropped."""
    Config.UPLOAD_PATH.mkdir(parents=True, exist_ok=True)
    on_disk = {}
    for path in Config.UPLOAD_PATH.iterdir():
        if path.name.startswith(".") or not path.is_file():
            continue
        try:
            on_disk[path.name] = path.stat()
        except OSError:
            continue

    with _lock:
        rows = {}
        for row in _db().execute("SELECT * FROM files").fetchall():
            rows[row["name"]] = dict(row)
        for name in list(rows):
            if name not in on_disk:
                _db().execute("DELETE FROM files WHERE name = ?", (name,))
                del rows[name]
        _db().commit()

        tags_by_name = {}
        for row in _db().execute("SELECT name, tag FROM file_tags ORDER BY tag").fetchall():
            tags_by_name.setdefault(row["name"], []).append(row["tag"])

    chat_titles = {}
    for chat in chat_store.list_chats():
        chat_titles[chat["id"]] = chat["title"]

    files = []
    for name, stat in on_disk.items():
        row = rows.get(name) or {}
        chat_id = row.get("chat_id")
        files.append({
            "name": name,
            "size": stat.st_size,
            "added": row.get("added") or stat.st_mtime,
            "source": row.get("source") or SOURCE_UPLOAD,
            "chat_id": chat_id,
            "chat_title": chat_titles.get(chat_id),
            "task_id": row.get("task_id"),
            "origin_path": row.get("origin_path"),
            "tags": tags_by_name.get(name, []),
        })
    files.sort(key=lambda entry: entry["name"].lower())
    return files
