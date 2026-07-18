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


def contact_stage(history: ConversationHistory) -> Tuple[str, str]:
    """
    Return (stage, guidance).
    stages: too_early | maybe | good | already_done
    """
    if history.is_new_match or not history.messages:
        return "too_early", "Do NOT ask for Instagram or WhatsApp yet."

    if contact_already_exchanged(history):
        return "already_done", "Contact already mentioned — do not ask again."

    turns = len(history.messages)
    their_msgs = [m for m in history.messages if m.sender.lower() != "you"]
    you_msgs = [m for m in history.messages if m.sender.lower() == "you"]
    planish = plan_is_stalled(history) or any(
        re.search(
            r"\b(drink|coffee|pint|tonight|tomorrow|meet|free)\b",
            m.text,
            re.I,
        )
        for m in history.messages
    )

    if turns < 4 or len(their_msgs) < 2:
        return "too_early", "Do NOT ask for Instagram or WhatsApp yet."

    if planish and len(you_msgs) >= 2 and len(their_msgs) >= 2:
        return (
            "good",
            "Prefer lightly steering to Instagram or WhatsApp in this reply "
            "(e.g. 'easier on whatsapp' / 'whats your ig') if it fits in one "
            "short line after answering them. Keep it casual, not pushy.",
        )

    if turns >= 8 and len(their_msgs) >= 3:
        return (
            "maybe",
            "Good window to float IG/WhatsApp if it feels natural after "
            "answering them; otherwise skip contact and keep the chat moving.",
        )

    return "too_early", "Do NOT ask for Instagram or WhatsApp yet."
