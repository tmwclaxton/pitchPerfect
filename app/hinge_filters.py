"""
Automate Hinge Discover → Dating preferences → Ethnicity filters.

Learned UI path (UK Hinge, probed 2026-07-20 on Honor PTP-N49):

1. Discover feed (bottom nav Discover selected).
2. Tap content-desc **Dating preferences** (top-left filter control).
3. On **Dating preferences** (pageTitle / Member preferences list), tap row
   content-desc starting with **Ethnicity**.
4. Ethnicity multi-select sheet: checkable rows with child TextView labels.
   Known labels include East Asian, Southeast Asian, South Asian, …
5. Tap **Back to preferences list**, then **Back to Settings** (returns to Discover).

Selection persists when leaving the Ethnicity sheet (no separate Save).
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from helper_functions import (
    connect_device_auto,
    get_screen_resolution,
    open_discover,
    open_hinge,
)
from ui_dump import (
    Bounds,
    dump_ui_xml,
    find_nodes,
    parse_bounds,
    parse_ui_nodes,
    press_back,
    swipe,
    tap_bounds,
)

# --- Persisted selectors (content-desc / resource-id / titles) ---

HINGE_PACKAGE = "co.hinge.app"

DESC_DATING_PREFERENCES = "Dating preferences"
DESC_BACK_TO_SETTINGS = "Back to Settings"
DESC_BACK_TO_PREFERENCES = "Back to preferences list"

TITLE_DATING_PREFERENCES = "Dating preferences"
TITLE_ETHNICITY = "Ethnicity"

RESOURCE_PAGE_TITLE = "co.hinge.app:id/pageTitle"
RESOURCE_PREF_BUTTON = "co.hinge.app:id/button"
RESOURCE_BACK = "co.hinge.app:id/back"

# Full Ethnicity sheet options observed in-app.
ETHNICITY_OPTIONS: Tuple[str, ...] = (
    "Black/African Descent",
    "East Asian",
    "Hispanic/Latino",
    "Middle Eastern",
    "Native American",
    "Pacific Islander",
    "South Asian",
    "Southeast Asian",
    "White/Caucasian",
    "Other",
    "Open to all",
)

# Preset → Hinge Ethnicity checkbox labels.
ETHNICITY_PRESET_LABELS: Dict[str, Tuple[str, ...]] = {
    "asian_baddies": ("East Asian", "Southeast Asian"),
    "asian": ("East Asian", "Southeast Asian"),
    "east_asian": ("East Asian",),
    "southeast_asian": ("Southeast Asian",),
    "south_asian": ("South Asian",),
    "open": ("Open to all",),
}

ASIAN_BADDIES_ETHNICITIES = ETHNICITY_PRESET_LABELS["asian_baddies"]


@dataclass
class CheckRow:
    label: str
    checked: bool
    bounds: Bounds


class FilterNavigationError(RuntimeError):
    """Raised when a required Hinge Preferences screen/control is missing."""


def parse_checkable_rows(xml_text: str) -> List[CheckRow]:
    """Parse Compose checkable rows (parent View checkable + child TextView label)."""
    start = xml_text.find("<")
    if start == -1:
        return []
    root = ET.fromstring(xml_text[start:])
    rows: List[CheckRow] = []
    for element in root.iter("node"):
        if element.attrib.get("checkable") != "true":
            continue
        if element.attrib.get("clickable") != "true":
            continue
        bounds = parse_bounds(element.attrib.get("bounds", ""))
        if bounds is None:
            continue
        label = ""
        for child in element:
            text = (child.attrib.get("text") or "").strip()
            if text:
                label = text
                break
        if not label:
            continue
        rows.append(
            CheckRow(
                label=label,
                checked=element.attrib.get("checked") == "true",
                bounds=bounds,
            )
        )
    return rows


def _dump(device) -> Tuple[str, list]:
    xml = dump_ui_xml(device)
    return xml, parse_ui_nodes(xml)


def _page_title(nodes) -> str:
    for node in find_nodes(nodes, resource_id=RESOURCE_PAGE_TITLE):
        if node.text:
            return node.text.strip()
    for node in nodes:
        if node.text in {TITLE_DATING_PREFERENCES, TITLE_ETHNICITY}:
            return node.text.strip()
    return ""


def on_dating_preferences(nodes) -> bool:
    if _page_title(nodes) == TITLE_DATING_PREFERENCES:
        return True
    return bool(find_nodes(nodes, desc_contains=DESC_BACK_TO_SETTINGS))


def on_ethnicity_picker(xml_text: str, nodes) -> bool:
    titles = {n.text.strip() for n in nodes if n.text}
    if TITLE_ETHNICITY in titles and find_nodes(
        nodes, desc_contains=DESC_BACK_TO_PREFERENCES
    ):
        return True
    labels = {row.label for row in parse_checkable_rows(xml_text)}
    return "East Asian" in labels and "Southeast Asian" in labels


def on_discover_with_prefs_entry(nodes) -> bool:
    return bool(find_nodes(nodes, desc_contains=DESC_DATING_PREFERENCES, clickable=True))


def _tap_desc(device, nodes, desc_contains: str) -> None:
    matches = find_nodes(nodes, desc_contains=desc_contains, clickable=True)
    if not matches:
        raise FilterNavigationError(f"Missing clickable control: {desc_contains!r}")
    tap_bounds(device, matches[0].bounds)
    time.sleep(1.0)


def _scroll_prefs_to_ethnicity(device, width: int, height: int, *, max_swipes: int = 8):
    """Scroll Dating preferences until the Ethnicity row is visible."""
    for _ in range(max_swipes):
        xml, nodes = _dump(device)
        if not on_dating_preferences(nodes):
            raise FilterNavigationError("Left Dating preferences while scrolling.")
        rows = find_nodes(nodes, desc_contains="Ethnicity", clickable=True)
        if rows:
            return rows[0]
        # Prefer scrolling toward Member preferences (up) then down.
        swipe(
            device,
            width // 2,
            int(height * 0.35),
            width // 2,
            int(height * 0.75),
            320,
        )
        time.sleep(0.55)
    # Try scrolling down if Ethnicity was below.
    for _ in range(max_swipes):
        xml, nodes = _dump(device)
        rows = find_nodes(nodes, desc_contains="Ethnicity", clickable=True)
        if rows:
            return rows[0]
        swipe(
            device,
            width // 2,
            int(height * 0.75),
            width // 2,
            int(height * 0.35),
            320,
        )
        time.sleep(0.55)
    raise FilterNavigationError("Ethnicity row not found on Dating preferences.")


def recover_to_discover(device, width: int, height: int) -> None:
    """
    Pop Ethnicity / Dating preferences (or unknown chrome) back to Discover.

    Prefer labeled backs; fall back to system Back + force Hinge + Discover tab.
    """
    for _ in range(6):
        xml, nodes = _dump(device)
        if on_discover_with_prefs_entry(nodes):
            return
        if on_ethnicity_picker(xml, nodes):
            backs = find_nodes(
                nodes, desc_contains=DESC_BACK_TO_PREFERENCES, clickable=True
            )
            if backs:
                tap_bounds(device, backs[0].bounds)
            else:
                press_back(device, settle_s=0.4)
            time.sleep(0.8)
            continue
        if on_dating_preferences(nodes):
            backs = find_nodes(
                nodes, desc_contains=DESC_BACK_TO_SETTINGS, clickable=True
            )
            if backs:
                tap_bounds(device, backs[0].bounds)
            else:
                press_back(device, settle_s=0.4)
            time.sleep(0.9)
            continue
        press_back(device, settle_s=0.35)
        time.sleep(0.45)

    open_hinge(device, force=True)
    time.sleep(1.0)
    open_discover(device, width, height)
    time.sleep(1.2)


def open_dating_preferences(device, width: int, height: int) -> None:
    """From Discover (or recover to it), open Dating preferences."""
    xml, nodes = _dump(device)
    if on_dating_preferences(nodes):
        return
    if on_ethnicity_picker(xml, nodes):
        _tap_desc(device, nodes, DESC_BACK_TO_PREFERENCES)
        xml, nodes = _dump(device)
        if on_dating_preferences(nodes):
            return

    if not on_discover_with_prefs_entry(nodes):
        recover_to_discover(device, width, height)
        xml, nodes = _dump(device)

    if not on_discover_with_prefs_entry(nodes):
        # Last resort: relaunch Hinge and hit Discover again.
        open_hinge(device, force=True)
        time.sleep(1.2)
        open_discover(device, width, height)
        time.sleep(1.4)
        xml, nodes = _dump(device)

    if not on_discover_with_prefs_entry(nodes):
        raise FilterNavigationError(
            "Discover 'Dating preferences' control not visible. "
            "Open Hinge Discover manually and retry."
        )
    _tap_desc(device, nodes, DESC_DATING_PREFERENCES)
    _, nodes = _dump(device)
    if not on_dating_preferences(nodes):
        raise FilterNavigationError(
            "Opened filter control but not on Dating preferences."
        )


def open_ethnicity_picker(device, width: int, height: int) -> None:
    xml, nodes = _dump(device)
    if on_ethnicity_picker(xml, nodes):
        return
    if not on_dating_preferences(nodes):
        open_dating_preferences(device, width, height)
    row = _scroll_prefs_to_ethnicity(device, width, height)
    tap_bounds(device, row.bounds)
    time.sleep(1.1)
    xml, nodes = _dump(device)
    if not on_ethnicity_picker(xml, nodes):
        raise FilterNavigationError("Failed to open Ethnicity picker.")


def set_ethnicity_selection(
    device,
    desired: Sequence[str],
    *,
    exclusive: bool = True,
) -> List[str]:
    """
    Toggle Ethnicity checkboxes so `desired` labels are checked.

    If exclusive=True, uncheck any other selected options (except when desired
    is only Open to all).
    Returns the final checked labels.
    """
    wanted = [label.strip() for label in desired if label and label.strip()]
    if not wanted:
        raise ValueError("At least one ethnicity label is required.")
    unknown = [label for label in wanted if label not in ETHNICITY_OPTIONS]
    if unknown:
        raise ValueError(
            f"Unknown ethnicity label(s): {unknown}. Known: {list(ETHNICITY_OPTIONS)}"
        )

    xml = dump_ui_xml(device)
    rows = parse_checkable_rows(xml)
    if not rows:
        raise FilterNavigationError("No checkable ethnicity rows on screen.")

    by_label = {row.label: row for row in rows}

    # Prefer selecting concrete labels first — Hinge clears "Open to all" itself.
    for label in wanted:
        row = by_label.get(label)
        if row is None:
            raise FilterNavigationError(f"Ethnicity option not on screen: {label}")
        if not row.checked:
            tap_bounds(device, row.bounds)
            time.sleep(0.55)
            xml = dump_ui_xml(device)
            by_label = {row.label: row for row in parse_checkable_rows(xml)}

    if exclusive:
        xml = dump_ui_xml(device)
        for row in parse_checkable_rows(xml):
            if row.checked and row.label not in wanted:
                tap_bounds(device, row.bounds)
                time.sleep(0.55)

    final = [
        row.label for row in parse_checkable_rows(dump_ui_xml(device)) if row.checked
    ]
    missing = [label for label in wanted if label not in final]
    if missing and wanted != ["Open to all"]:
        # Open to all may vanish from hierarchy when others are selected.
        raise FilterNavigationError(
            f"Ethnicity selection incomplete. Wanted {wanted}, got {final}."
        )
    return final


def leave_preferences_to_discover(device, width: int, height: int) -> None:
    """Back out of Ethnicity / Dating preferences to Discover feed."""
    recover_to_discover(device, width, height)


def read_ethnicity_summary(device, width: int, height: int) -> str:
    """Return Dating preferences Ethnicity detail, e.g. 'East Asian, Southeast Asian'."""
    open_dating_preferences(device, width, height)
    row = _scroll_prefs_to_ethnicity(device, width, height)
    detail = row.content_desc or ""
    # content-desc like "Ethnicity, East Asian, Southeast Asian"
    if detail.lower().startswith("ethnicity"):
        parts = [p.strip() for p in detail.split(",")]
        if len(parts) >= 2:
            return ", ".join(parts[1:])
    return detail


def apply_ethnicity_filters(
    device,
    ethnicities: Sequence[str],
    *,
    width: Optional[int] = None,
    height: Optional[int] = None,
    return_to_discover: bool = True,
) -> Dict[str, object]:
    """
    Full path: Discover → Dating preferences → Ethnicity → select → back.
    """
    if width is None or height is None:
        width, height = get_screen_resolution(device)

    open_dating_preferences(device, width, height)
    open_ethnicity_picker(device, width, height)
    selected = set_ethnicity_selection(device, ethnicities, exclusive=True)

    # Confirm on Dating preferences list before returning to Discover.
    xml, nodes = _dump(device)
    if on_ethnicity_picker(xml, nodes):
        _tap_desc(device, nodes, DESC_BACK_TO_PREFERENCES)
        time.sleep(0.8)
    row = _scroll_prefs_to_ethnicity(device, width, height)
    detail = row.content_desc or ""
    if detail.lower().startswith("ethnicity"):
        parts = [p.strip() for p in detail.split(",")]
        summary = ", ".join(parts[1:]) if len(parts) >= 2 else detail
    else:
        summary = detail or ", ".join(selected)

    if return_to_discover:
        leave_preferences_to_discover(device, width, height)

    return {
        "selected": selected,
        "summary": summary,
        "path": [
            "Discover",
            f"tap:{DESC_DATING_PREFERENCES}",
            "Dating preferences",
            "tap:Ethnicity",
            f"check:{list(ethnicities)}",
            f"tap:{DESC_BACK_TO_PREFERENCES}",
            f"tap:{DESC_BACK_TO_SETTINGS}",
        ],
    }


def resolve_ethnicity_labels(
    *,
    preset: Optional[str] = None,
    labels: Optional[Sequence[str]] = None,
) -> List[str]:
    if labels:
        return [str(x).strip() for x in labels if str(x).strip()]
    key = (preset or "asian_baddies").strip().lower()
    if key in ETHNICITY_PRESET_LABELS:
        return list(ETHNICITY_PRESET_LABELS[key])
    if key == "asian_baddies" or key == "asian":
        return list(ASIAN_BADDIES_ETHNICITIES)
    raise ValueError(
        f"Unknown ethnicity preset {preset!r}. "
        f"Known: {sorted(ETHNICITY_PRESET_LABELS)}"
    )


def run_apply_filters(
    *,
    preset: Optional[str] = None,
    labels: Optional[Sequence[str]] = None,
    dry_run: bool = False,
) -> Dict[str, object]:
    """Connect, optionally apply, return result dict."""
    from device_lock import acquire_device_lock

    ethnicities = resolve_ethnicity_labels(preset=preset, labels=labels)
    acquire_device_lock(owner="hinge_filters")
    device = connect_device_auto()
    if not device:
        raise FilterNavigationError("No ADB device connected.")
    width, height = get_screen_resolution(device)

    if dry_run:
        open_dating_preferences(device, width, height)
        summary = ""
        try:
            row = _scroll_prefs_to_ethnicity(device, width, height)
            summary = row.content_desc or ""
        finally:
            leave_preferences_to_discover(device, width, height)
        return {
            "dry_run": True,
            "would_select": ethnicities,
            "current_summary": summary,
            "options": list(ETHNICITY_OPTIONS),
        }

    return apply_ethnicity_filters(
        device,
        ethnicities,
        width=width,
        height=height,
        return_to_discover=True,
    )
