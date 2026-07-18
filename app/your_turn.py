# app/your_turn.py
"""List Hinge Your Turn chats and collect full conversation history."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import List, Optional, Set, Tuple

from ui_dump import (
    Bounds,
    MESSAGE_DESC_RE,
    bounds_center,
    composer_draft_texts,
    dump_ui_xml,
    find_nodes,
    is_composer_draft_text,
    is_composer_node,
    open_matches,
    parse_ui_nodes,
    press_back,
    swipe,
    tap_bounds,
)


@dataclass
class ConversationPreview:
    name: str
    preview: str
    bounds: Bounds
    is_new_match: bool = False
    section: str = "unknown"  # your_turn | their_turn | hidden | unknown


@dataclass
class ChatMessage:
    sender: str  # "You" or their name
    text: str
    timestamp: Optional[str] = None


@dataclass
class ConversationHistory:
    name: str
    messages: List[ChatMessage] = field(default_factory=list)
    is_new_match: bool = False

    def as_transcript(self) -> str:
        if not self.messages:
            return "(No messages yet - this is a new match.)"
        lines = []
        for message in self.messages:
            stamp = f" [{message.timestamp}]" if message.timestamp else ""
            lines.append(f"{message.sender}{stamp}: {message.text}")
        return "\n".join(lines)


def your_turn_count(device) -> Optional[int]:
    """Parse 'Your turn (N)' from the Matches list, if present."""
    nodes = parse_ui_nodes(dump_ui_xml(device))
    for node in nodes:
        for text in [node.text, *node.children_text]:
            match = re.search(r"your turn\s*\((\d+)\)", text, re.IGNORECASE)
            if match:
                return int(match.group(1))
    return None


def _section_y_range(
    nodes,
    *,
    start_label: str,
    end_labels: Tuple[str, ...],
) -> Optional[Tuple[int, Optional[int]]]:
    """
    Return (start_y, end_y) for a Matches list section.
    end_y is None when the end header is not on screen (section continues below).
    """
    start_y: Optional[int] = None
    end_y: Optional[int] = None
    for node in nodes:
        text = (node.text or "").strip().lower()
        if not text:
            continue
        if start_y is None and text.startswith(start_label.lower()):
            start_y = node.bounds[1]
            continue
        if start_y is not None and end_y is None:
            if any(text.startswith(label.lower()) for label in end_labels):
                end_y = node.bounds[1]
    if start_y is None:
        return None
    return start_y, end_y


def _section_headers(nodes) -> List[Tuple[int, str]]:
    """Return [(y, section_key), ...] for Matches list headers on screen."""
    headers: List[Tuple[int, str]] = []
    for node in nodes:
        text = (node.text or "").strip().lower()
        if not text:
            continue
        if text.startswith("your turn"):
            headers.append((node.bounds[1], "your_turn"))
        elif text.startswith("their turn"):
            headers.append((node.bounds[1], "their_turn"))
        elif text.startswith("hidden"):
            headers.append((node.bounds[1], "hidden"))
    headers.sort(key=lambda item: item[0])
    return headers


def _section_for_row(headers: List[Tuple[int, str]], row_y: int) -> str:
    section = "unknown"
    for header_y, key in headers:
        if row_y >= header_y:
            section = key
        else:
            break
    return section


def list_match_conversations(
    device,
    *,
    skip_new_matches: bool = False,
    only_your_turn: bool = False,
) -> List[ConversationPreview]:
    """List visible Matches-tab conversations (Your Turn and/or beyond)."""
    from ui_dump import in_match_conversation, is_hinge_xml

    xml_text = dump_ui_xml(device)
    if not is_hinge_xml(xml_text):
        print("Matches list skipped: not in Hinge")
        return []
    nodes = parse_ui_nodes(xml_text)
    if in_match_conversation(nodes):
        print("Matches list skipped: inside an open chat/profile")
        return []
    headers = _section_headers(nodes)
    your_turn_range = None
    if only_your_turn:
        your_turn_range = _section_y_range(
            nodes,
            start_label="your turn",
            end_labels=("their turn", "hidden", "hidden matches"),
        )
    conversations: List[ConversationPreview] = []

    for node in nodes:
        if not node.clickable:
            continue
        texts = [t for t in node.children_text if t]
        if not texts:
            continue
        # Skip section headers / banners.
        joined = " ".join(texts).lower()
        if "your turn" in joined or "their turn" in joined or "over the limit" in joined:
            continue
        if any(
            nav in (node.content_desc or "").lower()
            for nav in ("discover", "standouts", "likes you", "matches")
        ):
            continue

        name = texts[0].strip()
        skip_names = {
            "matches",
            "start chat",
            "profile",
            "chat",
            "send a message",
            "local",
            "hinge",
            "more",
            "back",
            "general",
            "web",
            "app",
            "settings",
            "google tv",
            "search",
            "gt",
            "sent",
            "delivered",
            "read",
            "liked",
            "active",
            "online",
            "new",
            "today",
            "yesterday",
            "hidden",
            "hidden matches",
            "their turn",
            "your turn",
        }
        if not name or name.lower() in skip_names:
            continue
        # Real match names are short; reject UI crumbs / drafts / "GT" spam.
        if len(name) < 2 or len(name) > 48:
            continue
        if name.lower().startswith("gt ") or name.upper() == "GT":
            continue
        if not re.search(r"[A-Za-z]", name):
            continue
        # Composer / in-chat chrome sometimes appears if we aren't on Matches.
        if is_composer_node(node):
            continue
        if is_composer_draft_text(name) or "send a message" in joined:
            continue
        # Unsent draft text often looks like a full sentence, not a first name.
        if len(name.split()) >= 5 and ("?" in name or "." in name or "," in name):
            continue
        # Banner / empty-state rows sometimes look clickable.
        if "waiting for your reply" in joined or "end chats" in joined:
            continue
        # System Settings / Honor search rows (wrong screen).
        if any(
            crumb in joined
            for crumb in ("google tv", "media|ringtone", "volume button", "settings")
        ):
            continue

        row_y = node.bounds[1]
        section = _section_for_row(headers, row_y)
        if only_your_turn:
            if your_turn_range is None:
                # Header scrolled off — keep rows that still look like Your Turn.
                if "reply?" not in joined and "start the chat" not in joined:
                    continue
                section = "your_turn"
            else:
                start_y, end_y = your_turn_range
                if row_y < start_y:
                    continue
                if end_y is not None and row_y >= end_y:
                    continue
                section = "your_turn"

        preview = texts[1].strip() if len(texts) > 1 else ""
        is_new = any("start the chat" in t.lower() for t in texts) or any(
            t.lower() == "start chat" for t in texts
        )
        if is_new and section == "unknown":
            section = "your_turn"
        if skip_new_matches and is_new:
            continue
        conversations.append(
            ConversationPreview(
                name=name,
                preview=preview,
                bounds=node.bounds,
                is_new_match=is_new,
                section=section,
            )
        )

    # Deduplicate by name keeping first (topmost) occurrence.
    seen: Set[str] = set()
    unique: List[ConversationPreview] = []
    for conversation in conversations:
        key = conversation.name.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(conversation)
    return unique


def list_your_turn_conversations(device) -> List[ConversationPreview]:
    """List only conversations under the Your Turn section."""
    return list_match_conversations(device, skip_new_matches=False, only_your_turn=True)


def _message_key(sender: str, text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip().lower()
    # Hinge appends "You liked this message." — keep it for identity.
    return f"{sender.lower()}::{normalized}"


def _parse_messages_from_nodes(nodes) -> Tuple[List[ChatMessage], List[str]]:
    """Return messages (top-to-bottom) and timestamp labels aligned by Y order."""
    drafts = composer_draft_texts(nodes)
    timed_nodes = []
    for node in nodes:
        # Never treat the unsent composer EditText / placeholder as a bubble.
        if is_composer_node(node):
            continue

        if re.match(
            r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun|Yesterday|Today)\b",
            node.text,
        ) or re.match(r"^\d", node.text):
            if node.text and not node.content_desc:
                timed_nodes.append(("time", node.bounds[1], node.text))
                continue

        desc = (node.content_desc or "").strip()
        # Profile-tab / like-prompt chrome is not a chat bubble.
        if desc.lower().startswith("prompt:"):
            continue
        if re.search(r"['’]s photo\s*$", desc, re.I):
            continue
        if desc.lower() in {
            "age",
            "gender",
            "sexuality",
            "job",
            "height",
            "location",
            "education",
            "school",
            "languages spoken",
            "relationship type",
            "verified",
            "back",
            "more",
        }:
            continue

        match = MESSAGE_DESC_RE.match(desc)
        if match:
            sender = match.group(1).strip()
            text = match.group(2).strip()
            if sender.lower() in {"prompt", "chat", "profile"}:
                continue
            text = re.sub(
                r"\s*You liked this message\.?\s*$",
                "",
                text,
                flags=re.IGNORECASE,
            ).strip()
            if text and not is_composer_draft_text(text, drafts):
                timed_nodes.append(("msg", node.bounds[1], sender, text))
            continue

        # Profile like / prompt like rows show as plain text, not message descs.
        plain = (node.text or "").strip()
        if plain and is_composer_draft_text(plain, drafts):
            continue
        if plain and re.match(
            r"^You liked .+\.?$",
            plain,
            re.IGNORECASE,
        ):
            timed_nodes.append(("msg", node.bounds[1], "You", plain))

    timed_nodes.sort(key=lambda item: item[1])

    messages: List[ChatMessage] = []
    last_timestamp: Optional[str] = None
    for item in timed_nodes:
        if item[0] == "time":
            last_timestamp = item[2]
            continue
        _, _, sender, text = item
        messages.append(
            ChatMessage(sender=sender, text=text, timestamp=last_timestamp)
        )
    return messages, []


def collect_chat_history(
    device,
    width: int,
    height: int,
    name: str,
    *,
    max_scrolls: int = 8,
    stagnant_limit: int = 2,
    settle_bottom: bool = True,
    scroll_pause_s: float = 0.65,
) -> ConversationHistory:
    """Scroll upward through a chat and reconstruct oldest→newest history."""
    ordered: List[ChatMessage] = []
    seen: Set[str] = set()
    stagnant = 0

    def ingest_screen() -> int:
        nonlocal ordered
        nodes = parse_ui_nodes(dump_ui_xml(device))
        on_screen, _ = _parse_messages_from_nodes(nodes)
        new_messages = []
        for message in on_screen:
            key = _message_key(message.sender, message.text)
            if key in seen:
                continue
            seen.add(key)
            new_messages.append(message)
        if not new_messages:
            return 0
        # When scrolling up, newly revealed messages are older → prepend.
        ordered = new_messages + ordered
        return len(new_messages)

    # Current viewport first (newer messages near bottom).
    ingest_screen()

    for _ in range(max_scrolls):
        # Finger moves down → older history appears at the top.
        swipe(
            device,
            width // 2,
            int(height * 0.32),
            width // 2,
            int(height * 0.78),
            280,
        )
        time.sleep(max(0.35, float(scroll_pause_s)))
        added = ingest_screen()
        if added == 0:
            stagnant += 1
            if stagnant >= stagnant_limit:
                break
        else:
            stagnant = 0

    # Scroll back to the latest messages so the composer is usable.
    # Sync often opens Profile next — skip this when settle_bottom=False.
    if settle_bottom:
        for _ in range(2):
            swipe(
                device,
                width // 2,
                int(height * 0.75),
                width // 2,
                int(height * 0.35),
                250,
            )
            time.sleep(0.35)

    return ConversationHistory(name=name, messages=ordered)


def open_conversation(
    device,
    conversation: ConversationPreview,
    *,
    settle_s: float = 1.2,
) -> None:
    tap_bounds(device, conversation.bounds)
    time.sleep(max(0.4, float(settle_s)))


def conversation_open_for_match(device, match_name: str) -> bool:
    """
    True when the open screen looks like this match's chat/profile
    (header name + chat chrome), not the Matches list or another thread.
    """
    from ui_dump import is_hinge_xml

    xml_text = dump_ui_xml(device)
    if not is_hinge_xml(xml_text):
        return False
    nodes = parse_ui_nodes(xml_text)
    want = (match_name or "").strip().lower()
    if not want:
        return False

    has_composer = any(is_composer_node(node) for node in nodes)
    has_chat_tab = any(
        (node.text or "").strip().lower() == "chat" and node.bounds[1] < 900
        for node in nodes
    )
    has_profile_tab = any(
        (node.text or "").strip().lower() == "profile" and node.bounds[1] < 900
        for node in nodes
    )
    if not (has_composer or (has_chat_tab and has_profile_tab)):
        return False

    # Name usually appears as header text or "Name, verified" content-desc.
    for node in nodes:
        text = (node.text or "").strip().lower()
        desc = (node.content_desc or "").strip().lower()
        if text == want or desc == want:
            return True
        if desc.startswith(want + ",") or desc.startswith(want + " "):
            return True
        if text.startswith(want + ",") or text.startswith(want + " "):
            return True
    return False


def _composer_text(device) -> str:
    nodes = parse_ui_nodes(dump_ui_xml(device))
    composers = find_nodes(
        nodes,
        resource_id="co.hinge.app:id/messageComposition",
    )
    if not composers:
        return ""
    text = composers[0].text or ""
    # Placeholder means empty.
    if text.strip().lower() in {"send a message", ""}:
        return ""
    return text


def _clear_composer(device, bounds: Bounds) -> None:
    """Select-all then delete so prior drafts don't linger."""
    tap_bounds(device, bounds)
    time.sleep(0.2)

    # Ctrl+A (KEYCODE_CTRL_LEFT=113, KEYCODE_A=29), then DEL (67).
    device.shell("input keycombination 113 29")
    time.sleep(0.12)
    device.shell("input keyevent 67")
    time.sleep(0.12)

    if _composer_text(device):
        # Fallback: jump to end and backspace leftovers.
        device.shell("input keyevent 123")  # MOVE_END
        del_events = " ".join(["67"] * 80)
        device.shell(f"input keyevent {del_events}")
        time.sleep(0.15)


def _normalize_for_adb(text: str) -> str:
    """ASCII-normalize draft text so adb typing is reliable."""
    if not text:
        return ""
    replacements = {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2026": "...",
        "\xa0": " ",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    text = text.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", text).strip()


def _adb_type_text(device, text: str) -> None:
    """Type ASCII text via adb input. Spaces become %s."""
    safe_chars = []
    for char in text:
        if char == "\n":
            if safe_chars:
                _adb_flush_chunk(device, "".join(safe_chars))
                safe_chars = []
            device.shell("input keyevent 66")
            continue
        if ord(char) >= 128:
            continue
        if char.isalnum() or char in " .,!?':;-/@#_":
            safe_chars.append(char)
        elif char == '"':
            continue
        else:
            if safe_chars:
                _adb_flush_chunk(device, "".join(safe_chars))
                safe_chars = []
    if safe_chars:
        _adb_flush_chunk(device, "".join(safe_chars))


def _adb_flush_chunk(device, chunk: str) -> None:
    if not chunk:
        return
    step = 60
    for index in range(0, len(chunk), step):
        part = chunk[index : index + step]
        escaped = (
            part.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("'", "\\'")
            .replace(" ", "%s")
            .replace("&", "\\&")
            .replace("<", "\\<")
            .replace(">", "\\>")
            .replace("|", "\\|")
            .replace(";", "\\;")
            .replace("(", "\\(")
            .replace(")", "\\)")
        )
        device.shell(f'input text "{escaped}"')
        time.sleep(0.05)


def focus_composer_and_type(device, text: str) -> bool:
    """Paste a draft into the Hinge composer without sending."""
    text = _normalize_for_adb(text)
    if not text:
        return False

    nodes = parse_ui_nodes(dump_ui_xml(device))
    composers = find_nodes(
        nodes,
        resource_id="co.hinge.app:id/messageComposition",
    )
    if not composers:
        composers = [
            node
            for node in find_nodes(nodes, text_contains="Send a message")
            if node.class_name == "EditText" or node.editable
        ]
    if not composers:
        return False

    _clear_composer(device, composers[0].bounds)
    tap_bounds(device, composers[0].bounds)
    time.sleep(0.15)
    _adb_type_text(device, text)
    time.sleep(0.25)
    return True


def ensure_matches_your_turn(device, width: int, height: int) -> None:
    open_matches(device, width, height)
    nodes = parse_ui_nodes(dump_ui_xml(device))
    headers = find_nodes(nodes, text_contains="Your turn")
    if not headers:
        # Try scrolling the matches list to the top.
        swipe(
            device,
            width // 2,
            int(height * 0.35),
            width // 2,
            int(height * 0.8),
            300,
        )
        time.sleep(1)
