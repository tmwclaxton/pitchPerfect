# app/conversation_goals.py
"""Conversation stage helpers: plans + Instagram/WhatsApp steering."""

from __future__ import annotations

import re
from typing import Tuple

from your_turn import ConversationHistory


def plan_is_stalled(history: ConversationHistory) -> bool:
    """True when You floated a meet-up and their latest message doesn't confirm."""
    you_plan = False
    for message in history.messages:
        if message.sender.lower() != "you":
            continue
        if re.search(
            r"\b(drink|coffee|pint|tonight|tomorrow|free\b|grab a|meet|hang)\b",
            message.text,
            re.IGNORECASE,
        ):
            you_plan = True
    if not you_plan:
        return False
    for message in reversed(history.messages):
        if message.sender.lower() == "you":
            continue
        if re.search(
            r"\b(sorry|late|busy|hectic|swamped|this week|next week)\b",
            message.text,
            re.IGNORECASE,
        ):
            return True
        if len(message.text.split()) <= 4 and "?" not in message.text:
            return True
        return False
    return False


def contact_already_exchanged(history: ConversationHistory) -> bool:
    blob = " ".join(message.text for message in history.messages).lower()
    return bool(
        re.search(
            r"\b(instagram|insta|ig|whatsapp|whats ?app|@\w+|0\d{10}|\+44)\b",
            blob,
        )
    )


def _real_message_count(history: ConversationHistory) -> Tuple[int, int, int]:
    """Count non-chrome messages: total, theirs, yours."""
    their = 0
    yours = 0
    for message in history.messages:
        text = (message.text or "").strip()
        if not text:
            continue
        if re.match(r"^you liked\b", text, re.I):
            continue
        if message.sender.lower() == "you":
            yours += 1
        else:
            their += 1
    return their + yours, their, yours


def contact_stage(history: ConversationHistory) -> Tuple[str, str]:
    """
    Return (stage, guidance).
    stages: too_early | maybe | good | already_done

    Prefer IG after ~5-6 real messages once there is rapport. WhatsApp is fine
    as a natural alternative. Never force contact on cold / opener threads.
    """
    if history.is_new_match or not history.messages:
        return "too_early", "Do NOT ask for Instagram or WhatsApp yet."

    if contact_already_exchanged(history):
        return "already_done", "Contact already mentioned — do not ask again."

    turns, their_msgs, you_msgs = _real_message_count(history)
    # Need a real back-and-forth before contact (~5-6 messages in thread).
    if turns < 5 or their_msgs < 2 or you_msgs < 2:
        return "too_early", "Do NOT ask for Instagram or WhatsApp yet."

    rapport = their_msgs >= 2 and you_msgs >= 2 and turns >= 5
    planish = plan_is_stalled(history) or any(
        re.search(
            r"\b(drink|coffee|pint|tonight|tomorrow|meet|free)\b",
            m.text,
            re.I,
        )
        for m in history.messages
        if m.sender.lower() == "you"
        and not re.match(r"^you liked\b", (m.text or "").strip(), re.I)
    )

    if rapport and turns >= 6 and (planish or their_msgs >= 3):
        return (
            "good",
            "Rapport exists (~6+ messages). Prefer lightly asking for Instagram "
            "if it fits after answering them (e.g. 'whats your ig'). WhatsApp "
            "is fine as a natural alternative. One short clause max, not pushy. "
            "If the thread is still about something specific they said, answer "
            "that first and only add contact if it feels natural.",
        )

    if rapport and turns >= 5:
        return (
            "maybe",
            "Thread has some rapport. You may float Instagram (or WhatsApp) "
            "only if it feels natural after answering them; otherwise skip "
            "contact and keep building on what they said.",
        )

    return "too_early", "Do NOT ask for Instagram or WhatsApp yet."
