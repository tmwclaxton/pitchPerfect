import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from datetime import datetime, timedelta
from unittest import mock

from db import (
    match_is_fresh,
    message_content_hash,
    sync_stats,
    upsert_match_history,
    upsert_profile_fields,
)
from migrate import apply_migrations, migrate_db
import db as db_module
import sqlite3


class MigrationsAndUpsertTest(unittest.TestCase):
    def test_migrations_create_matches_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "t.db")
            applied = migrate_db(path)
            self.assertIn(1, applied)
            conn = sqlite3.connect(path)
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertIn("matches", tables)
            self.assertIn("messages", tables)
            self.assertIn("profile_fields", tables)
            self.assertIn("schema_migrations", tables)
            versions = [
                row[0]
                for row in conn.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )
            ]
            self.assertEqual(versions, [1, 2])
            # Second run is a no-op.
            self.assertEqual(migrate_db(path), [])
            conn.close()

    def test_message_upsert_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "t.db")
            migrate_db(path)
            import db as db_module
            from unittest import mock

            with mock.patch.object(db_module, "SQLITE_PATH", path):
                msgs = [
                    {"sender": "You", "text": "free thursday?", "timestamp": "Mon"},
                    {"sender": "Ada", "text": "maybe", "timestamp": "Mon"},
                ]
                first = upsert_match_history(
                    "Ada", msgs, section="their_turn", list_preview="maybe"
                )
                second = upsert_match_history(
                    "Ada", msgs, section="their_turn", list_preview="maybe"
                )
                self.assertEqual(first["match_id"], second["match_id"])
                self.assertEqual(first["inserted"], 2)
                self.assertEqual(second["inserted"], 0)
                self.assertEqual(second["message_count"], 2)
                # Add one new message; old ones must not duplicate.
                msgs2 = msgs + [
                    {"sender": "You", "text": "soho 7?", "timestamp": "Tue"}
                ]
                third = upsert_match_history("Ada", msgs2, section="your_turn")
                self.assertEqual(third["inserted"], 1)
                self.assertEqual(third["message_count"], 3)
                stats = sync_stats()
                self.assertEqual(stats["matches"], 1)
                self.assertEqual(stats["messages"], 3)

    def test_content_hash_stable(self):
        a = message_content_hash("You", "Hello  there", "Mon")
        b = message_content_hash("you", "hello there", "mon")
        self.assertEqual(a, b)

    def test_legacy_conversations_migrate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "legacy.db")
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE conversations (
                    id INTEGER PRIMARY KEY,
                    match_name TEXT,
                    source TEXT,
                    is_new_match INTEGER,
                    transcript TEXT,
                    messages_json TEXT,
                    message_count INTEGER,
                    collected_at TEXT,
                    run_id INTEGER
                );
                """
            )
            conn.execute(
                """
                INSERT INTO conversations (
                    match_name, source, is_new_match, transcript,
                    messages_json, message_count, collected_at
                ) VALUES (?, ?, 0, ?, ?, 2, ?)
                """,
                (
                    "Bea",
                    "style_init",
                    "You: hi\nBea: hey",
                    json.dumps(
                        [
                            {"sender": "You", "text": "hi", "timestamp": None},
                            {"sender": "Bea", "text": "hey", "timestamp": "Today"},
                        ]
                    ),
                    "2026-01-01T00:00:00",
                ),
            )
            conn.commit()
            apply_migrations(conn)
            conn.commit()
            match = conn.execute(
                "SELECT id, message_count FROM matches WHERE name_key='bea'"
            ).fetchone()
            self.assertIsNotNone(match)
            self.assertEqual(match["message_count"], 2)
            msg_count = conn.execute(
                "SELECT COUNT(*) AS c FROM messages WHERE match_id = ?",
                (match["id"],),
            ).fetchone()["c"]
            self.assertEqual(msg_count, 2)
            conn.close()

    def test_match_is_fresh_skips_recent_full_sync(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "t.db")
            migrate_db(path)
            with mock.patch.object(db_module, "SQLITE_PATH", path):
                result = upsert_match_history(
                    "Ada",
                    [
                        {"sender": "You", "text": "hey"},
                        {"sender": "Ada", "text": "hi"},
                    ],
                )
                upsert_profile_fields(
                    result["match_id"],
                    [
                        {
                            "field_type": "basic",
                            "label": "Age",
                            "text_content": "25",
                        }
                    ],
                )
                self.assertTrue(
                    match_is_fresh("Ada", require_profile=True, max_age_hours=24)
                )
                self.assertFalse(
                    match_is_fresh("Ada", require_profile=True, max_age_hours=0)
                )
                # Age the sync timestamps so it is no longer fresh.
                old = (datetime.utcnow() - timedelta(hours=48)).isoformat()
                conn = sqlite3.connect(path)
                conn.execute(
                    """
                    UPDATE matches
                    SET last_synced_at = ?, profile_synced_at = ?
                    WHERE name_key = 'ada'
                    """,
                    (old, old),
                )
                conn.commit()
                conn.close()
                self.assertFalse(
                    match_is_fresh("Ada", require_profile=True, max_age_hours=24)
                )


if __name__ == "__main__":
    unittest.main()
