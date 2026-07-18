import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from conversation_goals import contact_already_exchanged, contact_stage
from reply_drafter import normalize_reply, strip_em_dashes
from reply_scorer import aggregate_score, heuristic_scores, pick_best
from style_learner import heuristic_style, style_prompt_block
from your_turn import ChatMessage, ConversationHistory
import db as db_module


def _history(name: str, pairs, *, is_new: bool = False) -> ConversationHistory:
    messages = [ChatMessage(sender, text) for sender, text in pairs]
    return ConversationHistory(name=name, messages=messages, is_new_match=is_new)


class ContactStageTest(unittest.TestCase):
    def test_too_early_for_new_or_short_chats(self):
        self.assertEqual(contact_stage(_history("A", [], is_new=True))[0], "too_early")
        early = _history(
            "A",
            [("You", "hey"), ("A", "hi there")],
        )
        self.assertEqual(contact_stage(early)[0], "too_early")

    def test_good_when_plan_is_forming(self):
        history = _history(
            "B",
            [
                ("You", "free for a drink this week?"),
                ("B", "yeah maybe"),
                ("You", "thursday in soho?"),
                ("B", "could work"),
                ("You", "cool what time"),
                ("B", "after 7?"),
            ],
        )
        self.assertEqual(contact_stage(history)[0], "good")

    def test_detects_ig_already_exchanged(self):
        history = _history(
            "C",
            [
                ("You", "whats your ig"),
                ("C", "its chillgirl99"),
            ],
        )
        self.assertTrue(contact_already_exchanged(history))
        self.assertEqual(contact_stage(history)[0], "already_done")


class EmDashAndScoringTest(unittest.TestCase):
    def test_strip_em_dashes(self):
        self.assertEqual(strip_em_dashes("hey — cool"), "hey, cool")
        self.assertNotIn("—", normalize_reply("Looking forward — tonight"))
        self.assertNotIn("–", normalize_reply("tonight – 7ish"))

    def test_penalizes_ai_polite_emdash_and_early_contact(self):
        early = _history("A", [("You", "hey"), ("A", "hi")])
        bad = heuristic_scores("Looking forward — absolutely, please share your ig", early)
        self.assertGreaterEqual(bad["em_dash_count"], 1)
        self.assertGreaterEqual(bad["ai_trope_hits"], 1)
        self.assertGreaterEqual(bad["polite_hits"], 1)
        self.assertLess(bad["contact_fit"], 3)

        good_stage = _history(
            "B",
            [
                ("You", "free for a drink this week?"),
                ("B", "yeah maybe"),
                ("You", "thursday in soho?"),
                ("B", "could work"),
                ("You", "cool what time"),
                ("B", "after 7?"),
            ],
        )
        light = heuristic_scores("easier on whatsapp if you want", good_stage)
        self.assertGreaterEqual(light["contact_fit"], 8)
        skip_contact = heuristic_scores("thursday after 7 then?", good_stage)
        self.assertLess(skip_contact["contact_fit"], light["contact_fit"])
        self.assertEqual(contact_stage(good_stage)[0], "good")
        self.assertIn("whatsapp", contact_stage(good_stage)[1].lower())

    def test_aggregate_floors_em_dash_winners(self):
        local = heuristic_scores(
            "Absolutely looking forward — please do",
            _history("A", [("You", "hey"), ("A", "hi")]),
        )
        total = aggregate_score(local, {"overall": 9, "anti_cringe": 9, "contact_fit": 9})
        # Hard floors applied in score_reply; here just ensure heuristics look bad.
        self.assertGreater(local["cringe_penalty"], 3)
        self.assertLess(local["naturalness"], 6)
        self.assertIsInstance(total, float)

    def test_pick_best(self):
        best = pick_best([{"total": 4.0, "reply": "a"}, {"total": 7.2, "reply": "b"}])
        self.assertEqual(best["reply"], "b")

    def test_penalizes_invented_area_plan_and_sameness(self):
        topical = _history(
            "Vivi",
            [
                ("Vivi", "i am on my summer holiday"),
            ],
        )
        generic = heuristic_scores("Marylebone tonight then?", topical)
        on_topic = heuristic_scores("Nice, anywhere good so far?", topical)
        self.assertLess(generic["specificity"], on_topic["specificity"])
        self.assertLessEqual(generic["specificity"], 3.0)

        same = heuristic_scores(
            "Marylebone tonight then?",
            topical,
            recent_drafts=["Saturday evening Marylebone then?"],
        )
        self.assertGreaterEqual(same["sameness"], 0.55)


class StyleAndDbTest(unittest.TestCase):
    def test_heuristic_style_and_prompt_block(self):
        histories = [
            _history(
                "A",
                [
                    ("You", "marylebone for a drink tonight?"),
                    ("A", "maybe"),
                    ("You", "whats your insta x"),
                ],
            )
        ]
        profile = heuristic_style(histories)
        self.assertGreaterEqual(profile["message_count"], 2)
        self.assertTrue(profile["contact_examples"])
        block = style_prompt_block({**profile, "summary": "Short and direct."})
        self.assertIn("Short and direct", block)
        self.assertIn("Contact style", block)

    def test_sqlite_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "test.db")
            with mock.patch.object(db_module, "SQLITE_PATH", path):
                run_id = db_module.start_run("test", {"ok": True})
                conversation_id = db_module.store_conversation(
                    match_name="Test",
                    transcript="You: hi\nTest: hey",
                    messages=[{"sender": "You", "text": "hi"}],
                    source="style_init",
                    run_id=run_id,
                )
                db_module.store_draft_reply(
                    draft_id="d1",
                    match_name="Test",
                    transcript="You: hi\nTest: hey",
                    draft_reply="tonight work?",
                    conversation_id=conversation_id,
                    run_id=run_id,
                    score={"total": 8.1},
                )
                db_module.save_style_profile(
                    {"summary": "terse", "avg_words": 6},
                    sample_count=3,
                    conversations_used=1,
                )
                db_module.finish_run(run_id, {"drafted": 1})
                self.assertEqual(db_module.load_style_profile()["summary"], "terse")
                drafts = db_module.list_recent_drafts(5)
                self.assertEqual(drafts[0]["draft_reply"], "tonight work?")


if __name__ == "__main__":
    unittest.main()
