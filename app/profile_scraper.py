# app/profile_scraper.py
"""Scrape visible text from a Hinge match's Profile tab."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import List, Optional, Set, Tuple

from ui_dump import (
    composer_draft_texts,
    dump_ui_xml,
    find_nodes,
    is_composer_draft_text,
    is_composer_node,
    is_hinge_xml,
    parse_ui_nodes,
    swipe,
    tap_bounds,
)

PROMPT_RE = re.compile(
    r"^Prompt:\s*(.+?)\s+Answer:\s*(.+)\s*$",
    re.IGNORECASE | re.DOTALL,
)
PHOTO_RE = re.compile(r"^(.+?)['’]s photo\s*$", re.IGNORECASE)
# Honor Settings / Google TV accessibility spam: "GT GT GT ... Google TV"
GT_SPAM_RE = re.compile(r"^(?:GT\s*){2,}", re.IGNORECASE)

# Known Hinge "basics" / attribute labels (content-desc on the row).
BASIC_LABELS = {
    "age",
    "gender",
    "sexuality",
    "height",
    "location",
    "job",
    "education",
    "school",
    "languages spoken",
    "ethnicity",
    "religion",
    "politics",
    "drinking",
    "smoking",
    "cannabis",
    "drugs",
    "kids",
    "family plans",
    "pets",
    "relationship type",
    "looking for",
    "pronouns",
    "hometown",
    "astrological sign",
}

CHROME_TEXT = {
    "chat",
    "profile",
    "send a message",
    "back",
    "more",
    "matches",
    "discover",
    "standouts",
    "likes you",
    "record voice note",
    "send message",
    "verified",
    # Android / Honor Settings + system search (wrong-screen scrapes).
    "local",
    "general",
    "web",
    "app",
    "settings",
    "google tv",
    "default volume button control",
    "media|ringtone|",
}


@dataclass
class ProfileField:
    field_type: str  # prompt | basic | header | photo | caption | other
    label: Optional[str]
    text_content: str
    raw: Optional[dict] = None


def open_profile_tab(device) -> bool:
    """Switch from Chat to Profile inside an open match. Returns True if tapped."""
    nodes = parse_ui_nodes(dump_ui_xml(device))
    # Prefer exact "Profile" tab label near the top of the chat header.
    candidates = [
        node
        for node in nodes
        if node.text.strip().lower() == "profile" and node.bounds[1] < 800
    ]
    if not candidates:
        candidates = find_nodes(nodes, text_contains="Profile")
        candidates = [n for n in candidates if n.bounds[1] < 800]
    if not candidates:
        return False
    # Right-hand tab is usually the widest / rightmost Profile label.
    candidates.sort(key=lambda n: n.bounds[0], reverse=True)
    tap_bounds(device, candidates[0].bounds)
    time.sleep(2.0)
    return True


def open_chat_tab(device) -> bool:
    nodes = parse_ui_nodes(dump_ui_xml(device))
    candidates = [
        node
        for node in nodes
        if node.text.strip().lower() == "chat" and node.bounds[1] < 800
    ]
    if not candidates:
        return False
    candidates.sort(key=lambda n: n.bounds[0])
    tap_bounds(device, candidates[0].bounds)
    time.sleep(1.2)
    return True


def _is_chrome(text: str, match_name: str) -> bool:
    lowered = text.strip().lower()
    if not lowered or lowered in CHROME_TEXT:
        return True
    if match_name and lowered == match_name.strip().lower():
        return True
    if lowered.startswith("send a message"):
        return True
    if GT_SPAM_RE.match(text.strip()):
        return True
    if "google tv" in lowered or lowered.startswith("gt gt"):
        return True
    # System settings crumbs often look like "Media|Ringtone|" rows.
    if "|" in lowered and len(lowered) < 40:
        return True
    return False


def extract_profile_fields_from_nodes(
    nodes,
    match_name: str = "",
) -> List[ProfileField]:
    """Parse one UI dump into typed profile fields (unordered; caller assigns order)."""
    fields: List[ProfileField] = []
    seen_keys: Set[str] = set()

    def add(field_type: str, label: Optional[str], text: str, raw=None) -> None:
        text = re.sub(r"\s+", " ", (text or "").strip())
        if not text:
            return
        label_n = re.sub(r"\s+", " ", (label or "").strip()) if label else None
        key = f"{field_type}|{(label_n or '').lower()}|{text.lower()}"
        if key in seen_keys:
            return
        seen_keys.add(key)
        fields.append(
            ProfileField(
                field_type=field_type,
                label=label_n or None,
                text_content=text,
                raw=raw,
            )
        )

    drafts = composer_draft_texts(nodes)

    # Header name / verified from top content-descs.
    for node in nodes:
        if is_composer_node(node):
            continue
        desc = (node.content_desc or "").strip()
        if not desc or is_composer_draft_text(desc, drafts):
            continue
        if re.search(r",\s*verified\s*$", desc, re.I):
            name_part = re.sub(r",\s*verified\s*$", "", desc, flags=re.I).strip()
            if name_part:
                add("header", "name", name_part, {"content_desc": desc})
            add("header", "verified", "true", {"content_desc": desc})
        photo = PHOTO_RE.match(desc)
        if photo:
            add("photo", "photo", photo.group(1).strip() + "'s photo", {"content_desc": desc})
            continue
        prompt = PROMPT_RE.match(desc)
        if prompt:
            question = prompt.group(1).strip().rstrip(".")
            answer = prompt.group(2).strip()
            if is_composer_draft_text(answer, drafts):
                continue
            add(
                "prompt",
                question,
                answer,
                {"content_desc": desc, "question": question, "answer": answer},
            )
            continue

    # Basics: content-desc label + nearest following text value by Y.
    labeled = []
    text_nodes = []
    for node in nodes:
        rid = (node.resource_id or "").lower()
        # Ignore composer / send controls that overlay the profile.
        if is_composer_node(node):
            continue
        if "sendmessage" in rid or "microphone" in rid:
            continue
        desc = (node.content_desc or "").strip()
        text = (node.text or "").strip()
        if desc and desc.lower() in BASIC_LABELS:
            labeled.append((node.bounds[1], desc, node.bounds))
        if (
            text
            and not _is_chrome(text, match_name)
            and not is_composer_draft_text(text, drafts)
        ):
            text_nodes.append((node.bounds[1], text, node.bounds))

    labeled.sort(key=lambda item: item[0])
    text_nodes.sort(key=lambda item: item[0])
    used_text_idx: Set[int] = set()
    for label_y, label, label_bounds in labeled:
        best_i = None
        best_dy = None
        for index, (text_y, text, text_bounds) in enumerate(text_nodes):
            if index in used_text_idx:
                continue
            if text_y < label_y - 20:
                continue
            # Value usually sits on the same row or just below the label.
            if text_y > label_y + 220:
                continue
            dy = abs(text_y - label_y)
            if best_dy is None or dy < best_dy:
                best_dy = dy
                best_i = index
        if best_i is None:
            continue
        used_text_idx.add(best_i)
        value = text_nodes[best_i][1]
        add(
            "basic",
            label,
            value,
            {"label": label, "value": value, "label_y": label_y},
        )

    # Remaining non-chrome text: captions / freeform.
    for index, (text_y, text, _bounds) in enumerate(text_nodes):
        if index in used_text_idx:
            continue
        if _is_chrome(text, match_name):
            continue
        # Skip pure ages already captured, short nav crumbs, etc.
        if text.lower() in BASIC_LABELS:
            continue
        field_type = "caption" if len(text.split()) <= 12 else "other"
        add(field_type, None, text, {"y": text_y})

    return fields


def _profile_tab_active(nodes) -> bool:
    """Heuristic: Profile content visible (basics label, prompt, or photo desc)."""
    for node in nodes:
        desc = (node.content_desc or "").strip()
        if not desc:
            continue
        if desc.lower() in BASIC_LABELS:
            return True
        if desc.lower().startswith("prompt:"):
            return True
        if PHOTO_RE.match(desc):
            return True
    return False


def collect_profile_fields(
    device,
    width: int,
    height: int,
    match_name: str = "",
    *,
    max_scrolls: int = 16,
    stagnant_limit: int = 3,
    min_scrolls: int = 8,
) -> List[ProfileField]:
    """
    Open Profile tab (if needed), scroll through the profile, return all fields.
    Caller should already be inside the match conversation.
    """
    xml_text = dump_ui_xml(device)
    if not is_hinge_xml(xml_text):
        print("  profile scrape skipped: not in Hinge")
        return []

    opened = open_profile_tab(device)
    xml_text = dump_ui_xml(device)
    if not is_hinge_xml(xml_text):
        print("  profile scrape skipped: left Hinge opening Profile tab")
        return []
    nodes = parse_ui_nodes(xml_text)
    if not _profile_tab_active(nodes):
        # Retry once — sometimes the first tap hits Chat chrome.
        if opened:
            open_profile_tab(device)
        xml_text = dump_ui_xml(device)
        if not is_hinge_xml(xml_text):
            print("  profile scrape skipped: left Hinge after Profile retry")
            return []
        nodes = parse_ui_nodes(xml_text)
        if not _profile_tab_active(nodes):
            print("  profile scrape skipped: Profile tab not active")
            return []

    # Nudge to top of profile content.
    for _ in range(2):
        swipe(
            device,
            width // 2,
            int(height * 0.35),
            width // 2,
            int(height * 0.75),
            280,
        )
        time.sleep(0.5)

    ordered: List[ProfileField] = []
    seen: Set[str] = set()
    stagnant = 0

    def ingest() -> Optional[int]:
        xml_text = dump_ui_xml(device)
        if not is_hinge_xml(xml_text):
            print("  profile scrape abort: left Hinge while scrolling")
            return None
        nodes = parse_ui_nodes(xml_text)
        # Matches-list / Settings chrome can keep producing "new" captions forever.
        if not _profile_tab_active(nodes) and not ordered:
            return None
        batch = extract_profile_fields_from_nodes(nodes, match_name=match_name)
        added = 0
        for field in batch:
            key = (
                f"{field.field_type}|{(field.label or '').lower()}|"
                f"{field.text_content.lower()}"
            )
            if key in seen:
                continue
            seen.add(key)
            ordered.append(field)
            added += 1
        return added

    first = ingest()
    if first is None:
        print("  profile scrape skipped: no Profile content visible")
        return []

    for scroll_i in range(max_scrolls):
        swipe(
            device,
            width // 2,
            int(height * 0.70),
            width // 2,
            int(height * 0.38),
            320,
        )
        time.sleep(1.15)
        added = ingest()
        if added is None:
            break
        if added == 0:
            stagnant += 1
            if scroll_i + 1 >= min_scrolls and stagnant >= stagnant_limit:
                break
        else:
            stagnant = 0

    return ordered


def profile_fields_as_dicts(fields: List[ProfileField]) -> List[dict]:
    return [
        {
            "field_type": field.field_type,
            "label": field.label,
            "text_content": field.text_content,
            "raw": field.raw,
        }
        for field in fields
    ]
