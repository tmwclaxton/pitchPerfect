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


@dataclass
class UiNode:
    text: str
    content_desc: str
    resource_id: str
    class_name: str
    clickable: bool
    editable: bool
    bounds: Bounds
    children_text: List[str]


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


def ensure_hinge_foreground(device) -> bool:
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

    open_hinge(device)
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


def open_matches(device, width: int, height: int) -> None:
    """Open the Matches tab (speech-bubble nav item)."""
    if not ensure_hinge_foreground(device):
        return
    nodes = parse_ui_nodes(dump_ui_xml(device))
    matches = find_nodes(nodes, desc_contains="Matches")
    # Prefer bottom-nav Matches over any in-content control with similar desc.
    nav_matches = [node for node in matches if node.bounds[1] > int(height * 0.88)]
    target = (nav_matches or matches or [None])[0]
    if target:
        tap_bounds(device, target.bounds)
    else:
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
    time.sleep(2)


def press_back(device) -> None:
    # Prefer the system Back key. Tapping a "Back" content-desc outside Hinge
    # (Honor Settings / search) keeps us trapped in system UI.
    device.shell("input keyevent 4")
    time.sleep(1.0)
    xml_text = dump_ui_xml(device)
    if is_hinge_xml(xml_text):
        return
    # Still off-app: one more Back, then force Hinge open.
    device.shell("input keyevent 4")
    time.sleep(0.8)
    if not is_hinge_xml(dump_ui_xml(device)):
        ensure_hinge_foreground(device)
