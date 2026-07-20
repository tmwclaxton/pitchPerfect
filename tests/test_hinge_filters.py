import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from hinge_filters import (
    ASIAN_BADDIES_ETHNICITIES,
    DESC_DATING_PREFERENCES,
    on_dating_preferences,
    on_discover_with_prefs_entry,
    on_ethnicity_picker,
    parse_checkable_rows,
    resolve_ethnicity_labels,
)
from ui_dump import parse_ui_nodes

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "hinge_filters"


class HingeFiltersFixtureTest(unittest.TestCase):
    def test_discover_has_dating_preferences_entry(self):
        xml = (FIXTURES / "discover.xml").read_text(encoding="utf-8")
        nodes = parse_ui_nodes(xml)
        self.assertTrue(on_discover_with_prefs_entry(nodes))
        self.assertTrue(
            any(
                DESC_DATING_PREFERENCES.lower() in n.content_desc.lower()
                for n in nodes
                if n.clickable
            )
        )

    def test_dating_preferences_screen_and_ethnicity_row(self):
        xml = (FIXTURES / "dating_preferences.xml").read_text(encoding="utf-8")
        nodes = parse_ui_nodes(xml)
        self.assertTrue(on_dating_preferences(nodes))
        eth = [
            n
            for n in nodes
            if n.clickable and "ethnicity" in n.content_desc.lower()
        ]
        self.assertEqual(1, len(eth))
        self.assertIn("Open to all", eth[0].content_desc)

    def test_ethnicity_picker_rows(self):
        xml = (FIXTURES / "ethnicity_picker.xml").read_text(encoding="utf-8")
        nodes = parse_ui_nodes(xml)
        self.assertTrue(on_ethnicity_picker(xml, nodes))
        rows = parse_checkable_rows(xml)
        labels = [row.label for row in rows]
        self.assertIn("East Asian", labels)
        self.assertIn("Southeast Asian", labels)
        self.assertIn("South Asian", labels)
        self.assertIn("Open to all", labels)
        open_row = next(row for row in rows if row.label == "Open to all")
        self.assertTrue(open_row.checked)
        east = next(row for row in rows if row.label == "East Asian")
        self.assertFalse(east.checked)

    def test_asian_baddies_labels(self):
        self.assertEqual(
            ["East Asian", "Southeast Asian"],
            resolve_ethnicity_labels(preset="asian_baddies"),
        )
        self.assertEqual(
            list(ASIAN_BADDIES_ETHNICITIES),
            resolve_ethnicity_labels(preset="asian"),
        )


if __name__ == "__main__":
    unittest.main()
