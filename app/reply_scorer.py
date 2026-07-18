# app/reply_scorer.py
"""Score drafted Hinge replies using dating-advice metrics + NanoGPT."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from nanogpt_service import NanoGptService
from your_turn import ConversationHistory

# Hard cringe / try-hard tells from common men's dating advice.
CRINGE_PHRASES = [
    r"\bi'?d love to\b",
    r"\bno worries( at all)?\b",
    r"\ball good\b",
    r"\bperfect for\b",
    r"\bcozy little\b",
    r"\bwhat do you say\b",
    r"\bcan'?t wait\b",
    r"\bso glad\b",
    r"\bamazing\b",
    r"\bliterally\b",
    r"\bhaha!\b",
    r"\blol!\b",
    r"\bwe should (hang|meet) sometime\b",
    r"\bhow are you\b",
    r"\bhey there\b",
    r"\bbet i can\b",
    r"\bcoffee challenge\b",
    r"\blow-key bar\b",
    r"\bi know a (great )?spot\b",
    r"\bspot near\b",
]

# ChatGPT-ish / brochure phrasing.
AI_TROPE_PHRASES = [
    r"\babsolutely\b",
    r"\blooking forward\b",
    r"\bthrilled\b",
    r"\bdelighted\b",
    r"\btestament\b",
    r"\btapestry\b",
    r"\bdelve\b",
    r"\bvibrant\b",
    r"\bcurated\b",
    r"\bnestled\b",
    r"\bmoreover\b",
    r"\bfurthermore\b",
    r"\bindeed\b",
    r"\bit'?s worth noting\b",
    r"\bin today'?s\b",
    r"\blet me know your thoughts\b",
    r"\bi'?d be happy to\b",
    r"\bkeen to\b",
    r"\bshall we\b",
    r"\bnot only\b.+\bbut also\b",
    r"\bas an ai\b",
]

# Overly polite / formal softener language.
POLITE_PHRASES = [
    r"\bplease\b",
    r"\bkindly\b",
    r"\bwould you mind\b",
    r"\bif that'?s (ok|okay|alright|all right)\b",
    r"\bthank you so much\b",
    r"\bthanks so much\b",
    r"\bhope you'?re well\b",
    r"\bhope this (helps|finds)\b",
    r"\bjust wanted to\b",
    r"\bfeel free\b",
    r"\bdon'?t hesitate\b",
    r"\bit would be my pleasure\b",
    r"\bhope that works\b",
    r"\bno problem at all\b",
    r"\btotally understand\b",
    r"\bif you'?re comfortable\b",
]

EM_DASH_RE = re.compile(r"[—–―]")


def _last_their_message(history: ConversationHistory) -> str:
    for message in reversed(history.messages):
        if message.sender.lower() != "you":
            return message.text
    return ""


def _you_suggested_plan(history: ConversationHistory) -> bool:
    for message in history.messages:
        if message.sender.lower() != "you":
            continue
        if re.search(
            r"\b(drink|coffee|pint|tonight|tomorrow|grab a|meet|hang)\b",
            message.text,
            re.IGNORECASE,
        ):
            return True
    return False


def _asks_for_contact(reply: str) -> bool:
    return bool(
        re.search(
            r"\b(instagram|insta|\big\b|whatsapp|whats ?app|your number)\b",
            reply,
            re.IGNORECASE,
        )
    )


def _contact_stage_for_scoring(history: ConversationHistory) -> str:
    from conversation_goals import contact_stage

    return contact_stage(history)[0]


def heuristic_scores(reply: str, history: ConversationHistory) -> Dict[str, float]:
    """Cheap local scores (0–10) before/alongside the model judge."""
    words = re.findall(r"\S+", reply)
    word_count = len(words)
    sentences = max(1, len(re.findall(r"[.!?]+", reply)) or (1 if reply.strip() else 0))
    their_last = _last_their_message(history)
    their_words = len(re.findall(r"\S+", their_last)) or 8

    # Prefer punchy 4–12 word texts; soft-penalize overshoot.
    if word_count <= 2:
        brevity = 5.0
    elif 4 <= word_count <= 12:
        brevity = 10.0 - abs(word_count - 7) * 0.2
    elif word_count <= 16:
        brevity = 7.5
    elif word_count <= 24:
        brevity = 4.5
    else:
        brevity = 2.0

    if their_words and word_count > their_words * 1.25:
        brevity = max(1.0, brevity - 2.5)

    bangs = reply.count("!")
    low_investment = 9.5
    if bangs >= 2:
        low_investment -= 3.5
    elif bangs >= 1:
        low_investment -= 1.5
    for pattern in CRINGE_PHRASES:
        if re.search(pattern, reply, re.IGNORECASE):
            low_investment -= 3.0
    if re.search(r"\b(sorry|apologize|my bad)\b", reply, re.IGNORECASE):
        low_investment -= 3.0
    # Soft filler after their apology is especially needy.
    if re.search(r"\b(busy|late|hectic)\b", their_last, re.IGNORECASE) and re.search(
        r"\b(no worries|all good|totally fine|no problem)\b",
        reply,
        re.IGNORECASE,
    ):
        low_investment -= 2.0

    polite_hits = sum(
        1 for pattern in POLITE_PHRASES if re.search(pattern, reply, re.IGNORECASE)
    )
    if polite_hits:
        low_investment -= min(4.5, 1.8 * polite_hits)

    low_investment = max(0.0, min(10.0, low_investment))

    specificity = 6.5
    if re.search(
        r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
        r"tonight|tomorrow|this week|\d+\s*(am|pm)|"
        r"\d{1,2}ish|"
        r"marylebone|soho|shoreditch|mayfair|hackney|islington|"
        r"drink|coffee|walk|pint)\b",
        reply,
        re.IGNORECASE,
    ):
        specificity = 9.2
    if re.search(r"\bsometime\b|\bwhenever\b|\bhang out\b", reply, re.IGNORECASE):
        specificity = 2.5
    # Invented venue energy.
    if re.search(
        r"\b(i know a|there'?s a|cozy|perfect for|spot near)\b",
        reply,
        re.IGNORECASE,
    ):
        specificity = min(specificity, 4.0)

    ease = 8.0 if ("?" in reply or word_count <= 14) else 5.0
    if reply.strip().endswith("?") and sentences <= 2:
        ease = 9.2

    # When a plan was floated, reward short confirm/time asks.
    if _you_suggested_plan(history) and word_count <= 12 and specificity >= 8.5:
        brevity = min(10.0, brevity + 0.8)
        ease = min(10.0, ease + 0.5)

    # Contact steering: reward only when the stage fits; punish early/pushy asks.
    contact_fit = 7.0
    stage = _contact_stage_for_scoring(history)
    asks_contact = _asks_for_contact(reply)
    pushy_contact = bool(
        re.search(
            r"\b(add me|follow me|dm me|give me your|what's your (number|instagram|insta|ig))\b",
            reply,
            re.IGNORECASE,
        )
    )
    if asks_contact and stage == "too_early":
        contact_fit = 1.5
        low_investment = max(0.0, low_investment - 2.5)
    elif asks_contact and stage == "already_done":
        contact_fit = 2.0
    elif asks_contact and stage == "good" and word_count <= 16 and not pushy_contact:
        contact_fit = 9.5
    elif asks_contact and stage == "maybe" and word_count <= 14 and not pushy_contact:
        contact_fit = 8.0
    elif asks_contact and pushy_contact:
        contact_fit = 2.5
        low_investment = max(0.0, low_investment - 2.0)
    elif stage == "good" and not asks_contact:
        # Fine to skip contact even when the window is open.
        contact_fit = 7.5

    cringe_risk = 10.0 - low_investment  # high = bad; invert later in aggregate
    natural = 8.5
    if re.search(
        r"\b(utilize|delightful|enchanting|nestled|unexpectedly)\b",
        reply,
        re.IGNORECASE,
    ):
        natural = 2.5
    if sentences > 2:
        natural -= 2.5
    if word_count >= 20:
        natural -= 1.5

    ai_hits = sum(
        1 for pattern in AI_TROPE_PHRASES if re.search(pattern, reply, re.IGNORECASE)
    )
    if ai_hits:
        natural = max(0.0, natural - min(5.0, 2.0 * ai_hits))
        low_investment = max(0.0, low_investment - min(3.0, 1.2 * ai_hits))
        cringe_risk = min(10.0, cringe_risk + min(4.0, 1.5 * ai_hits))
    if polite_hits:
        natural = max(0.0, natural - min(4.0, 1.5 * polite_hits))
        cringe_risk = min(10.0, cringe_risk + min(3.0, 1.2 * polite_hits))

    # Em dashes are a hard AI tell in dating texts.
    em_dash_count = len(EM_DASH_RE.findall(reply))
    if em_dash_count:
        natural = max(0.0, natural - min(6.0, 3.0 * em_dash_count))
        cringe_risk = min(10.0, cringe_risk + min(5.0, 2.5 * em_dash_count))
        low_investment = max(0.0, low_investment - min(3.0, 1.5 * em_dash_count))

    return {
        "brevity": round(min(10.0, max(0.0, brevity)), 2),
        "low_investment": round(low_investment, 2),
        "specificity": round(specificity, 2),
        "ease_of_reply": round(ease, 2),
        "naturalness": round(max(0.0, natural), 2),
        "contact_fit": round(contact_fit, 2),
        "cringe_penalty": round(max(0.0, cringe_risk), 2),
        "ai_trope_hits": float(ai_hits),
        "polite_hits": float(polite_hits),
        "em_dash_count": float(em_dash_count),
        "word_count": float(word_count),
        "sentence_count": float(sentences),
    }


SCORE_SYSTEM = """You judge dating-app replies the way a blunt men's dating coach would.
Prefer short, calm, specific texts that sound like a real guy texting.
Punish try-hard, needy, essay-like, AI-suave, or overly polite lines.
Especially punish:
- AI tropes / ChatGPT tone (absolutely, looking forward, delve, vibrant, curated, "let me know your thoughts")
- Overly polite softener language (please, kindly, hope you're well, feel free, just wanted to)
- Em dashes (—) or en dashes used as fancy punctuation
- "no worries", invented venues, "what do you say", long setups
Asking for Instagram/WhatsApp is good only when rapport/plan is established and the ask is light; punish early or pushy contact asks.
Score each metric 0-10. Return JSON only."""


def model_scores(
    reply: str,
    history: ConversationHistory,
    *,
    service: Optional[NanoGptService] = None,
) -> Dict[str, Any]:
    service = service or NanoGptService()
    transcript = history.as_transcript()
    prompt = f"""Conversation with {history.name}:
{transcript}

Candidate reply from You:
{reply}

Score JSON with keys:
- brevity (higher = shorter / less-is-more)
- low_investment (higher = cooler, not needy)
- specificity (higher = concrete detail/plan, not vague)
- ease_of_reply (higher = easy for them to answer)
- naturalness (higher = sounds like a real guy texting, not AI)
- contact_fit (higher = right time/tone for IG/WhatsApp ask, or correctly skipped)
- anti_cringe (higher = NOT cringe; 10 = clean, 0 = very cringe)
- overall (0-10 weighted gut check)
- reason (one short clause)
Hard caps: if the reply uses an em dash (—), or sounds AI/overly polite, anti_cringe and naturalness should be <= 4.
"""
    return service.chat_json(
        [
            {"role": "system", "content": SCORE_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=250,
    )


def aggregate_score(local: Dict[str, float], judged: Dict[str, Any]) -> float:
    anti_cringe = float(judged.get("anti_cringe", 10.0 - local["cringe_penalty"]))
    contact_fit = float(judged.get("contact_fit", local.get("contact_fit", 7.0)))
    # Heuristics dominate so model leniency can't crown cringe winners.
    parts = [
        local["brevity"] * 1.6,
        local["low_investment"] * 1.8,
        local["specificity"] * 1.1,
        local["ease_of_reply"] * 1.0,
        local["naturalness"] * 1.2,
        local.get("contact_fit", 7.0) * 0.9,
        contact_fit * 0.5,
        anti_cringe * 1.4,
        float(judged.get("brevity", local["brevity"])) * 0.5,
        float(judged.get("low_investment", local["low_investment"])) * 0.5,
        float(judged.get("overall", 5.0)) * 0.8,
    ]
    return round(sum(parts) / len(parts), 3)


def score_reply(
    reply: str,
    history: ConversationHistory,
    *,
    service: Optional[NanoGptService] = None,
) -> Dict[str, Any]:
    local = heuristic_scores(reply, history)
    try:
        judged = model_scores(reply, history, service=service)
    except Exception as exception:
        judged = {
            "overall": local["low_investment"],
            "anti_cringe": max(0.0, 10.0 - local["cringe_penalty"]),
            "reason": f"model score failed: {exception}",
        }
    total = aggregate_score(local, judged)
    # Hard floor for banned-soft / AI-ish replies so they almost never win.
    if local["low_investment"] <= 3.0:
        total = min(total, 6.0)
    if local.get("em_dash_count", 0) > 0:
        total = min(total, 5.5)
    if local.get("ai_trope_hits", 0) >= 2 or local.get("polite_hits", 0) >= 2:
        total = min(total, 6.0)
    return {
        "reply": reply,
        "total": total,
        "heuristic": local,
        "model": judged,
    }


def pick_best(
    scored: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if not scored:
        raise ValueError("No scored replies to pick from.")
    return max(scored, key=lambda item: item["total"])
