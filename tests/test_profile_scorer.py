import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from autoswipe_config import AutoswipeSettings
from profile_scorer import (
    compute_composite_score,
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
                "ethnicity_fit": 12,
                "notes": "  Bright outdoor photos  ",
            }
        )

        self.assertEqual(
            {
                "attractiveness": 9,
                "slimness": 10,
                "quirkiness": 1,
                "ethnicity_fit": 10,
                "notes": "Bright outdoor photos",
            },
            scores,
        )

    def test_compute_composite_score_weighted(self):
        settings = AutoswipeSettings(
            weight_attractiveness=0.5,
            weight_slimness=0.2,
            weight_quirkiness=0.1,
            weight_ethnicity_fit=0.2,
            ethnicity_preference="East/Southeast Asian",
        )
        scores = {
            "attractiveness": 8,
            "slimness": 6,
            "quirkiness": 4,
            "ethnicity_fit": 9,
        }
        # (0.5*8 + 0.2*6 + 0.1*4 + 0.2*9) / 1.0 = 7.4
        self.assertEqual(7.4, compute_composite_score(scores, settings))

    def test_composite_ignores_ethnicity_weight_without_preference(self):
        settings = AutoswipeSettings(
            weight_attractiveness=0.5,
            weight_slimness=0.3,
            weight_quirkiness=0.2,
            weight_ethnicity_fit=0.9,
            ethnicity_preference="",
        )
        scores = {
            "attractiveness": 8,
            "slimness": 6,
            "quirkiness": 4,
            "ethnicity_fit": 1,
        }
        # ethnicity weight ignored: (0.5*8 + 0.3*6 + 0.2*4) / 1.0 = 6.6
        self.assertEqual(6.6, compute_composite_score(scores, settings))

    def test_should_like_biases_right_when_uncertain_or_near(self):
        settings = AutoswipeSettings(
            min_composite=6.0,
            weight_attractiveness=0.5,
            weight_slimness=0.2,
            weight_quirkiness=0.1,
            weight_ethnicity_fit=0.2,
            ethnicity_preference="East/Southeast Asian",
        )
        like_scores = {
            "attractiveness": 7,
            "slimness": 6,
            "quirkiness": 5,
            "ethnicity_fit": 8,
            "notes": "ok",
        }
        like_scores["composite"] = compute_composite_score(like_scores, settings)
        self.assertGreaterEqual(like_scores["composite"], 6.0)
        self.assertTrue(should_like_profile(like_scores, settings))

        # Near threshold (5.4 with margin 0.75) → like.
        near = {"composite": 5.4, "notes": "borderline", "uncertain": False}
        self.assertTrue(should_like_profile(near, settings))

        # Clearly below → pass.
        low = {"composite": 4.0, "notes": "low", "uncertain": False}
        self.assertFalse(should_like_profile(low, settings))

        # Vision failure / uncertain → like.
        from profile_scorer import vision_failure_scores

        self.assertTrue(should_like_profile(vision_failure_scores(), settings))

    def test_score_profile_images_uses_nanogpt_vision(self):
        class FakeNanoGpt:
            def chat_with_images(self, **kwargs):
                self.kwargs = kwargs
                return {
                    "attractiveness": 8,
                    "slimness": 7,
                    "quirkiness": 6,
                    "ethnicity_fit": 9,
                    "notes": "Stylish and relaxed",
                }

        fake_service = FakeNanoGpt()
        settings = AutoswipeSettings(
            ethnicity_preference="East/Southeast Asian",
            weight_attractiveness=0.5,
            weight_slimness=0.2,
            weight_quirkiness=0.1,
            weight_ethnicity_fit=0.2,
        )
        scores = score_profile_images(
            ["images/a.png", "images/b.png"],
            fake_service,
            settings=settings,
        )

        self.assertEqual(8, scores["attractiveness"])
        self.assertIn("composite", scores)
        self.assertTrue(fake_service.kwargs["json_response"])
        self.assertIn("East/Southeast Asian", fake_service.kwargs["prompt"])
        self.assertEqual(
            ["images/a.png", "images/b.png"], fake_service.kwargs["image_paths"]
        )

    def test_format_scores_for_comment(self):
        text = format_scores_for_comment(
            {
                "attractiveness": 8,
                "slimness": 7,
                "quirkiness": 6,
                "ethnicity_fit": 9,
                "composite": 7.5,
                "notes": "Outdoor vibe",
            }
        )

        self.assertIn("composite: 7.5/10", text)
        self.assertIn("attractiveness: 8/10", text)
        self.assertIn("Outdoor vibe", text)


class AutoswipeConfigTest(unittest.TestCase):
    def test_asian_baddies_preset_thresholds(self):
        from autoswipe_config import PRESETS

        preset = PRESETS["asian_baddies"]
        self.assertEqual(6.0, preset["min_composite"])
        self.assertEqual("East/Southeast Asian", preset["ethnicity_preference"])
        self.assertGreaterEqual(preset["weight_attractiveness"], 0.45)

    def test_apply_preset_persists(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        import autoswipe_config as cfg
        import db as db_module
        from migrate import migrate_db

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "t.db")
            json_path = str(Path(tmp) / "settings.json")
            migrate_db(db_path)
            with mock.patch.object(db_module, "SQLITE_PATH", db_path), mock.patch.object(
                cfg, "SETTINGS_JSON_PATH", json_path
            ):
                settings = cfg.apply_preset("asian_baddies")
                self.assertEqual("asian_baddies", settings.preset)
                self.assertEqual(6.0, settings.min_composite)
                loaded = cfg.load_settings()
                self.assertEqual("asian_baddies", loaded.preset)
                self.assertEqual("East/Southeast Asian", loaded.ethnicity_preference)


if __name__ == "__main__":
    unittest.main()
