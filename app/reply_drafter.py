# app/reply_drafter.py
"""Draft Hinge replies from full conversation history via NanoGPT."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional, Sequence

from conversation_goals import contact_stage, plan_is_stalled
from db import get_match_by_name, load_match_messages, load_profile_fields, load_style_profile
from nanogpt_service import NanoGptService
from reply_scorer import pick_best, score_reply
from style_learner import style_prompt_block
from your_turn import ChatMessage, ConversationHistory

# Principles distilled from men's dating / Hinge texting advice:
# short > impressive, mirror energy, answer-and-add, never needy.
SYSTEM_PROMPT = """You draft Hinge texts for the user ("You") like a calm guy who texts less, not a pickup artist.

Less is more. Sound human. Sound unbothered. Not like ChatGPT.

Hard rules:
- Reply to WHAT THEY ACTUALLY SAID in this thread. Quote or echo a concrete detail from their last message when you can.
- Usually ONE short sentence. Two only if you must answer + add. Never 3+.
- Target ~5-14 words. Slightly shorter than their last message when possible.
- Answer what they said (if needed), then ONE light add: observation, tease, or easy ask about THEIR topic.
- Do NOT jump to a meet-up / area / time unless THIS conversation already has a plan brewing, or they clearly opened that door.
- Never reuse a generic "Marylebone tonight?" style line. Do not invent areas, venues, or plans that are not already in THIS transcript.
- If a plan is already live / stalled IN THIS THREAD: be direct and specific (day / area / time). No "sometime".
- If they apologize for a late reply: do NOT say "no worries", "all good", or apologize back. Continue the topic or the existing plan.
- Do NOT invent venues, "spots you know", or facts about the user's life that aren't in the transcript or profile notes.
- No try-hard suave: no "cozy little spot", "perfect for", "I'd love to", "what do you say?", "bet I can".
- No AI/polite filler: no "absolutely", "looking forward", "hope you're well", "feel free", "just wanted to", "let me know your thoughts".
- No interview mode, no essay, no stacked compliments, max one "!" total, emoji only if they used one.
- Never use em dashes. Plain ASCII only (comma, period, or hyphen).
- Soft goal: after ~5-6 messages with real rapport, you may lightly ask for Instagram (WhatsApp ok if more natural). Never on openers / cold threads. Never force it.
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
    r"\b(whatsapp|instagram|insta|ig).{0,40}\b(whatsapp|instagram|insta|ig)\b",
    r"\blooking forward\b",
    r"\bhope you'?re well\b",
    r"\bfeel free\b",
    r"\bjust wanted to\b",
    r"\blet me know your thoughts\b",
]

GENERIC_PLAN_RE = re.compile(
    r"\b(marylebone|soho|shoreditch|mayfair|tonight|tomorrow|"
    r"grab a drink|drink tonight|this week)\b",
    re.I,
)


def _their_last_len(history: ConversationHistory) -> int:
    for message in reversed(history.messages):
        if message.sender.lower() != "you":
            return len(message.text.split())
    return 10


def _their_last_text(history: ConversationHistory) -> str:
    for message in reversed(history.messages):
        if message.sender.lower() != "you":
            return (message.text or "").strip()
    return ""


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


def _message_key(sender: str, text: str) -> str:
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
    normalized = re.sub(
        r"\s*you liked this message\.?\s*$",
        "",
        normalized,
        flags=re.I,
    ).strip()
    return f"{sender.strip().lower()}::{normalized}"


def merge_history_with_db(history: ConversationHistory) -> ConversationHistory:
    """
    Prefer the richer of live scrape vs SQLite for this match.
    Keeps live order when both exist; fills gaps from DB.
    """
    match = get_match_by_name(history.name)
    if not match:
        return history
    rows = load_match_messages(int(match["id"]))
    if not rows:
        return history

    db_messages = [
        ChatMessage(
            sender=row["sender"],
            text=row["body"],
            timestamp=row.get("timestamp_label"),
        )
        for row in rows
        if (row.get("body") or "").strip()
    ]
    if not history.messages:
        return ConversationHistory(
            name=history.name,
            messages=db_messages,
            is_new_match=history.is_new_match and not db_messages,
        )

    live_keys = {_message_key(m.sender, m.text) for m in history.messages}
    merged: List[ChatMessage] = []
    # DB first (older), then any live-only tails.
    for message in db_messages:
        key = _message_key(message.sender, message.text)
        if key in {_message_key(m.sender, m.text) for m in merged}:
            continue
        merged.append(message)
    for message in history.messages:
        key = _message_key(message.sender, message.text)
        if key in {_message_key(m.sender, m.text) for m in merged}:
            continue
        # Skip pure like-chrome if DB already has real chat.
        if re.match(r"^you liked\b", (message.text or "").strip(), re.I) and len(merged) > 1:
            continue
        merged.append(message)
        live_keys.add(key)

    return ConversationHistory(
        name=history.name,
        messages=merged or history.messages,
        is_new_match=False if merged else history.is_new_match,
    )


def profile_context_for_match(match_name: str, *, limit: int = 10) -> str:
    match = get_match_by_name(match_name)
    if not match:
        return ""
    fields = load_profile_fields(int(match["id"]))
    if not fields:
        return ""
    lines = []
    for field in fields[:limit]:
        label = (field.get("label") or field.get("field_type") or "").strip()
        text = (field.get("text_content") or "").strip()
        if not text:
            continue
        if label:
            lines.append(f"- {label}: {text[:120]}")
        else:
            lines.append(f"- {text[:120]}")
    if not lines:
        return ""
    return "Profile notes (optional spice, do not invent beyond these):\n" + "\n".join(lines)


def _avoid_drafts_block(recent_drafts: Sequence[str]) -> str:
    cleaned = []
    for draft in recent_drafts:
        text = (draft or "").strip()
        if text and text not in cleaned:
            cleaned.append(text)
        if len(cleaned) >= 8:
            break
    if not cleaned:
        return ""
    bullets = "\n".join(f"- {item}" for item in cleaned)
    return (
        "Do NOT copy or lightly rephrase these recent drafts to other matches "
        "(avoid same plan/area lines):\n"
        f"{bullets}\n"
    )


def _user_prompt(
    history: ConversationHistory,
    extra_context: str = "",
    *,
    recent_drafts: Optional[Sequence[str]] = None,
) -> str:
    style_block = style_prompt_block(load_style_profile())
    profile_block = profile_context_for_match(history.name)
    avoid_block = _avoid_drafts_block(recent_drafts or [])
    their_last = _their_last_text(history)

    if history.is_new_match or not history.messages:
        return f"""New match: {history.name}. No history.
Write one short, specific opener (under 12 words). Curious, not charming-on-purpose.
Use a profile note if one stands out; otherwise a light open question.
Do NOT ask for Instagram/WhatsApp. Do NOT propose Marylebone/tonight drinks.
Plain ASCII only.
{style_block}
{profile_block}
{avoid_block}
{extra_context}
"""

    their_words = _their_last_len(history)
    stalled = plan_is_stalled(history)
    stage, contact_guidance = contact_stage(history)
    plan_already = bool(
        GENERIC_PLAN_RE.search(_clean_transcript(history))
        and any(
            m.sender.lower() == "you"
            and GENERIC_PLAN_RE.search(m.text or "")
            for m in history.messages
        )
    )
    situation = ""
    if stalled and plan_already:
        situation = (
            "Situation: a meet-up was already suggested IN THIS THREAD and stalled. "
            "Revive THAT plan in one short line with a concrete time/area already "
            "discussed. Do not invent a new neighborhood. Do not acknowledge the delay.\n"
        )
    elif not plan_already:
        situation = (
            "Situation: no live plan in this thread. Do NOT pitch a drink/area/time. "
            "Build on their last message instead.\n"
        )

    return f"""Match: {history.name}
Their last message (~{their_words} words): {their_last!r}
You MUST respond to that content specifically.
Goal: one chill next text that continues THIS conversation.
Contact stage: {stage}. {contact_guidance}
{situation}
{style_block}
{profile_block}
{avoid_block}
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
    n: int = 2,
    service: Optional[NanoGptService] = None,
    extra_context: str = "",
    recent_drafts: Optional[Sequence[str]] = None,
) -> List[str]:
    service = service or NanoGptService()
    prompt = _user_prompt(history, extra_context, recent_drafts=recent_drafts)
    candidates: List[str] = []
    temperatures = [0.55, 0.85, 0.7][: max(1, n)]
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
                        + "\nRewrite without soft/apologetic filler or try-hard lines. "
                        "Answer their last message directly.",
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
    recent_drafts: Optional[Sequence[str]] = None,
) -> str:
    """Backward-compatible single draft (no scoring)."""
    history = merge_history_with_db(history)
    candidates = generate_candidates(
        history,
        n=1,
        service=service,
        extra_context=extra_context,
        recent_drafts=recent_drafts,
    )
    return candidates[0] if candidates else ""


def draft_scored_reply(
    history: ConversationHistory,
    *,
    n_candidates: int = 2,
    service: Optional[NanoGptService] = None,
    extra_context: str = "",
    recent_drafts: Optional[Sequence[str]] = None,
    use_model_judge: bool = False,
) -> Dict[str, Any]:
    """
    Generate several replies, score them on dating-advice metrics, return the best.

    use_model_judge=False (default): heuristic scoring only — much faster.
    """
    service = service or NanoGptService()
    history = merge_history_with_db(history)
    candidates = generate_candidates(
        history,
        n=n_candidates,
        service=service,
        extra_context=extra_context,
        recent_drafts=recent_drafts,
    )
    if not candidates:
        raise ValueError("No draft candidates generated.")
    scored = [
        score_reply(
            candidate,
            history,
            service=service,
            use_model_judge=use_model_judge,
            recent_drafts=recent_drafts,
        )
        for candidate in candidates
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
