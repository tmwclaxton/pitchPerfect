from typing import Any, Dict, List, Optional

from config import (
    NANOGPT_VISION_MODEL,
    PROFILE_MIN_ATTRACTIVENESS,
    PROFILE_MIN_SLIMNESS,
)
from nanogpt_service import NanoGptService

PROFILE_SCORE_SYSTEM_PROMPT = (
    "You rate dating profile photos for an automated screening assistant. "
    "Return only valid JSON matching the requested schema. "
    "Score each metric from 1 to 10 using these definitions: "
    "attractiveness = overall visual appeal; "
    "slimness = how slim or lean the person appears; "
    "quirkiness = distinctive style, personality, or vibe in photos. "
    "notes = one short sentence summarizing the person's look and vibe."
)

PROFILE_SCORE_USER_PROMPT = (
    "These are consecutive screenshots from one dating profile. "
    "Ignore app chrome and focus on the person in the photos. "
    "Return JSON with keys: attractiveness, slimness, quirkiness, notes. "
    "Each score must be an integer from 1 to 10."
)


def normalize_profile_scores(raw_scores: Dict[str, Any]) -> Dict[str, Any]:
    notes = raw_scores.get("notes", "")
    if not isinstance(notes, str):
        notes = str(notes)

    return {
        "attractiveness": _normalize_score(raw_scores.get("attractiveness")),
        "slimness": _normalize_score(raw_scores.get("slimness")),
        "quirkiness": _normalize_score(raw_scores.get("quirkiness")),
        "notes": notes.strip(),
    }


def score_profile_images(
    image_paths: List[str],
    service: Optional[NanoGptService] = None,
) -> Dict[str, Any]:
    if not image_paths:
        raise ValueError("At least one profile image is required for scoring.")

    scorer = service or NanoGptService(model=NANOGPT_VISION_MODEL)
    raw_scores = scorer.chat_with_images(
        prompt=PROFILE_SCORE_USER_PROMPT,
        image_paths=image_paths,
        system_prompt=PROFILE_SCORE_SYSTEM_PROMPT,
        temperature=0.2,
        max_tokens=300,
        model=NANOGPT_VISION_MODEL,
        json_response=True,
    )
    return normalize_profile_scores(raw_scores)


def should_like_profile(scores: Dict[str, Any]) -> bool:
    return (
        scores["attractiveness"] >= PROFILE_MIN_ATTRACTIVENESS
        and scores["slimness"] >= PROFILE_MIN_SLIMNESS
    )


def format_scores_for_comment(scores: Dict[str, Any]) -> str:
    return (
        f"Vision scores - attractiveness: {scores['attractiveness']}/10, "
        f"slimness: {scores['slimness']}/10, "
        f"quirkiness: {scores['quirkiness']}/10. "
        f"Notes: {scores['notes']}"
    )


def _normalize_score(value: Any) -> int:
    try:
        score = int(round(float(value)))
    except (TypeError, ValueError):
        return 0

    return max(1, min(10, score))
