"""SQLite-backed chat history: chats survive restarts, unlike the in-memory
task registry. Single connection guarded by a lock — plenty for one lab box.
"""
from __future__ import annotations

import sqlite3
import threading
import time
import uuid

from ..config import Config

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        Config.CHAT_DB.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(Config.CHAT_DB, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS chats (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created REAL NOT NULL,
                updated REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created REAL NOT NULL
            );
            """
        )
        _conn.execute("PRAGMA foreign_keys = ON")
        _conn.commit()
    return _conn


def list_chats() -> list[dict]:
    with _lock:
        rows = _db().execute(
            "SELECT id, title, created, updated FROM chats ORDER BY updated DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def create_chat(title: str = "New chat") -> dict:
    now = time.time()
    chat = {"id": uuid.uuid4().hex[:12], "title": title, "created": now, "updated": now}
    with _lock:
        _db().execute(
            "INSERT INTO chats (id, title, created, updated) VALUES (?, ?, ?, ?)",
            (chat["id"], title, now, now),
        )
        _db().commit()
    return chat


def get_chat(chat_id: str) -> dict | None:
    with _lock:
        chat = _db().execute(
            "SELECT id, title, created, updated FROM chats WHERE id = ?", (chat_id,)
        ).fetchone()
        if chat is None:
            return None
        msgs = _db().execute(
            "SELECT role, content, created FROM messages"
            " WHERE chat_id = ? ORDER BY id",
            (chat_id,),
        ).fetchall()
    out = dict(chat)
    out["messages"] = [dict(m) for m in msgs]
    return out


def rename_chat(chat_id: str, title: str) -> bool:
    with _lock:
        cur = _db().execute(
            "UPDATE chats SET title = ?, updated = ? WHERE id = ?",
            (title, time.time(), chat_id),
        )
        _db().commit()
    return cur.rowcount > 0


def delete_chat(chat_id: str) -> bool:
    with _lock:
        _db().execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
        cur = _db().execute("DELETE FROM chats WHERE id = ?", (chat_id,))
        _db().commit()
    return cur.rowcount > 0


def add_message(chat_id: str, role: str, content: str) -> bool:
    now = time.time()
    with _lock:
        chat = _db().execute("SELECT 1 FROM chats WHERE id = ?", (chat_id,)).fetchone()
        if chat is None:
            return False
        _db().execute(
            "INSERT INTO messages (chat_id, role, content, created) VALUES (?, ?, ?, ?)",
            (chat_id, role, content, now),
        )
        _db().execute("UPDATE chats SET updated = ? WHERE id = ?", (now, chat_id))
        _db().commit()
    return True
