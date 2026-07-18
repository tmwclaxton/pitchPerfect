"""Offline Phase B processing from saved UI dumps."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from capture_store import CaptureAsset, CaptureRun, write_manifest  # noqa: E402
from migrate import migrate_db  # noqa: E402
from sync_process import _merge_chat_messages, _merge_profile_fields, process_one_match  # noqa: E402
import db as db_module  # noqa: E402

FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "window_dump_profile_top.xml"
)


class SyncProcessTest(unittest.TestCase):
    def test_merge_profile_from_fixture(self):
        if not FIXTURE.exists():
            self.skipTest("fixture missing")
        xml = FIXTURE.read_text(encoding="utf-8")
        fields = _merge_profile_fields([xml], "Sara")
        types = {field["field_type"] for field in fields}
        self.assertIn("prompt", types)
        self.assertIn("basic", types)

    def test_merge_chat_dedupes_across_frames(self):
        # Minimal synthetic dumps with one message bubble each.
        frame_a = """<?xml version='1.0'?>
        <hierarchy>
          <node text="" content-desc="You: Hello there" bounds="[0,100][100,200]"
                resource-id="" class="android.view.View" clickable="false"
                editable="false" selected="false" package="co.hinge.app"/>
          <node text="Send a message" content-desc=""
                resource-id="co.hinge.app:id/messageComposition"
                class="android.widget.EditText" clickable="true" editable="true"
                selected="false" package="co.hinge.app" bounds="[0,2400][1000,2600]"/>
        </hierarchy>"""
        frame_b = """<?xml version='1.0'?>
        <hierarchy>
          <node text="" content-desc="Sara: Hi back" bounds="[0,100][100,200]"
                resource-id="" class="android.view.View" clickable="false"
                editable="false" selected="false" package="co.hinge.app"/>
          <node text="" content-desc="You: Hello there" bounds="[0,300][100,400]"
                resource-id="" class="android.view.View" clickable="false"
                editable="false" selected="false" package="co.hinge.app"/>
          <node text="Send a message" content-desc=""
                resource-id="co.hinge.app:id/messageComposition"
                class="android.widget.EditText" clickable="true" editable="true"
                selected="false" package="co.hinge.app" bounds="[0,2400][1000,2600]"/>
        </hierarchy>"""
        # First frame = newer viewport; second = after scroll-up (older revealed).
        messages = _merge_chat_messages([frame_a, frame_b])
        texts = [message.text for message in messages]
        self.assertEqual(texts.count("Hello there"), 1)
        self.assertIn("Hi back", texts)

    def test_process_one_match_upserts(self):
        if not FIXTURE.exists():
            self.skipTest("fixture missing")
        xml = FIXTURE.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "run1"
            match_dir = root / "matches" / "Sara"
            match_dir.mkdir(parents=True)
            chat_path = match_dir / "chat_000.xml"
            profile_path = match_dir / "profile_000.xml"
            chat_path.write_text(xml, encoding="utf-8")
            profile_path.write_text(xml, encoding="utf-8")
            db_path = str(Path(tmp) / "t.db")
            migrate_db(db_path)

            run = CaptureRun(
                id=1,
                root_dir=str(root),
                started_at="2026-01-01T00:00:00",
                status="captured",
                matches={
                    "sara": {
                        "name": "Sara",
                        "section": "your_turn",
                        "preview": "hi",
                        "is_new_match": False,
                    }
                },
                assets=[
                    CaptureAsset(
                        id=1,
                        run_id=1,
                        match_name="Sara",
                        kind="chat",
                        sequence=0,
                        xml_path=str(chat_path.relative_to(root)),
                        image_path=None,
                        captured_at="2026-01-01T00:00:01",
                    ),
                    CaptureAsset(
                        id=2,
                        run_id=1,
                        match_name="Sara",
                        kind="profile",
                        sequence=0,
                        xml_path=str(profile_path.relative_to(root)),
                        image_path=None,
                        captured_at="2026-01-01T00:00:02",
                    ),
                ],
            )
            write_manifest(run)

            with mock.patch.object(db_module, "SQLITE_PATH", db_path), mock.patch(
                "sync_process.mark_assets_processed"
            ):
                outcome = process_one_match(
                    run,
                    "sara",
                    run.matches["sara"],
                )
            self.assertIsNone(outcome.get("error"))
            self.assertGreater(outcome.get("profile_count") or 0, 0)
            with mock.patch.object(db_module, "SQLITE_PATH", db_path):
                stats = db_module.sync_stats()
            self.assertGreaterEqual(stats["matches"], 1)
            self.assertGreater(stats.get("profile_fields", 0), 0)


if __name__ == "__main__":
    unittest.main()
