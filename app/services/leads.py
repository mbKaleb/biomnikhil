"""Leads: candidate findings the lab may want to follow up, kept across chats.

A lead is saved from an agent's answer (or added by hand) and then moves
through new, testing, validated or failed as the lab works on it. The answer's
own text says how far it was checked: the review and the planted-truth assay
append sections to it (biomni_runner._review_answer and _assay_answer), and
review_state and assay_state read those back, so a lead shows whether it was
checked without anyone typing that in.

Stored in its own SQLite file next to the chat history, so deleting a chat
never deletes a lead.
"""
from __future__ import annotations

import sqlite3
import threading
import time
import uuid

from ..config import Config
from . import workflow

STATUSES = ("new", "testing", "validated", "failed")
OPEN_STATUSES = ("new", "testing")
MAX_TITLE_CHARS = 200
MAX_EVIDENCE_CHARS = 100_000
MAX_NOTE_CHARS = 20_000
DERIVED_TITLE_CHARS = 100

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


class LeadError(ValueError):
    """A request the leads board can't act on; the message says why."""


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        Config.LEADS_DB.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(Config.LEADS_DB, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS leads (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL,
                evidence TEXT NOT NULL,
                review TEXT NOT NULL,
                assay TEXT NOT NULL,
                chat_id TEXT,
                notes TEXT NOT NULL,
                next_experiment TEXT NOT NULL,
                created REAL NOT NULL,
                updated REAL NOT NULL
            );
            """
        )
        _conn.commit()
    return _conn


# ---- reading what was checked off an answer's text -------------------------

_REVIEW_HEADING = "**Adversarial review**\n\n"
_REVIEW_STATES_BY_VERDICT = {
    "HOLDS": "holds",
    "HOLDS WITH CAVEATS": "caveats",
    "DOES NOT HOLD": "does_not_hold",
}


def review_state(answer: str) -> str:
    """How far the answer's review got: holds, caveats, does_not_hold, revised
    (it didn't hold and was redone, and the redo wasn't reviewed), unclear (no
    verdict), unreviewed (the review was stopped or failed) or none."""
    if "\n\n---\n\n**Revised after review." in answer:
        return "revised"
    if "\n\n---\n\n**The review gave no clear verdict" in answer:
        return "unclear"
    heading_at = answer.find(_REVIEW_HEADING)
    if heading_at >= 0:
        review = answer[heading_at + len(_REVIEW_HEADING):]
        verdict = workflow.review_verdict(review)
        return _REVIEW_STATES_BY_VERDICT.get(verdict, "unclear")
    if "so this answer is unreviewed" in answer:
        return "unreviewed"
    return "none"


_ASSAY_HEADINGS = (
    ("**Planted-truth assay**\n\n", "ran"),
    ("**The planted-truth assay doesn't apply", "not_applicable"),
    ("**The planted-truth assay couldn't reproduce", "couldnt_reproduce"),
    ("**The planted-truth assay gave no clear status", "unclear"),
    ("**The planted-truth assay was stopped", "untested"),
    ("**The planted-truth assay failed", "untested"),
)


def assay_state(answer: str) -> str:
    """What the planted-truth assay did for the answer: ran, not_applicable,
    couldnt_reproduce, unclear, untested (stopped or failed) or none."""
    for heading, state in _ASSAY_HEADINGS:
        if heading in answer:
            return state
    return "none"


def title_from(text: str) -> str:
    """A short title for a lead saved from an answer: its first line of text,
    without the Markdown around it."""
    for line in text.splitlines():
        cleaned = line.strip().lstrip("#>*-_ \t")
        for mark in ("**", "__", "`"):
            cleaned = cleaned.replace(mark, "")
        cleaned = cleaned.strip().rstrip(": \t")
        if not cleaned:
            continue
        if len(cleaned) <= DERIVED_TITLE_CHARS:
            return cleaned
        cut = cleaned[:DERIVED_TITLE_CHARS]
        last_space = cut.rfind(" ")
        if last_space > DERIVED_TITLE_CHARS // 2:
            cut = cut[:last_space]
        return cut.rstrip(",;: ") + "…"
    return ""


# ---- storage ---------------------------------------------------------------

def _lead_from(row: sqlite3.Row) -> dict:
    return dict(row)


def _text(value, name: str, limit: int) -> str:
    """A text field from a request: missing counts as empty."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise LeadError(f"{name} must be text")
    value = value.strip()
    if len(value) > limit:
        raise LeadError(f"{name} is too long (the limit is {limit:,} characters)")
    return value


def list_leads() -> list[dict]:
    with _lock:
        rows = _db().execute("SELECT * FROM leads ORDER BY updated DESC").fetchall()
    leads = []
    for row in rows:
        leads.append(_lead_from(row))
    return leads


def get_lead(lead_id: str) -> dict | None:
    with _lock:
        row = _db().execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    return _lead_from(row) if row else None


def create_lead(
    title=None, evidence=None, chat_id=None, notes=None, next_experiment=None,
) -> tuple[dict, bool]:
    """Adds a lead and returns it with whether it is new. Saving the same
    evidence from the same chat again returns the lead already there, so a
    double click doesn't make two."""
    evidence = _text(evidence, "evidence", MAX_EVIDENCE_CHARS)
    title = _text(title, "title", MAX_TITLE_CHARS) or title_from(evidence)
    if not title:
        raise LeadError("a lead needs a title")
    notes = _text(notes, "notes", MAX_NOTE_CHARS)
    next_experiment = _text(next_experiment, "next experiment", MAX_NOTE_CHARS)
    if chat_id is not None and not isinstance(chat_id, str):
        raise LeadError("chat_id must be text")

    now = time.time()
    lead = {
        "id": uuid.uuid4().hex[:12],
        "title": title,
        "status": "new",
        "evidence": evidence,
        "review": review_state(evidence),
        "assay": assay_state(evidence),
        "chat_id": chat_id or None,
        "notes": notes,
        "next_experiment": next_experiment,
        "created": now,
        "updated": now,
    }
    with _lock:
        if evidence and chat_id:
            existing = _db().execute(
                "SELECT * FROM leads WHERE chat_id = ? AND evidence = ?", (chat_id, evidence),
            ).fetchone()
            if existing:
                return _lead_from(existing), False
        _db().execute(
            "INSERT INTO leads (id, title, status, evidence, review, assay, chat_id,"
            " notes, next_experiment, created, updated)"
            " VALUES (:id, :title, :status, :evidence, :review, :assay, :chat_id,"
            " :notes, :next_experiment, :created, :updated)",
            lead,
        )
        _db().commit()
    return lead, True


_EDITABLE_TEXT = {
    "title": ("title", MAX_TITLE_CHARS),
    "notes": ("notes", MAX_NOTE_CHARS),
    "next_experiment": ("next experiment", MAX_NOTE_CHARS),
}


def update_lead(lead_id: str, changes: dict) -> dict | None:
    """Applies the status, title, notes and next experiment in `changes`;
    anything else in it is ignored. Returns the lead, or None for an unknown id."""
    updates = {}
    if "status" in changes:
        status = changes["status"]
        if status not in STATUSES:
            raise LeadError("status must be one of: " + ", ".join(STATUSES))
        updates["status"] = status
    for field, (label, limit) in _EDITABLE_TEXT.items():
        if field not in changes:
            continue
        value = _text(changes[field], label, limit)
        if field == "title" and not value:
            raise LeadError("a lead needs a title")
        updates[field] = value

    with _lock:
        if _db().execute("SELECT 1 FROM leads WHERE id = ?", (lead_id,)).fetchone() is None:
            return None
        if updates:
            updates["updated"] = time.time()
            assignments = []
            for field in updates:
                assignments.append(f"{field} = :{field}")  # field names come from the fixed sets above
            _db().execute(
                f"UPDATE leads SET {', '.join(assignments)} WHERE id = :id",
                {**updates, "id": lead_id},
            )
            _db().commit()
        row = _db().execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    return _lead_from(row)


def delete_lead(lead_id: str) -> bool:
    with _lock:
        cursor = _db().execute("DELETE FROM leads WHERE id = ?", (lead_id,))
        _db().commit()
    return cursor.rowcount > 0
