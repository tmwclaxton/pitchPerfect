# app/db.py
"""SQLite persistence for drafts, chat histories, style profile, and runs."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional

from config import SQLITE_PATH


def _utc_now() -> str:
    return datetime.utcnow().isoformat()


def ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


@contextmanager
def connect(db_path: Optional[str] = None) -> Iterator[sqlite3.Connection]:
    path = db_path or SQLITE_PATH
    ensure_parent_dir(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        init_schema(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            meta_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_name TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'your_turn',
            is_new_match INTEGER NOT NULL DEFAULT 0,
            transcript TEXT NOT NULL,
            messages_json TEXT NOT NULL DEFAULT '[]',
            message_count INTEGER NOT NULL DEFAULT 0,
            collected_at TEXT NOT NULL,
            run_id INTEGER,
            FOREIGN KEY(run_id) REFERENCES runs(id)
        );

        CREATE TABLE IF NOT EXISTS draft_replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_id TEXT NOT NULL UNIQUE,
            match_name TEXT NOT NULL,
            conversation_id INTEGER,
            transcript TEXT NOT NULL,
            draft_reply TEXT NOT NULL,
            pasted INTEGER NOT NULL DEFAULT 0,
            is_new_match INTEGER NOT NULL DEFAULT 0,
            score_json TEXT,
            candidates_json TEXT,
            created_at TEXT NOT NULL,
            run_id INTEGER,
            FOREIGN KEY(conversation_id) REFERENCES conversations(id),
            FOREIGN KEY(run_id) REFERENCES runs(id)
        );

        CREATE TABLE IF NOT EXISTS style_profile (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            profile_json TEXT NOT NULL,
            sample_count INTEGER NOT NULL DEFAULT 0,
            conversations_used INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_conversations_name
            ON conversations(match_name);
        CREATE INDEX IF NOT EXISTS idx_drafts_name
            ON draft_replies(match_name);
        """
    )


def start_run(kind: str, meta: Optional[Dict[str, Any]] = None) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO runs (kind, started_at, meta_json) VALUES (?, ?, ?)",
            (kind, _utc_now(), json.dumps(meta or {})),
        )
        return int(cur.lastrowid)


def finish_run(run_id: int, meta: Optional[Dict[str, Any]] = None) -> None:
    with connect() as conn:
        if meta is not None:
            conn.execute(
                "UPDATE runs SET finished_at = ?, meta_json = ? WHERE id = ?",
                (_utc_now(), json.dumps(meta), run_id),
            )
        else:
            conn.execute(
                "UPDATE runs SET finished_at = ? WHERE id = ?",
                (_utc_now(), run_id),
            )


def store_conversation(
    match_name: str,
    transcript: str,
    messages: Optional[List[Dict[str, Any]]] = None,
    *,
    source: str = "your_turn",
    is_new_match: bool = False,
    run_id: Optional[int] = None,
) -> int:
    messages = messages or []
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO conversations (
                match_name, source, is_new_match, transcript,
                messages_json, message_count, collected_at, run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                match_name,
                source,
                1 if is_new_match else 0,
                transcript,
                json.dumps(messages),
                len(messages),
                _utc_now(),
                run_id,
            ),
        )
        return int(cur.lastrowid)


def store_draft_reply(
    draft_id: str,
    match_name: str,
    transcript: str,
    draft_reply: str,
    *,
    pasted: bool = False,
    is_new_match: bool = False,
    score: Optional[Dict[str, Any]] = None,
    candidates: Optional[List[Dict[str, Any]]] = None,
    conversation_id: Optional[int] = None,
    run_id: Optional[int] = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO draft_replies (
                draft_id, match_name, conversation_id, transcript, draft_reply,
                pasted, is_new_match, score_json, candidates_json, created_at, run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                draft_id,
                match_name,
                conversation_id,
                transcript,
                draft_reply,
                1 if pasted else 0,
                1 if is_new_match else 0,
                json.dumps(score) if score is not None else None,
                json.dumps(candidates or []),
                _utc_now(),
                run_id,
            ),
        )


def save_style_profile(
    profile: Dict[str, Any],
    *,
    sample_count: int = 0,
    conversations_used: int = 0,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO style_profile (
                id, profile_json, sample_count, conversations_used, updated_at
            ) VALUES (1, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                profile_json = excluded.profile_json,
                sample_count = excluded.sample_count,
                conversations_used = excluded.conversations_used,
                updated_at = excluded.updated_at
            """,
            (
                json.dumps(profile, indent=2),
                sample_count,
                conversations_used,
                _utc_now(),
            ),
        )


def load_style_profile() -> Optional[Dict[str, Any]]:
    with connect() as conn:
        row = conn.execute(
            "SELECT profile_json FROM style_profile WHERE id = 1"
        ).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row["profile_json"])
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def list_recent_drafts(limit: int = 20) -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT draft_id, match_name, draft_reply, pasted, created_at
            FROM draft_replies
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]
