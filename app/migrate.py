# app/migrate.py
"""Lightweight versioned SQLite migrations (SQL files in app/migrations/)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime
from typing import List, Optional, Tuple

MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "migrations")


def _utc_now() -> str:
    return datetime.utcnow().isoformat()


def _content_hash(
    sender: str, body: str, timestamp_label: Optional[str] = None
) -> str:
    norm_body = re.sub(r"\s+", " ", (body or "").strip()).lower()
    norm_sender = (sender or "").strip().lower()
    stamp = (timestamp_label or "").strip().lower()
    payload = f"{norm_sender}|{norm_body}|{stamp}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:40]


def ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def _list_migration_files() -> List[Tuple[int, str, str]]:
    """Return [(version, name, path), ...] sorted by version."""
    if not os.path.isdir(MIGRATIONS_DIR):
        return []
    found: List[Tuple[int, str, str]] = []
    for filename in os.listdir(MIGRATIONS_DIR):
        if not filename.endswith(".sql"):
            continue
        match = re.match(r"^(\d+)_(.+)\.sql$", filename)
        if not match:
            continue
        version = int(match.group(1))
        name = match.group(2)
        found.append((version, name, os.path.join(MIGRATIONS_DIR, filename)))
    found.sort(key=lambda item: item[0])
    return found


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> List[str]:
    if not _table_exists(conn, table):
        return []
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def _ensure_column(
    conn: sqlite3.Connection, table: str, column: str, declaration: str
) -> None:
    if column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def _applied_versions(conn: sqlite3.Connection) -> set:
    if not _table_exists(conn, "schema_migrations"):
        return set()
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {int(row[0]) for row in rows}


_JUNK_MATCH_NAMES = {
    "profile",
    "chat",
    "local",
    "matches",
    "hinge",
    "sent",
    "delivered",
    "read",
    "liked",
    "search",
    "gt",
    "send a message",
}


def _is_junk_legacy_name(name: str) -> bool:
    cleaned = (name or "").strip()
    if not cleaned:
        return True
    key = cleaned.lower()
    if key in _JUNK_MATCH_NAMES:
        return True
    if len(cleaned) > 48:
        return True
    if len(cleaned.split()) >= 5 and any(ch in cleaned for ch in ".,?!"):
        return True
    return False


def _migrate_legacy_conversations(conn: sqlite3.Connection) -> int:
    """Copy blob conversations.messages_json into matches + messages once."""
    if not _table_exists(conn, "conversations") or not _table_exists(conn, "matches"):
        return 0
    if not _table_exists(conn, "messages"):
        return 0

    # Only migrate rows whose name_key is not already present.
    legacy_rows = conn.execute(
        """
        SELECT id, match_name, source, is_new_match, transcript,
               messages_json, collected_at
        FROM conversations
        ORDER BY id ASC
        """
    ).fetchall()
    migrated = 0
    for row in legacy_rows:
        name = row["match_name"]
        if _is_junk_legacy_name(name):
            continue
        name_key = name.strip().lower()
        existing = conn.execute(
            "SELECT id FROM matches WHERE name_key = ?", (name_key,)
        ).fetchone()
        if existing:
            continue
        now = row["collected_at"] or _utc_now()
        cur = conn.execute(
            """
            INSERT INTO matches (
                name, name_key, section, is_new_match, list_preview,
                message_count, first_seen_at, last_synced_at, meta_json
            ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (
                name,
                name_key,
                row["source"] or "legacy",
                int(row["is_new_match"] or 0),
                None,
                now,
                now,
                json.dumps({"legacy_conversation_id": row["id"]}),
            ),
        )
        match_id = int(cur.lastrowid)
        try:
            messages = json.loads(row["messages_json"] or "[]")
        except json.JSONDecodeError:
            messages = []
        if not isinstance(messages, list):
            messages = []

        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                continue
            sender = str(message.get("sender") or "").strip() or "Unknown"
            body = str(message.get("text") or message.get("body") or "").strip()
            if not body:
                continue
            stamp = message.get("timestamp") or message.get("timestamp_label")
            stamp_s = str(stamp).strip() if stamp else None
            content_hash = _content_hash(sender, body, stamp_s)
            conn.execute(
                """
                INSERT OR IGNORE INTO messages (
                    match_id, sender, body, timestamp_label, position,
                    content_hash, scraped_at, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    match_id,
                    sender,
                    body,
                    stamp_s,
                    index,
                    content_hash,
                    now,
                    json.dumps(message),
                ),
            )
        count = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE match_id = ?", (match_id,)
        ).fetchone()[0]
        conn.execute(
            "UPDATE matches SET message_count = ? WHERE id = ?",
            (count, match_id),
        )
        migrated += 1
    return migrated


def apply_migrations(
    conn: sqlite3.Connection,
    *,
    migrations_dir: Optional[str] = None,
) -> List[int]:
    """
    Apply pending *.sql migrations. Returns list of newly applied versions.
    """
    global MIGRATIONS_DIR
    if migrations_dir:
        MIGRATIONS_DIR = migrations_dir

    conn.execute("PRAGMA foreign_keys = ON")
    applied = _applied_versions(conn)
    newly: List[int] = []

    for version, name, path in _list_migration_files():
        if version in applied:
            continue
        with open(path, "r", encoding="utf-8") as handle:
            sql = handle.read()
        conn.executescript(sql)
        # Ensure schema_migrations row even if the SQL file created the table.
        conn.execute(
            """
            INSERT OR IGNORE INTO schema_migrations (version, name, applied_at)
            VALUES (?, ?, ?)
            """,
            (version, name, _utc_now()),
        )
        newly.append(version)

    # Soft upgrades for DBs that already had draft_replies from older init_schema.
    if _table_exists(conn, "draft_replies"):
        _ensure_column(conn, "draft_replies", "match_id", "INTEGER")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_drafts_match_id
                ON draft_replies(match_id)
            """
        )

    if _table_exists(conn, "matches"):
        _ensure_column(conn, "matches", "profile_field_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "matches", "profile_synced_at", "TEXT")

    migrated = _migrate_legacy_conversations(conn)
    if migrated:
        print(f"Migrated {migrated} legacy conversation blob(s) into matches/messages.")

    return newly


def migrate_db(db_path: str) -> List[int]:
    ensure_parent_dir(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        newly = apply_migrations(conn)
        conn.commit()
        return newly
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    from config import SQLITE_PATH

    applied = migrate_db(SQLITE_PATH)
    print(f"SQLite: {SQLITE_PATH}")
    print(f"Applied migrations: {applied or 'none (already up to date)'}")
