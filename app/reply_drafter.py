# app/reply_drafter.py
"""Draft Hinge replies from full conversation history via NanoGPT."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional

from conversation_goals import contact_stage, plan_is_stalled
from db import load_style_profile
from nanogpt_service import NanoGptService
from reply_scorer import pick_best, score_reply
from style_learner import style_prompt_block
from your_turn import ConversationHistory

# Principles distilled from men's dating / Hinge texting advice:
# short > impressive, mirror energy, answer-and-add, never needy.
SYSTEM_PROMPT = """You draft Hinge texts for the user ("You") like a calm guy who texts less, not a pickup artist.

Less is more. Sound human. Sound unbothered. Not like ChatGPT.

Hard rules:
- Usually ONE short sentence. Two only if you must answer + add. Never 3+.
- Target ~5-14 words. Slightly shorter than their last message when possible.
- Answer what they said (if needed), then ONE light add: observation, tease, or easy ask.
- If a plan is live / stalled: be direct and specific (day / area / time). No "sometime".
- If they apologize for a late reply: do NOT say "no worries", "all good", or apologize back. Just move the plan forward.
- Do NOT invent venues, "spots you know", or facts about the user's life that aren't in the transcript.
- No try-hard suave: no "cozy little spot", "perfect for", "I'd love to", "what do you say?", "bet I can".
- No AI/polite filler: no "absolutely", "looking forward", "hope you're well", "feel free", "just wanted to", "let me know your thoughts".
- No interview mode, no essay, no stacked compliments, max one "!" total, emoji only if they used one.
- Never use em dashes. Plain ASCII only (comma, period, or hyphen).
- Soft goal: when rapport is solid or a plan is forming, you may naturally steer toward Instagram or WhatsApp. Never pushy, never on early/new chats, never every message.
- Return only the reply text."""

BANNED_SNIPPETS = [
    r"\bno worries\b",
    r"\ball good\b",
    r"\bi'?d love to\b",
    r"\bwhat do you say\b",
    r"\bcozy little\b",
    r"\bperfect for\b",
    r"\bbet i can\b",
    r"\bcan'?t wait\b",
    r"\bwe should (hang|meet) sometime\b",
    r"\b(whatsapp|instagram|insta|\big\b).{0,40}\b(whatsapp|instagram|insta|\big\b)\b",
    r"\blooking forward\b",
    r"\bhope you'?re well\b",
    r"\bfeel free\b",
    r"\bjust wanted to\b",
    r"\blet me know your thoughts\b",
]


def _their_last_len(history: ConversationHistory) -> int:
    for message in reversed(history.messages):
        if message.sender.lower() != "you":
            return len(message.text.split())
    return 10


def _clean_transcript(history: ConversationHistory) -> str:
    """Transcript for the model: drop Hinge 'liked' chrome."""
    if not history.messages:
        return "(No messages yet - this is a new match.)"
    lines = []
    for message in history.messages:
        text = re.sub(
            r"\s*You liked this message\.?\s*$",
            "",
            message.text,
            flags=re.IGNORECASE,
        ).strip()
        if not text:
            continue
        stamp = f" [{message.timestamp}]" if message.timestamp else ""
        lines.append(f"{message.sender}{stamp}: {text}")
    return "\n".join(lines) if lines else "(No messages yet - this is a new match.)"


def _user_prompt(history: ConversationHistory, extra_context: str = "") -> str:
    style_block = style_prompt_block(load_style_profile())
    if history.is_new_match or not history.messages:
        return f"""New match: {history.name}. No history.
Write one short, specific opener (under 12 words). Curious, not charming-on-purpose.
Do NOT ask for Instagram/WhatsApp.
Plain ASCII only.
{style_block}
{extra_context}
"""

    their_words = _their_last_len(history)
    stalled = plan_is_stalled(history)
    stage, contact_guidance = contact_stage(history)
    situation = ""
    if stalled:
        situation = (
            "Situation: you already suggested meeting; they delayed or soft-replied. "
            "Revive the plan in one short line with a concrete time/area. "
            "Do not acknowledge the delay.\n"
        )

    return f"""Match: {history.name}
Their last message was ~{their_words} words - stay at or under that.
Goal: one chill next text. Advance slightly; don't perform.
Contact stage: {stage}. {contact_guidance}
{situation}
{style_block}
Conversation (oldest → newest):
{_clean_transcript(history)}

Write the next reply from You only. Plain ASCII. No quotes around the reply.
{extra_context}
"""


def strip_em_dashes(text: str) -> str:
    """Replace em/en dashes with natural ASCII (comma / hyphen), never leave —."""
    if not text:
        return ""
    # Spaced dash often joins clauses -> comma.
    text = re.sub(r"\s*[—–―]\s*", ", ", text)
    # Any leftover dash glyphs (including HTML entities if present).
    text = text.replace("&mdash;", ", ").replace("&ndash;", "-")
    text = re.sub(r"\s*,\s*,+", ", ", text)
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r",\s*\.", ".", text)
    return text


def normalize_reply(text: str) -> str:
    """Make model output paste-safe and consistent. Always strips em dashes."""
    if not text:
        return ""
    text = text.strip().strip('"').strip("'").strip("`")
    text = re.sub(r"^(you)\s*:\s*", "", text, flags=re.IGNORECASE)
    text = strip_em_dashes(text)
    replacements = {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2026": "...",
        "\xa0": " ",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    # Defensive: if any dash glyphs remain, force ASCII hyphen with spaces.
    text = re.sub(r"[—–―]", " - ", text)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.!?])", r"\1", text)
    return text


def _is_banned(text: str) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in BANNED_SNIPPETS)


def generate_candidates(
    history: ConversationHistory,
    *,
    n: int = 3,
    service: Optional[NanoGptService] = None,
    extra_context: str = "",
) -> List[str]:
    service = service or NanoGptService()
    prompt = _user_prompt(history, extra_context)
    candidates: List[str] = []
    temperatures = [0.5, 0.7, 0.9][: max(1, n)]
    while len(temperatures) < n:
        temperatures.append(0.65)

    attempts = 0
    max_attempts = max(n + 2, n * 2)
    while len(candidates) < n and attempts < max_attempts:
        temperature = temperatures[min(attempts, len(temperatures) - 1)]
        attempts += 1
        raw = service.chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=temperature,
            max_tokens=50,
        )
        text = normalize_reply(raw)
        if not text or text in candidates:
            continue
        if _is_banned(text):
            raw = service.chat(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": prompt
                        + "\nRewrite without soft/apologetic filler or try-hard lines.",
                    },
                ],
                temperature=0.45,
                max_tokens=50,
            )
            text = normalize_reply(raw)
            if not text or text in candidates or _is_banned(text):
                continue
        candidates.append(text)
    return candidates


def draft_reply(
    history: ConversationHistory,
    *,
    service: Optional[NanoGptService] = None,
    extra_context: str = "",
) -> str:
    """Backward-compatible single draft (no scoring)."""
    candidates = generate_candidates(
        history, n=1, service=service, extra_context=extra_context
    )
    return candidates[0] if candidates else ""


def draft_scored_reply(
    history: ConversationHistory,
    *,
    n_candidates: int = 3,
    service: Optional[NanoGptService] = None,
    extra_context: str = "",
) -> Dict[str, Any]:
    """
    Generate several replies, score them on dating-advice metrics, return the best.
    """
    service = service or NanoGptService()
    candidates = generate_candidates(
        history,
        n=n_candidates,
        service=service,
        extra_context=extra_context,
    )
    if not candidates:
        raise ValueError("No draft candidates generated.")
    scored = [
        score_reply(candidate, history, service=service) for candidate in candidates
    ]
    best = pick_best(scored)
    # Final safety pass: never save/paste em dashes or curly punctuation.
    cleaned = normalize_reply(best["reply"])
    best = {**best, "reply": cleaned}
    for candidate in scored:
        candidate["reply"] = normalize_reply(candidate["reply"])
    return {
        "reply": cleaned,
        "score": best,
        "candidates": scored,
        "contact_stage": contact_stage(history)[0],
    }
