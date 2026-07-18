import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from migrate import migrate_db
from profile_scraper import extract_profile_fields_from_nodes
from ui_dump import parse_ui_nodes
import db as db_module


FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "window_dump_profile_top.xml"
)


class ProfileScraperTest(unittest.TestCase):
    def test_extracts_prompts_and_basics_from_dump(self):
        if not FIXTURE.exists():
            self.skipTest("profile UI dump fixture missing")
        nodes = parse_ui_nodes(FIXTURE.read_text(encoding="utf-8"))
        fields = extract_profile_fields_from_nodes(nodes, match_name="Sara")
        types = {field.field_type for field in fields}
        self.assertIn("prompt", types)
        self.assertIn("basic", types)

        prompts = [f for f in fields if f.field_type == "prompt"]
        self.assertTrue(prompts)
        self.assertIn("stay sane", prompts[0].text_content.lower())
        self.assertIn("should", (prompts[0].label or "").lower())

        basics = {
            (f.label or "").lower(): f.text_content for f in fields if f.field_type == "basic"
        }
        self.assertEqual(basics.get("age"), "23")
        self.assertEqual(basics.get("job"), "Fashion production")
        self.assertIn("english", (basics.get("languages spoken") or "").lower())

    def test_profile_field_upsert_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "t.db")
            migrate_db(path)
            with mock.patch.object(db_module, "SQLITE_PATH", path):
                match = db_module.upsert_match_history(
                    "Sara",
                    [{"sender": "You", "text": "hey"}],
                    section="your_turn",
                )
                fields = [
                    {
                        "field_type": "basic",
                        "label": "Age",
                        "text_content": "23",
                    },
                    {
                        "field_type": "prompt",
                        "label": "You should not go out with me if",
                        "text_content": "U wanna stay sane",
                    },
                ]
                first = db_module.upsert_profile_fields(match["match_id"], fields)
                second = db_module.upsert_profile_fields(match["match_id"], fields)
                self.assertEqual(first, (2, 2))
                self.assertEqual(second[0], 0)
                self.assertEqual(second[1], 2)
                loaded = db_module.load_profile_fields(match["match_id"])
                self.assertEqual(len(loaded), 2)
                stats = db_module.sync_stats()
                self.assertEqual(stats["profile_fields"], 2)


if __name__ == "__main__":
    unittest.main()
