# app/ui_dump.py
"""Parse Android UI Automator dumps for Hinge navigation and chat reading."""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Optional, Set, Tuple

Bounds = Tuple[int, int, int, int]
BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
PACKAGE_RE = re.compile(r'package="([^"]+)"')
MESSAGE_DESC_RE = re.compile(
    r"^\s*(You|[^:]+):\s*(.*?)\s*$",
    re.DOTALL,
)
HINGE_PACKAGE = "co.hinge.app"
COMPOSER_PLACEHOLDER = "send a message"
COMPOSER_RESOURCE_FRAGMENTS = (
    "messagecomposition",
    "message_composition",
)


# High-level Hinge screens sync/draft automation must distinguish.
SCREEN_OFF_HINGE = "off_hinge"
SCREEN_MATCHES_LIST = "matches_list"
SCREEN_MATCH_CHAT = "match_chat"
SCREEN_MATCH_PROFILE = "match_profile"
SCREEN_DISCOVER = "discover"
SCREEN_STANDOUTS = "standouts"
SCREEN_LIKES_YOU = "likes_you"
SCREEN_UNKNOWN = "unknown"

# Bottom-nav / feed labels that mean we left Matches → conversation flow.
_FEED_NAV_LABELS = (
    ("discover", SCREEN_DISCOVER),
    ("explore", SCREEN_DISCOVER),
    ("standouts", SCREEN_STANDOUTS),
    ("likes you", SCREEN_LIKES_YOU),
)


@dataclass
class UiNode:
    text: str
    content_desc: str
    resource_id: str
    class_name: str
    clickable: bool
    editable: bool
    selected: bool
    bounds: Bounds
    children_text: List[str]


@dataclass
class ScreenContext:
    """Where automation believes it is inside (or outside) Hinge."""

    kind: str
    match_name: Optional[str] = None
    detail: str = ""

    @property
    def is_matches_list(self) -> bool:
        return self.kind == SCREEN_MATCHES_LIST

    @property
    def is_match_conversation(self) -> bool:
        return self.kind in {SCREEN_MATCH_CHAT, SCREEN_MATCH_PROFILE}

    @property
    def is_feed(self) -> bool:
        return self.kind in {
            SCREEN_DISCOVER,
            SCREEN_STANDOUTS,
            SCREEN_LIKES_YOU,
        }

    @property
    def is_lost_for_match_sync(self) -> bool:
        """True when sync must abort the current match and recover."""
        return self.kind in {
            SCREEN_OFF_HINGE,
            SCREEN_DISCOVER,
            SCREEN_STANDOUTS,
            SCREEN_LIKES_YOU,
            SCREEN_UNKNOWN,
            SCREEN_MATCHES_LIST,
        }


def parse_bounds(raw: str) -> Optional[Bounds]:
    match = BOUNDS_RE.match(raw or "")
    if not match:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def bounds_center(bounds: Bounds) -> Tuple[int, int]:
    x1, y1, x2, y2 = bounds
    return (x1 + x2) // 2, (y1 + y2) // 2


def dump_ui_xml(device, remote_path: str = "/sdcard/window_dump.xml") -> str:
    device.shell(f"uiautomator dump {remote_path}")
    # `cat` can truncate very large dumps; Hinge dumps are small enough.
    return device.shell(f"cat {remote_path}")


def ui_packages(xml_text: str) -> Set[str]:
    return {match.group(1) for match in PACKAGE_RE.finditer(xml_text or "")}


def is_hinge_xml(xml_text: str) -> bool:
    """True when the foreground dump is Hinge (not Settings/system search)."""
    packages = ui_packages(xml_text)
    return HINGE_PACKAGE in packages


def ensure_hinge_foreground(device, *, settle_s: float = 2.5) -> bool:
    """
    If the phone left Hinge (e.g. Honor/Android Settings search), reopen it.
    Returns True when Hinge is foreground after the call.
    """
    xml_text = dump_ui_xml(device)
    if is_hinge_xml(xml_text):
        return True
    packages = ", ".join(sorted(ui_packages(xml_text))[:4]) or "unknown"
    print(f"Left Hinge (foreground: {packages}); reopening co.hinge.app")
    # Import lazily to avoid circular imports with helper_functions.
    from helper_functions import open_hinge

    open_hinge(device, settle_s=settle_s)
    xml_text = dump_ui_xml(device)
    ok = is_hinge_xml(xml_text)
    if not ok:
        print("Failed to recover Hinge foreground")
    return ok


def parse_ui_nodes(xml_text: str) -> List[UiNode]:
    # adb shell sometimes prefixes status lines; keep from the first tag.
    start = xml_text.find("<")
    if start == -1:
        return []
    root = ET.fromstring(xml_text[start:])
    nodes: List[UiNode] = []
    for element in root.iter("node"):
        bounds = parse_bounds(element.attrib.get("bounds", ""))
        if bounds is None:
            continue
        children_text = []
        for child in element.iter("node"):
            child_text = (child.attrib.get("text") or "").strip()
            if child_text:
                children_text.append(child_text)
        nodes.append(
            UiNode(
                text=(element.attrib.get("text") or "").strip(),
                content_desc=(element.attrib.get("content-desc") or "").strip(),
                resource_id=(element.attrib.get("resource-id") or "").strip(),
                class_name=(element.attrib.get("class") or "").split(".")[-1],
                clickable=element.attrib.get("clickable") == "true",
                editable=element.attrib.get("editable") == "true",
                selected=element.attrib.get("selected") == "true",
                bounds=bounds,
                children_text=children_text,
            )
        )
    return nodes


def find_nodes(
    nodes: List[UiNode],
    *,
    text_contains: Optional[str] = None,
    desc_contains: Optional[str] = None,
    resource_id: Optional[str] = None,
    clickable: Optional[bool] = None,
    editable: Optional[bool] = None,
) -> List[UiNode]:
    matches = []
    for node in nodes:
        if text_contains is not None and text_contains.lower() not in node.text.lower():
            continue
        if (
            desc_contains is not None
            and desc_contains.lower() not in node.content_desc.lower()
        ):
            continue
        if resource_id is not None and node.resource_id != resource_id:
            continue
        if clickable is not None and node.clickable != clickable:
            continue
        if editable is not None and node.editable != editable:
            continue
        matches.append(node)
    return matches


def is_composer_node(node: UiNode) -> bool:
    """
    True for Hinge message-composer chrome (EditText / unsent draft / placeholder).
    These must never be treated as chat bubbles or profile fields.
    """
    rid = (node.resource_id or "").lower()
    if any(fragment in rid for fragment in COMPOSER_RESOURCE_FRAGMENTS):
        return True
    if node.editable or (node.class_name or "") == "EditText":
        return True
    text = (node.text or "").strip().lower()
    desc = (node.content_desc or "").strip().lower()
    if text == COMPOSER_PLACEHOLDER or desc == COMPOSER_PLACEHOLDER:
        return True
    return False


def composer_draft_texts(nodes: List[UiNode]) -> Set[str]:
    """Non-placeholder text currently sitting in composer EditText nodes."""
    drafts: Set[str] = set()
    for node in nodes:
        if not is_composer_node(node):
            continue
        for candidate in (node.text, node.content_desc):
            text = (candidate or "").strip()
            if not text or text.lower() == COMPOSER_PLACEHOLDER:
                continue
            drafts.add(text)
    return drafts


def is_composer_draft_text(text: str, drafts: Optional[Set[str]] = None) -> bool:
    """True for placeholder or an exact unsent composer draft string."""
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    if not normalized:
        return False
    if normalized.lower() == COMPOSER_PLACEHOLDER:
        return True
    if drafts and normalized in drafts:
        return True
    if drafts and normalized.lower() in {d.lower() for d in drafts}:
        return True
    return False


def tap_bounds(device, bounds: Bounds) -> None:
    x, y = bounds_center(bounds)
    device.shell(f"input tap {x} {y}")


def swipe(
    device,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    duration_ms: int = 400,
) -> None:
    device.shell(f"input swipe {x1} {y1} {x2} {y2} {duration_ms}")


def _top_tab_nodes(nodes: List[UiNode], label: str, *, max_y: int = 900) -> List[UiNode]:
    want = label.strip().lower()
    return [
        node
        for node in nodes
        if (node.text or "").strip().lower() == want and node.bounds[1] < max_y
    ]


def chat_profile_tabs_visible(nodes: List[UiNode]) -> bool:
    """True when in-conversation Chat + Profile tabs are both visible near the top."""
    return bool(_top_tab_nodes(nodes, "chat") and _top_tab_nodes(nodes, "profile"))


def header_match_name_visible(nodes: List[UiNode], match_name: str) -> bool:
    """True when the open conversation header looks like this match."""
    want = (match_name or "").strip().lower()
    if not want:
        return False
    for node in nodes:
        # Prefer header-area nodes (above Chat/Profile tabs / composer).
        if node.bounds[1] > 1100:
            continue
        text = (node.text or "").strip().lower()
        desc = (node.content_desc or "").strip().lower()
        if text == want or desc == want:
            return True
        if desc.startswith(want + ",") or desc.startswith(want + " "):
            return True
        if text.startswith(want + ",") or text.startswith(want + " "):
            return True
    return False


def _bottom_nav_hits(nodes: List[UiNode], height: int) -> List[UiNode]:
    floor = int(height * 0.86)
    hits = []
    for node in nodes:
        if node.bounds[1] < floor:
            continue
        label = ((node.content_desc or node.text) or "").strip().lower()
        if not label:
            continue
        if any(
            key in label
            for key, _ in _FEED_NAV_LABELS
        ) or label == "matches" or "matches" in label:
            hits.append(node)
    return hits


def active_bottom_nav_kind(nodes: List[UiNode], height: int) -> Optional[str]:
    """
    Return discover|standouts|likes_you|matches when bottom nav is readable.
    Prefer selected=true; fall back to presence of a single dominant feed label.
    """
    hits = _bottom_nav_hits(nodes, height)
    if not hits:
        return None

    def kind_for_label(label: str) -> Optional[str]:
        lowered = label.strip().lower()
        for key, kind in _FEED_NAV_LABELS:
            if key in lowered:
                return kind
        if "matches" in lowered:
            return SCREEN_MATCHES_LIST
        return None

    selected_kinds = []
    for node in hits:
        if not node.selected:
            continue
        kind = kind_for_label((node.content_desc or node.text) or "")
        if kind:
            selected_kinds.append(kind)
    if selected_kinds:
        # Prefer feed tabs over Matches when both claim selected (defensive).
        for feed_kind in (
            SCREEN_DISCOVER,
            SCREEN_STANDOUTS,
            SCREEN_LIKES_YOU,
        ):
            if feed_kind in selected_kinds:
                return feed_kind
        return selected_kinds[0]

    present = []
    for node in hits:
        kind = kind_for_label((node.content_desc or node.text) or "")
        if kind and kind not in present:
            present.append(kind)
    # Without selected=, only treat as feed when conversation chrome is absent
    # (caller decides). Here just expose Matches if present alone.
    if SCREEN_MATCHES_LIST in present and len(present) == 1:
        return SCREEN_MATCHES_LIST
    return None


def _matches_list_headers_visible(nodes: List[UiNode]) -> bool:
    for node in nodes:
        text = (node.text or "").strip().lower()
        if text.startswith("your turn") or text.startswith("their turn"):
            return True
        if text.startswith("hidden"):
            return True
    return False


def _looks_like_discover_feed(nodes: List[UiNode], height: int) -> Optional[str]:
    """
    Detect Discover / Standouts / Likes You / Explore when Chat/Profile chrome
    for a match conversation is missing.

    Important: bottom-nav labels (Discover/Standouts/Matches/…) are always in the
    dump — mere presence must NOT mean we left Matches.
    """
    if chat_profile_tabs_visible(nodes):
        return None
    # Matches list section headers beat feed heuristics.
    if _matches_list_headers_visible(nodes):
        return None

    # Only trust an explicitly selected feed tab.
    nav_kind = active_bottom_nav_kind(nodes, height)
    if nav_kind in {
        SCREEN_DISCOVER,
        SCREEN_STANDOUTS,
        SCREEN_LIKES_YOU,
    }:
        return nav_kind

    # Without selected=, require feed chrome that Matches list never shows:
    # e.g. standalone top "Standouts"/"Discover" title and no Matches header.
    top_titles = []
    for node in nodes:
        if node.bounds[1] > int(height * 0.25):
            continue
        label = ((node.text or node.content_desc) or "").strip().lower()
        if not label:
            continue
        for key, kind in _FEED_NAV_LABELS:
            if label == key or label.startswith(key + ","):
                top_titles.append(kind)
    has_matches_title = any(
        ((n.text or n.content_desc) or "").strip().lower() in {"matches", "matches,"}
        or ((n.text or "").strip().lower().startswith("matches"))
        for n in nodes
        if n.bounds[1] < int(height * 0.25)
    )
    if top_titles and not has_matches_title:
        return top_titles[0]
    return None


def _profile_content_visible(nodes: List[UiNode]) -> bool:
    photo_re = re.compile(r"['’]s photo\s*$", re.I)
    basic_labels = {
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
        "relationship type",
        "looking for",
    }
    for node in nodes:
        desc = (node.content_desc or "").strip()
        if not desc:
            continue
        lowered = desc.lower()
        if lowered in basic_labels or lowered.startswith("prompt:"):
            return True
        if photo_re.search(desc):
            return True
    return False


def _profile_tab_selected(nodes: List[UiNode]) -> bool:
    for node in _top_tab_nodes(nodes, "profile"):
        if node.selected:
            return True
        # Parent/sibling often carries selected=true around the Profile label.
        x1, y1, x2, y2 = node.bounds
        for other in nodes:
            if not other.selected:
                continue
            ox1, oy1, ox2, oy2 = other.bounds
            if oy1 > 900:
                continue
            # Overlap with the Profile tab region.
            if ox1 <= (x1 + x2) // 2 <= ox2 and oy1 <= (y1 + y2) // 2 <= oy2:
                return True
            if abs(ox1 - x1) < 80 and abs(oy1 - y1) < 80:
                return True
    return False


def classify_hinge_screen(
    nodes: List[UiNode],
    height: int,
    *,
    xml_text: Optional[str] = None,
    expect_match: Optional[str] = None,
) -> ScreenContext:
    """
    Classify the current UI into Matches list / match chat / match profile /
    Discover-like feeds / unknown / off-Hinge.
    """
    if xml_text is not None and not is_hinge_xml(xml_text):
        packages = ", ".join(sorted(ui_packages(xml_text))[:4]) or "unknown"
        return ScreenContext(SCREEN_OFF_HINGE, detail=f"packages={packages}")

    if chat_profile_tabs_visible(nodes):
        if expect_match and not header_match_name_visible(nodes, expect_match):
            return ScreenContext(
                SCREEN_UNKNOWN,
                detail=f"chat/profile tabs but not header for {expect_match!r}",
            )
        shown = (expect_match or "").strip() or None
        has_chat_bubbles = any(
            MESSAGE_DESC_RE.match((node.content_desc or "").strip())
            for node in nodes
            if not is_composer_node(node)
        )
        if _profile_tab_selected(nodes) or (
            _profile_content_visible(nodes) and not has_chat_bubbles
        ):
            return ScreenContext(
                SCREEN_MATCH_PROFILE,
                match_name=shown,
                detail="match conversation Profile tab",
            )
        return ScreenContext(
            SCREEN_MATCH_CHAT,
            match_name=shown,
            detail="match conversation Chat tab",
        )

    # Matches list markers before feed detection — bottom nav always lists
    # Discover/Standouts/Likes You even while Matches is open.
    if _matches_list_headers_visible(nodes):
        return ScreenContext(SCREEN_MATCHES_LIST, detail="section header visible")

    feed = _looks_like_discover_feed(nodes, height)
    if feed:
        return ScreenContext(feed, detail="feed/nav without match chat chrome")

    if in_match_conversation(nodes):
        shown = (expect_match or "").strip() or None
        if expect_match and not header_match_name_visible(nodes, expect_match):
            return ScreenContext(
                SCREEN_UNKNOWN,
                detail=f"conversation chrome but not {expect_match!r}",
            )
        return ScreenContext(
            SCREEN_MATCH_CHAT,
            match_name=shown,
            detail="composer / chat chrome",
        )

    nav_kind = active_bottom_nav_kind(nodes, height)
    if nav_kind == SCREEN_MATCHES_LIST:
        return ScreenContext(SCREEN_MATCHES_LIST, detail="bottom nav Matches")
    if nav_kind in {SCREEN_DISCOVER, SCREEN_STANDOUTS, SCREEN_LIKES_YOU}:
        return ScreenContext(nav_kind, detail="bottom nav feed tab")

    # Top "Matches" title without section headers (scrolled mid-list).
    for node in nodes:
        if node.bounds[1] > int(height * 0.25):
            continue
        label = ((node.text or node.content_desc) or "").strip().lower()
        if label == "matches" or label.startswith("matches,"):
            return ScreenContext(SCREEN_MATCHES_LIST, detail="Matches title visible")

    return ScreenContext(SCREEN_UNKNOWN, detail="unrecognized hinge screen")


def classify_device_screen(
    device,
    height: int,
    *,
    expect_match: Optional[str] = None,
) -> ScreenContext:
    xml_text = dump_ui_xml(device)
    nodes = parse_ui_nodes(xml_text) if is_hinge_xml(xml_text) else []
    if not is_hinge_xml(xml_text):
        return classify_hinge_screen([], height, xml_text=xml_text)
    return classify_hinge_screen(
        nodes, height, xml_text=xml_text, expect_match=expect_match
    )


def on_match_conversation_screen(
    nodes: List[UiNode],
    height: int,
    match_name: str,
    *,
    xml_text: Optional[str] = None,
) -> bool:
    """True only for this match's Chat/Profile conversation (not Discover)."""
    ctx = classify_hinge_screen(
        nodes, height, xml_text=xml_text, expect_match=match_name
    )
    return ctx.is_match_conversation and header_match_name_visible(nodes, match_name)


def on_match_profile_screen(
    nodes: List[UiNode],
    height: int,
    match_name: str,
    *,
    xml_text: Optional[str] = None,
) -> bool:
    """
    True only on a match's Profile tab inside a conversation.
    Discover/Standouts profile cards must return False — never scroll those.
    """
    ctx = classify_hinge_screen(
        nodes, height, xml_text=xml_text, expect_match=match_name
    )
    if ctx.kind != SCREEN_MATCH_PROFILE:
        # Still allow when tabs + header + profile content are present even if
        # classifier called it match_chat (composer still visible on Profile).
        if not chat_profile_tabs_visible(nodes):
            return False
        if not header_match_name_visible(nodes, match_name):
            return False
        if ctx.is_feed or ctx.kind in {SCREEN_OFF_HINGE, SCREEN_MATCHES_LIST}:
            return False
        return _profile_content_visible(nodes) or _profile_tab_selected(nodes)
    return header_match_name_visible(nodes, match_name)


def recover_to_matches(
    device,
    width: int,
    height: int,
    *,
    reason: str,
    max_backs: int = 4,
    lost: bool = True,
) -> bool:
    """
    Abort off-context scrolling: Back out, reopen Hinge if needed, open Matches.
    Returns True when Matches list looks visible afterward.
    """
    if lost:
        print(f"LOST CONTEXT: {reason}")
        print("  recovery: leave current screen → Matches list")
    else:
        print(f"  returning to Matches: {reason}")

    for attempt in range(max_backs):
        ctx = classify_device_screen(device, height)
        if ctx.is_matches_list:
            print(f"  recovery: already on Matches list ({ctx.detail})")
            return True
        if ctx.kind == SCREEN_OFF_HINGE:
            print(f"  recovery: off Hinge ({ctx.detail}); reopening")
            if not ensure_hinge_foreground(device, settle_s=2.0):
                print("  recovery FAILED: could not reopen Hinge")
                return False
            break
        if ctx.is_feed:
            print(f"  recovery: on {ctx.kind} feed — tapping Matches / Back")
            break
        if ctx.is_match_conversation:
            print(
                f"  recovery: Back from {ctx.kind}"
                + (f" ({ctx.match_name})" if ctx.match_name else "")
            )
            press_back(device, settle_s=0.45)
            continue
        print(f"  recovery: Back from {ctx.kind} ({ctx.detail}) [try {attempt + 1}]")
        press_back(device, settle_s=0.45)

    if not ensure_hinge_foreground(device, settle_s=2.0):
        print("  recovery FAILED: Hinge not foreground")
        return False

    open_matches(device, width, height, settle_s=1.0)
    ctx = classify_device_screen(device, height)
    if ctx.is_matches_list:
        print(f"  recovery OK: Matches list ({ctx.detail})")
        return True

    # Last resort: force Hinge open again + Matches.
    print(f"  recovery: still {ctx.kind}; forcing open_hinge + Matches")
    from helper_functions import open_hinge

    open_hinge(device, settle_s=2.5)
    open_matches(device, width, height, settle_s=1.0)
    ctx = classify_device_screen(device, height)
    ok = ctx.is_matches_list
    if ok:
        print(f"  recovery OK after reopen: Matches list ({ctx.detail})")
    else:
        print(f"  recovery FAILED: screen is {ctx.kind} ({ctx.detail})")
    return ok


def in_match_conversation(nodes: List[UiNode]) -> bool:
    """True when Chat/Profile + composer chrome is showing (not Matches list)."""
    has_composer = any(is_composer_node(node) for node in nodes)
    return has_composer or chat_profile_tabs_visible(nodes)


def matches_list_visible(nodes: List[UiNode], height: int) -> bool:
    """Heuristic: Matches list (not an open chat/profile thread)."""
    if in_match_conversation(nodes):
        return False
    feed = _looks_like_discover_feed(nodes, height)
    if feed:
        return False
    for node in nodes:
        text = (node.text or "").strip().lower()
        if text.startswith("your turn") or text.startswith("their turn"):
            return True
    nav = [
        node
        for node in find_nodes(nodes, desc_contains="Matches")
        if node.bounds[1] > int(height * 0.88)
    ]
    # Bottom-nav Matches alone is weak; require we are not in a thread/feed.
    return bool(nav)


def open_matches(
    device,
    width: int,
    height: int,
    *,
    settle_s: float = 1.0,
    xml_text: Optional[str] = None,
) -> None:
    """Open the Matches tab (speech-bubble nav item)."""
    if xml_text is None or not is_hinge_xml(xml_text):
        xml_text = dump_ui_xml(device)
        if not is_hinge_xml(xml_text):
            if not ensure_hinge_foreground(device):
                return
            xml_text = dump_ui_xml(device)

    # Leave any open chat/profile before tapping Matches — otherwise we stay
    # in-thread and the Matches list never appears.
    for _ in range(3):
        nodes = parse_ui_nodes(xml_text)
        if matches_list_visible(nodes, height):
            break
        if in_match_conversation(nodes):
            press_back(device, settle_s=0.45)
            xml_text = dump_ui_xml(device)
            if not is_hinge_xml(xml_text):
                if not ensure_hinge_foreground(device):
                    return
                xml_text = dump_ui_xml(device)
            continue
        break

    def _tap_matches_nav(local_nodes: List[UiNode]) -> bool:
        matches = find_nodes(local_nodes, desc_contains="Matches")
        # Prefer bottom-nav Matches over any in-content control with similar desc.
        # Use 0.82 — Hinge nav sits slightly above the absolute bottom on some devices.
        nav_matches = [
            node for node in matches if node.bounds[1] > int(height * 0.82)
        ]
        target = (nav_matches or matches or [None])[0]
        if target:
            tap_bounds(device, target.bounds)
            return True
        # Fallback: 4th of 5 bottom-nav slots — only safe inside Hinge.
        tap_bounds(
            device,
            (
                int(width * 0.70),
                int(height * 0.96),
                int(width * 0.80),
                int(height * 0.99),
            ),
        )
        return True

    nodes = parse_ui_nodes(xml_text)
    ctx = classify_hinge_screen(nodes, height, xml_text=xml_text)
    if ctx.is_matches_list or matches_list_visible(nodes, height):
        time.sleep(max(0.2, float(settle_s) * 0.4))
        return

    _tap_matches_nav(nodes)
    time.sleep(max(0.35, float(settle_s)))

    # If still in a thread or feed, Back (when in thread) + Matches tap again.
    xml_text = dump_ui_xml(device)
    nodes = parse_ui_nodes(xml_text)
    ctx = classify_hinge_screen(nodes, height, xml_text=xml_text)
    if ctx.is_matches_list or matches_list_visible(nodes, height):
        return
    if in_match_conversation(nodes):
        press_back(device, settle_s=0.45)
        xml_text = dump_ui_xml(device)
        nodes = parse_ui_nodes(xml_text)
    _tap_matches_nav(nodes)
    time.sleep(max(0.35, float(settle_s)))


def press_back(device, *, settle_s: float = 0.55) -> None:
    # Prefer the system Back key. Tapping a "Back" content-desc outside Hinge
    # (Honor Settings / search) keeps us trapped in system UI.
    device.shell("input keyevent 4")
    time.sleep(max(0.25, float(settle_s)))
    xml_text = dump_ui_xml(device)
    if is_hinge_xml(xml_text):
        return
    # Still off-app: one more Back, then force Hinge open.
    device.shell("input keyevent 4")
    time.sleep(0.45)
    if not is_hinge_xml(dump_ui_xml(device)):
        ensure_hinge_foreground(device)
