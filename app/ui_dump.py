# app/ui_dump.py
"""Parse Android UI Automator dumps for Hinge navigation and chat reading."""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Optional, Tuple

Bounds = Tuple[int, int, int, int]
BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
MESSAGE_DESC_RE = re.compile(
    r"^\s*(You|[^:]+):\s*(.*?)\s*$",
    re.DOTALL,
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
    nodes = parse_ui_nodes(dump_ui_xml(device))
    matches = find_nodes(nodes, desc_contains="Matches")
    if matches:
        tap_bounds(device, matches[0].bounds)
    else:
        # Fallback: 4th of 5 bottom-nav slots.
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
    nodes = parse_ui_nodes(dump_ui_xml(device))
    back = find_nodes(nodes, desc_contains="Back", clickable=True)
    if back:
        tap_bounds(device, back[0].bounds)
    else:
        device.shell("input keyevent 4")
    time.sleep(1.5)
