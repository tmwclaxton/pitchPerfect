import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from profile_scorer import (
    format_scores_for_comment,
    normalize_profile_scores,
    score_profile_images,
    should_like_profile,
)


class ProfileScorerTest(unittest.TestCase):
    def test_normalize_profile_scores_clamps_values(self):
        scores = normalize_profile_scores(
            {
                "attractiveness": "8.6",
                "slimness": 11,
                "quirkiness": 0,
                "notes": "  Bright outdoor photos  ",
            }
        )

        self.assertEqual(
            {
                "attractiveness": 9,
                "slimness": 10,
                "quirkiness": 1,
                "notes": "Bright outdoor photos",
            },
            scores,
        )

    def test_should_like_profile_uses_thresholds(self):
        passing_scores = {
            "attractiveness": 7,
            "slimness": 6,
            "quirkiness": 4,
            "notes": "Casual style",
        }
        failing_scores = {
            "attractiveness": 7,
            "slimness": 4,
            "quirkiness": 8,
            "notes": "Casual style",
        }

        self.assertTrue(should_like_profile(passing_scores))
        self.assertFalse(should_like_profile(failing_scores))

    def test_score_profile_images_uses_nanogpt_vision(self):
        class FakeNanoGpt:
            def chat_with_images(self, **kwargs):
                self.kwargs = kwargs
                return {
                    "attractiveness": 8,
                    "slimness": 7,
                    "quirkiness": 6,
                    "notes": "Stylish and relaxed",
                }

        fake_service = FakeNanoGpt()
        scores = score_profile_images(["images/a.png", "images/b.png"], fake_service)

        self.assertEqual(8, scores["attractiveness"])
        self.assertTrue(fake_service.kwargs["json_response"])
        self.assertEqual(["images/a.png", "images/b.png"], fake_service.kwargs["image_paths"])

    def test_format_scores_for_comment(self):
        text = format_scores_for_comment(
            {
                "attractiveness": 8,
                "slimness": 7,
                "quirkiness": 6,
                "notes": "Outdoor vibe",
            }
        )

        self.assertIn("attractiveness: 8/10", text)
        self.assertIn("Outdoor vibe", text)


if __name__ == "__main__":
    unittest.main()
