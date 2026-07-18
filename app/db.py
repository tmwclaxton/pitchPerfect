# app/db.py
"""SQLite persistence for matches, messages, drafts, style, and runs."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from config import SQLITE_PATH
from migrate import apply_migrations, ensure_parent_dir


def _utc_now() -> str:
    return datetime.utcnow().isoformat()


def message_content_hash(
    sender: str,
    body: str,
    timestamp_label: Optional[str] = None,
) -> str:
    """Stable idempotency key for a chat bubble."""
    norm_body = re.sub(r"\s+", " ", (body or "").strip()).lower()
    norm_sender = (sender or "").strip().lower()
    stamp = (timestamp_label or "").strip().lower()
    payload = f"{norm_sender}|{norm_body}|{stamp}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:40]


def name_key(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip()).lower()


@contextmanager
def connect(db_path: Optional[str] = None) -> Iterator[sqlite3.Connection]:
    path = db_path or SQLITE_PATH
    ensure_parent_dir(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        apply_migrations(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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


def upsert_match(
    match_name: str,
    *,
    section: Optional[str] = None,
    is_new_match: bool = False,
    list_preview: Optional[str] = None,
    hinge_id: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> int:
    """Insert or update a match row; returns match id."""
    key = name_key(match_name)
    now = _utc_now()
    with connect() as conn:
        row = conn.execute(
            "SELECT id, meta_json FROM matches WHERE name_key = ?", (key,)
        ).fetchone()
        if row:
            match_id = int(row["id"])
            merged_meta = {}
            try:
                merged_meta = json.loads(row["meta_json"] or "{}")
            except json.JSONDecodeError:
                merged_meta = {}
            if meta:
                merged_meta.update(meta)
            conn.execute(
                """
                UPDATE matches SET
                    name = ?,
                    hinge_id = COALESCE(?, hinge_id),
                    section = COALESCE(?, section),
                    is_new_match = ?,
                    list_preview = COALESCE(?, list_preview),
                    meta_json = ?
                WHERE id = ?
                """,
                (
                    match_name.strip(),
                    hinge_id,
                    section,
                    1 if is_new_match else 0,
                    list_preview,
                    json.dumps(merged_meta),
                    match_id,
                ),
            )
            return match_id

        cur = conn.execute(
            """
            INSERT INTO matches (
                name, name_key, hinge_id, section, is_new_match, list_preview,
                message_count, first_seen_at, last_synced_at, meta_json
            ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, NULL, ?)
            """,
            (
                match_name.strip(),
                key,
                hinge_id,
                section,
                1 if is_new_match else 0,
                list_preview,
                now,
                json.dumps(meta or {}),
            ),
        )
        return int(cur.lastrowid)


def upsert_match_messages(
    match_id: int,
    messages: Sequence[Dict[str, Any]],
    *,
    scraped_at: Optional[str] = None,
) -> Tuple[int, int]:
    """
    Upsert messages for a match (idempotent via content_hash).
    Returns (inserted_count, total_count).
    """
    return _upsert_match_messages_impl(
        match_id, messages, scraped_at or _utc_now()
    )


def _upsert_match_messages_impl(
    match_id: int,
    messages: Sequence[Dict[str, Any]],
    scraped_at: str,
) -> Tuple[int, int]:
    inserted = 0
    with connect() as conn:
        for index, message in enumerate(messages):
            sender = str(message.get("sender") or "").strip() or "Unknown"
            body = str(message.get("text") or message.get("body") or "").strip()
            if not body:
                continue
            stamp = message.get("timestamp") or message.get("timestamp_label")
            stamp_s = str(stamp).strip() if stamp else None
            content_hash = message_content_hash(sender, body, stamp_s)
            exists = conn.execute(
                """
                SELECT id FROM messages
                WHERE match_id = ? AND content_hash = ?
                """,
                (match_id, content_hash),
            ).fetchone()
            raw = message.get("raw")
            raw_json = json.dumps(raw) if raw is not None else None
            if exists:
                conn.execute(
                    """
                    UPDATE messages SET
                        position = ?,
                        timestamp_label = COALESCE(?, timestamp_label),
                        scraped_at = ?,
                        raw_json = COALESCE(?, raw_json)
                    WHERE id = ?
                    """,
                    (index, stamp_s, scraped_at, raw_json, int(exists["id"])),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO messages (
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
                        scraped_at,
                        raw_json,
                    ),
                )
                inserted += 1

        total = conn.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE match_id = ?",
            (match_id,),
        ).fetchone()["c"]
        conn.execute(
            """
            UPDATE matches SET
                message_count = ?,
                last_synced_at = ?
            WHERE id = ?
            """,
            (total, scraped_at, match_id),
        )
    return inserted, int(total)


def upsert_match_history(
    match_name: str,
    messages: Sequence[Dict[str, Any]],
    *,
    section: Optional[str] = None,
    is_new_match: bool = False,
    list_preview: Optional[str] = None,
    hinge_id: Optional[str] = None,
    run_id: Optional[int] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Upsert match + messages. Returns ids and insert stats."""
    extra = dict(meta or {})
    if run_id is not None:
        extra["last_run_id"] = run_id
    match_id = upsert_match(
        match_name,
        section=section,
        is_new_match=is_new_match,
        list_preview=list_preview,
        hinge_id=hinge_id,
        meta=extra,
    )
    inserted, total = _upsert_match_messages_impl(
        match_id, list(messages), _utc_now()
    )
    return {
        "match_id": match_id,
        "inserted": inserted,
        "message_count": total,
    }


def get_match_by_name(match_name: str) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM matches WHERE name_key = ?",
            (name_key(match_name),),
        ).fetchone()
    return dict(row) if row else None


def _parse_iso_utc(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        # Stored as utcnow().isoformat() — accept trailing Z too.
        return datetime.fromisoformat(text.replace("Z", ""))
    except ValueError:
        return None


def match_is_fresh(
    match_name: str,
    *,
    require_profile: bool = True,
    max_age_hours: float = 24.0,
) -> bool:
    """
    True when this match was synced recently and already has messages
    (and profile fields when require_profile).
    """
    row = get_match_by_name(match_name)
    if not row:
        return False
    if int(row.get("message_count") or 0) <= 0:
        return False
    if require_profile and int(row.get("profile_field_count") or 0) <= 0:
        return False
    synced_at = _parse_iso_utc(row.get("last_synced_at"))
    if require_profile:
        profile_at = _parse_iso_utc(row.get("profile_synced_at"))
        # Need both timestamps recent when profiles are required.
        if synced_at is None or profile_at is None:
            return False
        oldest = min(synced_at, profile_at)
    else:
        if synced_at is None:
            return False
        oldest = synced_at
    age = datetime.utcnow() - oldest
    return age <= timedelta(hours=float(max_age_hours))


def list_matches(limit: int = 500) -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, name, section, is_new_match, message_count,
                   last_synced_at, list_preview
            FROM matches
            ORDER BY (last_synced_at IS NULL), last_synced_at DESC, name COLLATE NOCASE
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def load_match_messages(match_id: int) -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT sender, body, timestamp_label, position, content_hash, scraped_at
            FROM messages
            WHERE match_id = ?
            ORDER BY position ASC, id ASC
            """,
            (match_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def match_transcript(match_id: int) -> str:
    messages = load_match_messages(match_id)
    if not messages:
        return "(No messages yet - this is a new match.)"
    lines = []
    for message in messages:
        stamp = (
            f" [{message['timestamp_label']}]"
            if message.get("timestamp_label")
            else ""
        )
        lines.append(f"{message['sender']}{stamp}: {message['body']}")
    return "\n".join(lines)


def profile_field_content_hash(
    field_type: str,
    label: Optional[str],
    text_content: str,
) -> str:
    normalized_text = re.sub(r"\s+", " ", (text_content or "").strip()).lower()
    payload = (
        f"{(field_type or '').strip().lower()}|"
        f"{(label or '').strip().lower()}|"
        f"{normalized_text}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:40]


def upsert_profile_fields(
    match_id: int,
    fields: Sequence[Dict[str, Any]],
    *,
    scraped_at: Optional[str] = None,
) -> Tuple[int, int]:
    """
    Upsert profile fields for a match (idempotent via content_hash).
    Returns (inserted_count, total_count).
    """
    scraped_at = scraped_at or _utc_now()
    inserted = 0
    with connect() as conn:
        for index, field in enumerate(fields):
            field_type = str(field.get("field_type") or "other").strip() or "other"
            label = field.get("label")
            label_s = str(label).strip() if label else None
            text = str(
                field.get("text_content") or field.get("text") or field.get("body") or ""
            ).strip()
            if not text:
                continue
            content_hash = profile_field_content_hash(field_type, label_s, text)
            raw = field.get("raw")
            raw_json = json.dumps(raw) if raw is not None else None
            exists = conn.execute(
                """
                SELECT id FROM profile_fields
                WHERE match_id = ? AND content_hash = ?
                """,
                (match_id, content_hash),
            ).fetchone()
            if exists:
                conn.execute(
                    """
                    UPDATE profile_fields SET
                        sort_order = ?,
                        label = COALESCE(?, label),
                        scraped_at = ?,
                        raw_json = COALESCE(?, raw_json)
                    WHERE id = ?
                    """,
                    (index, label_s, scraped_at, raw_json, int(exists["id"])),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO profile_fields (
                        match_id, field_type, label, text_content, sort_order,
                        content_hash, scraped_at, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        match_id,
                        field_type,
                        label_s,
                        text,
                        index,
                        content_hash,
                        scraped_at,
                        raw_json,
                    ),
                )
                inserted += 1

        total = conn.execute(
            "SELECT COUNT(*) AS c FROM profile_fields WHERE match_id = ?",
            (match_id,),
        ).fetchone()["c"]
        conn.execute(
            """
            UPDATE matches SET
                profile_field_count = ?,
                profile_synced_at = ?
            WHERE id = ?
            """,
            (total, scraped_at, match_id),
        )
    return inserted, int(total)


def load_profile_fields(match_id: int) -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT field_type, label, text_content, sort_order, scraped_at
            FROM profile_fields
            WHERE match_id = ?
            ORDER BY sort_order ASC, id ASC
            """,
            (match_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def sync_stats() -> Dict[str, int]:
    with connect() as conn:
        matches = conn.execute("SELECT COUNT(*) AS c FROM matches").fetchone()["c"]
        messages = conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
        with_msgs = conn.execute(
            "SELECT COUNT(*) AS c FROM matches WHERE message_count > 0"
        ).fetchone()["c"]
        profile_fields = 0
        with_profiles = 0
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "profile_fields" in tables:
            profile_fields = conn.execute(
                "SELECT COUNT(*) AS c FROM profile_fields"
            ).fetchone()["c"]
            with_profiles = conn.execute(
                "SELECT COUNT(*) AS c FROM matches WHERE profile_field_count > 0"
            ).fetchone()["c"]
    return {
        "matches": int(matches),
        "messages": int(messages),
        "matches_with_messages": int(with_msgs),
        "profile_fields": int(profile_fields),
        "matches_with_profiles": int(with_profiles),
    }


# --- backward-compatible helpers used by draft_replies / style init ---


def store_conversation(
    match_name: str,
    transcript: str,
    messages: Optional[List[Dict[str, Any]]] = None,
    *,
    source: str = "your_turn",
    is_new_match: bool = False,
    run_id: Optional[int] = None,
) -> int:
    """Upsert into matches/messages; returns match_id."""
    result = upsert_match_history(
        match_name,
        messages or [],
        section=source,
        is_new_match=is_new_match,
        run_id=run_id,
        meta={"transcript_snapshot": transcript[:2000] if transcript else ""},
    )
    return int(result["match_id"])


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
    match = get_match_by_name(match_name)
    match_id = int(match["id"]) if match else conversation_id
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO draft_replies (
                draft_id, match_name, match_id, conversation_id, transcript,
                draft_reply, pasted, is_new_match, score_json, candidates_json,
                created_at, run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                draft_id,
                match_name,
                match_id,
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


def load_all_you_messages(limit: int = 5000) -> List[Dict[str, Any]]:
    """Messages sent by the user, for style learning from the DB."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT m.sender, m.body, m.timestamp_label, mt.name AS match_name
            FROM messages m
            JOIN matches mt ON mt.id = m.match_id
            WHERE lower(m.sender) = 'you'
            ORDER BY m.id ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]
