# app/style_learner.py
"""Infer the user's Hinge texting style from collected chat histories."""

from __future__ import annotations

import re
import statistics
from typing import Any, Dict, List, Optional

from db import list_matches, load_match_messages, load_style_profile, save_style_profile
from nanogpt_service import NanoGptService
from your_turn import ChatMessage, ConversationHistory


def _is_junk_match_name(name: str) -> bool:
    """Reject chrome / composer-draft rows accidentally stored as matches."""
    cleaned = (name or "").strip()
    if not cleaned or len(cleaned) > 48:
        return True
    lowered = cleaned.lower()
    if lowered in {
        "profile",
        "chat",
        "local",
        "matches",
        "hinge",
        "search",
        "gt",
        "send a message",
    }:
        return True
    if lowered.startswith("gt "):
        return True
    # Unsent drafts / long sentences saved as "names".
    if len(cleaned.split()) >= 5 and any(ch in cleaned for ch in ".,?!"):
        return True
    return False


def histories_from_db(
    *,
    max_chats: int = 50,
    min_you_messages: int = 1,
) -> List[ConversationHistory]:
    """
    Load saved Matches chats from SQLite for offline style learning.
    Prefers chats where You sent at least min_you_messages.
    """
    histories: List[ConversationHistory] = []
    for match in list_matches(limit=max(max_chats * 3, 50)):
        if len(histories) >= max_chats:
            break
        name = (match.get("name") or "").strip()
        if _is_junk_match_name(name):
            continue
        rows = load_match_messages(int(match["id"]))
        if not rows:
            continue
        messages = [
            ChatMessage(
                sender=row["sender"],
                text=row["body"],
                timestamp=row.get("timestamp_label"),
            )
            for row in rows
            if (row.get("body") or "").strip()
        ]
        you_count = sum(1 for m in messages if m.sender.lower() == "you")
        if you_count < min_you_messages:
            continue
        histories.append(
            ConversationHistory(
                name=name,
                messages=messages,
                is_new_match=bool(match.get("is_new_match")),
            )
        )
    return histories

STYLE_SYSTEM = """You analyze a man's Hinge texts and describe his real texting style.
Be concrete and short. Return JSON only."""


def _you_messages(histories: List[ConversationHistory]) -> List[str]:
    texts: List[str] = []
    for history in histories:
        for message in history.messages:
            if message.sender.lower() == "you":
                text = message.text.strip()
                if text and not text.lower().startswith("you liked"):
                    texts.append(text)
    return texts


def heuristic_style(histories: List[ConversationHistory]) -> Dict[str, Any]:
    samples = _you_messages(histories)
    if not samples:
        return {
            "summary": "Not enough sent messages to learn a style yet.",
            "avg_words": 10,
            "emoji_rate": 0.0,
            "question_rate": 0.5,
            "exclamation_rate": 0.0,
            "uses_x": False,
            "uses_haha": False,
            "contact_examples": [],
            "plan_examples": [],
            "sample_lines": [],
        }

    word_counts = [len(re.findall(r"\S+", s)) for s in samples]
    emoji_re = re.compile(
        "["
        "\U0001F300-\U0001FAFF"
        "\U00002700-\U000027BF"
        "\U0001F600-\U0001F64F"
        "]+",
        flags=re.UNICODE,
    )
    emoji_hits = sum(1 for s in samples if emoji_re.search(s))
    question_hits = sum(1 for s in samples if "?" in s)
    bang_hits = sum(1 for s in samples if "!" in s)
    uses_x = any(re.search(r"\bx\b", s.lower()) for s in samples)
    uses_haha = any(re.search(r"\bha(ha)+\b|\blol\b", s.lower()) for s in samples)

    contact_examples = [
        s
        for s in samples
        if re.search(r"\b(instagram|insta|ig|whatsapp|whats ?app|number)\b", s, re.I)
    ][:5]
    plan_examples = [
        s
        for s in samples
        if re.search(r"\b(drink|coffee|pint|tonight|tomorrow|free|meet)\b", s, re.I)
    ][:5]

    # Prefer shorter representative lines.
    sample_lines = sorted(samples, key=len)[:8]
    if len(samples) > 8:
        sample_lines = samples[-8:]

    avg_words = round(statistics.mean(word_counts), 1)
    return {
        "summary": "",
        "avg_words": avg_words,
        "median_words": statistics.median(word_counts),
        "emoji_rate": round(emoji_hits / len(samples), 2),
        "question_rate": round(question_hits / len(samples), 2),
        "exclamation_rate": round(bang_hits / len(samples), 2),
        "uses_x": uses_x,
        "uses_haha": uses_haha,
        "contact_examples": contact_examples,
        "plan_examples": plan_examples,
        "sample_lines": sample_lines,
        "message_count": len(samples),
        "conversation_count": len(histories),
    }


def infer_style_profile(
    histories: List[ConversationHistory],
    *,
    service: Optional[NanoGptService] = None,
) -> Dict[str, Any]:
    local = heuristic_style(histories)
    samples = local.get("sample_lines") or []
    if not samples:
        save_style_profile(local, sample_count=0, conversations_used=len(histories))
        return local

    service = service or NanoGptService()
    prompt = f"""Here are messages the user ("You") sent on Hinge:

{chr(10).join(f'- {line}' for line in samples[:20])}

Heuristic stats:
- avg words: {local['avg_words']}
- emoji rate: {local['emoji_rate']}
- question rate: {local['question_rate']}
- uses trailing x: {local['uses_x']}
- uses haha/lol: {local['uses_haha']}
Plan examples: {local.get('plan_examples') or []}
Contact examples: {local.get('contact_examples') or []}

Return JSON keys:
- summary (2-4 sentences: tone, length, slang, punctuation, emoji, how they ask for plans/contact)
- tone_tags (array of short tags)
- length_guidance (one line)
- do (array of short habits to copy)
- dont (array of things they avoid)
- contact_style (how they ask for IG/WhatsApp, or "unknown")
- plan_style (how they suggest meeting, or "unknown")
"""
    try:
        judged = service.chat_json(
            [
                {"role": "system", "content": STYLE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=400,
        )
    except Exception as exception:
        judged = {"summary": f"Model style infer failed: {exception}", "tone_tags": []}

    profile = {**local, **{k: v for k, v in judged.items() if v is not None}}
    if not profile.get("summary"):
        profile["summary"] = (
            f"Usually ~{local['avg_words']} words; "
            f"{'uses x' if local['uses_x'] else 'no trailing x'}; "
            f"questions in ~{int(local['question_rate'] * 100)}% of texts."
        )

    save_style_profile(
        profile,
        sample_count=int(local.get("message_count") or 0),
        conversations_used=len(histories),
    )
    return profile


def style_prompt_block(profile: Optional[Dict[str, Any]] = None) -> str:
    profile = profile if profile is not None else load_style_profile()
    if not profile:
        return ""

    summary = profile.get("summary") or ""
    length = profile.get("length_guidance") or f"Aim around {profile.get('avg_words', 10)} words."
    do = profile.get("do") or []
    dont = profile.get("dont") or []
    contact = profile.get("contact_style") or "unknown"
    plan = profile.get("plan_style") or "unknown"
    samples = profile.get("sample_lines") or []
    sample_bit = ""
    if samples:
        sample_bit = "Example lines of yours:\n" + "\n".join(
            f"- {line}" for line in samples[:5]
        )

    return f"""Match the user's real texting style:
{summary}
Length: {length}
Do: {', '.join(do) if do else 'n/a'}
Don't: {', '.join(dont) if dont else 'n/a'}
Plan style: {plan}
Contact style: {contact}
{sample_bit}
"""


def messages_as_dicts(messages: List[ChatMessage]) -> List[Dict[str, Any]]:
    return [
        {
            "sender": message.sender,
            "text": message.text,
            "timestamp": message.timestamp,
        }
        for message in messages
    ]
