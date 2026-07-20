from typing import Any, Dict, List, Optional, Tuple

from autoswipe_config import AutoswipeSettings, load_settings
from config import NANOGPT_VISION_MODEL
from nanogpt_service import NanoGptService

PROFILE_SCORE_SYSTEM_PROMPT = (
    "You rate dating profile photos for an automated screening assistant. "
    "Return only valid JSON matching the requested schema. "
    "Score each metric from 1 to 10 using these definitions: "
    "attractiveness = overall visual appeal / 'baddie' energy; "
    "slimness = how slim or lean the person appears; "
    "quirkiness = distinctive style, personality, or vibe in photos; "
    "ethnicity_fit = how well the person matches the stated ethnicity "
    "preference (use 5 if no preference was given); "
    "notes = one short sentence summarizing the person's look and vibe."
)

# Like when composite is within this distance below min_composite ("near threshold").
NEAR_THRESHOLD_MARGIN = 0.75


def _user_prompt(settings: AutoswipeSettings) -> str:
    preference = (settings.ethnicity_preference or "").strip()
    ethnicity_line = (
        f"Ethnicity preference for ethnicity_fit: {preference}. "
        "Score ethnicity_fit higher when presentation clearly matches "
        "(East/Southeast Asian features/presentation when that is the preference). "
        "Score lower when clearly mismatched. Use mid-range when uncertain."
        if preference
        else "No ethnicity preference — set ethnicity_fit to 5."
    )
    return (
        "These are consecutive screenshots from one dating profile. "
        "Ignore app chrome and focus on the person in the photos. "
        f"{ethnicity_line} "
        "Return JSON with keys: attractiveness, slimness, quirkiness, "
        "ethnicity_fit, notes. "
        "Each score must be an integer from 1 to 10."
    )


def normalize_profile_scores(raw_scores: Dict[str, Any]) -> Dict[str, Any]:
    notes = raw_scores.get("notes", "")
    if not isinstance(notes, str):
        notes = str(notes)

    ethnicity = raw_scores.get("ethnicity_fit", raw_scores.get("ethnicityFit", 5))
    return {
        "attractiveness": _normalize_score(raw_scores.get("attractiveness")),
        "slimness": _normalize_score(raw_scores.get("slimness")),
        "quirkiness": _normalize_score(raw_scores.get("quirkiness")),
        "ethnicity_fit": _normalize_score(ethnicity, default=5),
        "notes": notes.strip(),
    }


def compute_composite_score(
    scores: Dict[str, Any],
    settings: Optional[AutoswipeSettings] = None,
) -> float:
    """
    Weighted average of vision metrics (1–10 scale).

    composite = Σ(weight_i * score_i) / Σ(weight_i)
    When ethnicity_preference is empty, ethnicity_fit weight is ignored so
    a neutral 5 does not drag the composite.
    """
    cfg = settings or load_settings()
    parts = [
        (cfg.weight_attractiveness, float(scores.get("attractiveness") or 0)),
        (cfg.weight_slimness, float(scores.get("slimness") or 0)),
        (cfg.weight_quirkiness, float(scores.get("quirkiness") or 0)),
    ]
    if (cfg.ethnicity_preference or "").strip():
        parts.append(
            (cfg.weight_ethnicity_fit, float(scores.get("ethnicity_fit") or 0))
        )

    total_w = sum(max(0.0, w) for w, _ in parts)
    if total_w <= 0:
        return 0.0
    return round(sum(max(0.0, w) * s for w, s in parts) / total_w, 2)


def score_profile_images(
    image_paths: List[str],
    service: Optional[NanoGptService] = None,
    settings: Optional[AutoswipeSettings] = None,
) -> Dict[str, Any]:
    if not image_paths:
        raise ValueError("At least one profile image is required for scoring.")

    cfg = settings or load_settings()
    scorer = service or NanoGptService(model=NANOGPT_VISION_MODEL)
    raw_scores = scorer.chat_with_images(
        prompt=_user_prompt(cfg),
        image_paths=image_paths,
        system_prompt=PROFILE_SCORE_SYSTEM_PROMPT,
        temperature=0.2,
        max_tokens=300,
        model=NANOGPT_VISION_MODEL,
        json_response=True,
    )
    scores = normalize_profile_scores(raw_scores)
    scores["composite"] = compute_composite_score(scores, cfg)
    scores["uncertain"] = False
    scores["vision_failed"] = False
    return scores


def vision_failure_scores(reason: str = "Vision scoring failed") -> Dict[str, Any]:
    """Placeholder scores that bias the swipe decision to LIKE."""
    return {
        "attractiveness": 0,
        "slimness": 0,
        "quirkiness": 0,
        "ethnicity_fit": 0,
        "composite": None,
        "notes": reason,
        "uncertain": True,
        "vision_failed": True,
    }


def is_uncertain_scores(scores: Dict[str, Any]) -> bool:
    if scores.get("vision_failed") or scores.get("uncertain"):
        return True
    notes = str(scores.get("notes") or "").lower()
    if "vision scoring failed" in notes or "uncertain" in notes:
        return True
    # All-zero / missing composite after a failed normalize path.
    if scores.get("composite") is None:
        return True
    return False


def should_like_profile(
    scores: Dict[str, Any],
    settings: Optional[AutoswipeSettings] = None,
    *,
    near_margin: float = NEAR_THRESHOLD_MARGIN,
) -> bool:
    """
    Bias to LIKE when unsure.

    LIKE when:
      - vision failed / uncertain, OR
      - composite >= min_composite, OR
      - composite is near the threshold (within near_margin below)

    PASS only when composite is clearly below (composite < min_composite - margin).
    Individual floors are soft: they never force a pass if composite is near/above.
    """
    cfg = settings or load_settings()

    if is_uncertain_scores(scores):
        return True

    composite = scores.get("composite")
    if composite is None:
        return True
    try:
        composite_f = float(composite)
    except (TypeError, ValueError):
        return True

    # Clear pass band only.
    clear_pass_below = float(cfg.min_composite) - float(near_margin)
    if composite_f < clear_pass_below:
        return False

    # At/above threshold, or near it → like.
    return True


def like_decision_reason(
    scores: Dict[str, Any],
    settings: Optional[AutoswipeSettings] = None,
    *,
    near_margin: float = NEAR_THRESHOLD_MARGIN,
) -> Tuple[bool, str]:
    """Return (should_like, human reason) for logging."""
    cfg = settings or load_settings()
    if is_uncertain_scores(scores):
        return True, "like (uncertain/vision error — bias right)"
    composite = scores.get("composite")
    try:
        composite_f = float(composite)
    except (TypeError, ValueError):
        return True, "like (missing composite — bias right)"
    if composite_f >= cfg.min_composite:
        return True, f"like (composite {composite_f} >= {cfg.min_composite})"
    clear_pass_below = float(cfg.min_composite) - float(near_margin)
    if composite_f >= clear_pass_below:
        return (
            True,
            f"like (composite {composite_f} near threshold {cfg.min_composite})",
        )
    return (
        False,
        f"pass (composite {composite_f} clearly < {cfg.min_composite})",
    )


def format_scores_for_comment(scores: Dict[str, Any]) -> str:
    composite = scores.get("composite")
    composite_bit = (
        f"composite: {composite}/10, " if composite is not None else ""
    )
    return (
        f"Vision scores - {composite_bit}"
        f"attractiveness: {scores['attractiveness']}/10, "
        f"slimness: {scores['slimness']}/10, "
        f"quirkiness: {scores['quirkiness']}/10, "
        f"ethnicity_fit: {scores.get('ethnicity_fit', 5)}/10. "
        f"Notes: {scores['notes']}"
    )


def _normalize_score(value: Any, default: int = 0) -> int:
    try:
        score = int(round(float(value)))
    except (TypeError, ValueError):
        return default

    return max(1, min(10, score))
