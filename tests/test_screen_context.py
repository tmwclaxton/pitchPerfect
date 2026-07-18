"""Unit tests for Hinge screen classification / lost-context guards."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from ui_dump import (  # noqa: E402
    SCREEN_DISCOVER,
    SCREEN_MATCH_CHAT,
    SCREEN_MATCH_PROFILE,
    SCREEN_MATCHES_LIST,
    UiNode,
    classify_hinge_screen,
    on_match_profile_screen,
    parse_ui_nodes,
)


FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "window_dump_profile_top.xml"
)


def _node(
    *,
    text="",
    desc="",
    bounds=(0, 0, 100, 100),
    selected=False,
    clickable=False,
    editable=False,
    class_name="View",
    resource_id="",
):
    return UiNode(
        text=text,
        content_desc=desc,
        resource_id=resource_id,
        class_name=class_name,
        clickable=clickable,
        editable=editable,
        selected=selected,
        bounds=bounds,
        children_text=[],
    )


class ScreenContextTest(unittest.TestCase):
    def test_fixture_is_match_profile(self):
        if not FIXTURE.exists():
            self.skipTest("profile UI dump fixture missing")
        xml_text = FIXTURE.read_text(encoding="utf-8")
        nodes = parse_ui_nodes(xml_text)
        ctx = classify_hinge_screen(
            nodes, 2800, xml_text=xml_text, expect_match="Sara"
        )
        self.assertEqual(ctx.kind, SCREEN_MATCH_PROFILE)
        self.assertTrue(
            on_match_profile_screen(nodes, 2800, "Sara", xml_text=xml_text)
        )

    def test_discover_feed_not_match_profile(self):
        nodes = [
            _node(text="Discover", desc="Discover", bounds=(40, 2700, 200, 2780), selected=True),
            _node(text="Matches", desc="Matches", bounds=(900, 2700, 1100, 2780)),
            _node(text="Like", desc="Like", bounds=(900, 2400, 1100, 2550), clickable=True),
            _node(desc="Alex’s photo", bounds=(70, 400, 1200, 1400)),
            _node(desc="Age", bounds=(100, 1500, 200, 1600)),
            _node(text="25", bounds=(220, 1500, 300, 1600)),
        ]
        ctx = classify_hinge_screen(nodes, 2800, expect_match="Alex")
        self.assertEqual(ctx.kind, SCREEN_DISCOVER)
        self.assertFalse(on_match_profile_screen(nodes, 2800, "Alex"))

    def test_matches_list_your_turn(self):
        nodes = [
            _node(text="Your turn (3)", bounds=(40, 200, 400, 280)),
            _node(text="Matches", desc="Matches", bounds=(900, 2700, 1100, 2780), selected=True),
            _node(
                text="Sara",
                bounds=(40, 400, 1200, 560),
                clickable=True,
            ),
        ]
        ctx = classify_hinge_screen(nodes, 2800)
        self.assertEqual(ctx.kind, SCREEN_MATCHES_LIST)

    def test_match_chat_tabs(self):
        nodes = [
            _node(text="Luana", bounds=(500, 160, 700, 240)),
            _node(desc="Luana, Verified", bounds=(500, 160, 700, 240)),
            _node(text="Chat", bounds=(40, 320, 600, 400)),
            _node(text="Profile", bounds=(640, 320, 1200, 400)),
            _node(
                text="Send a message",
                resource_id="co.hinge.app:id/messageComposition",
                class_name="EditText",
                editable=True,
                bounds=(40, 2500, 1000, 2650),
            ),
        ]
        ctx = classify_hinge_screen(nodes, 2800, expect_match="Luana")
        self.assertEqual(ctx.kind, SCREEN_MATCH_CHAT)


if __name__ == "__main__":
    unittest.main()
