# app/sync_capture.py
"""
Phase A: walk Hinge Matches on-device and save UI dumps (+ optional PNGs).

No SQLite message/profile upserts here — only navigation guards + artifact IO.
"""

from __future__ import annotations

import hashlib
import time
from typing import Optional, Set

from capture_store import (
    CaptureRun,
    abandon_stale_capture_runs,
    completed_match_keys,
    create_capture_run,
    find_resumable_capture_run,
    finish_capture_run,
    mark_match_progress,
    persist_run_progress,
    register_match_meta,
    save_ui_dump,
    write_manifest,
)
from db import match_is_fresh, name_key
from device_lock import acquire_device_lock
from helper_functions import connect_device_auto, get_screen_resolution, open_hinge
from profile_scraper import open_profile_tab
from ui_dump import (
    classify_device_screen,
    classify_hinge_screen,
    dump_ui_xml,
    ensure_hinge_foreground,
    on_match_conversation_screen,
    on_match_profile_screen,
    open_matches,
    parse_ui_nodes,
    press_back,
    recover_to_matches,
    swipe,
)
from your_turn import (
    conversation_open_for_match,
    conversation_row_tappable,
    list_match_conversations,
    open_conversation,
)

DEFAULT_MAX_CHATS = 200
DEFAULT_FRESH_HOURS = 24.0
DEFAULT_CHAT_SCROLLS = 8
DEFAULT_PROFILE_SCROLLS = 10


def _xml_fingerprint(xml_text: str) -> str:
    return hashlib.md5((xml_text or "").encode("utf-8", errors="ignore")).hexdigest()


def _maybe_screencap(device, *, enabled: bool) -> Optional[bytes]:
    if not enabled:
        return None
    try:
        return device.screencap()
    except Exception as exception:
        print(f"  screencap failed: {exception}")
        return None


def _ensure_matches_list(
    device,
    width: int,
    height: int,
    *,
    why: str,
    xml_text: Optional[str] = None,
) -> bool:
    ctx = classify_device_screen(device, height, xml_text=xml_text)
    if ctx.is_matches_list:
        return True
    return recover_to_matches(
        device,
        width,
        height,
        reason=f"{why} (saw {ctx.kind}: {ctx.detail})",
        max_backs=2,
    )


def _scroll_matches_list(device, width: int, height: int) -> bool:
    if not _ensure_matches_list(device, width, height, why="before Matches list scroll"):
        return False
    # Swipe inside the conversation list only. Starts at ~0.82 land on
    # Hidden / bottom-nav chrome and the RecyclerView never advances into
    # Their turn / Hidden rows.
    swipe(
        device,
        width // 2,
        int(height * 0.62),
        width // 2,
        int(height * 0.36),
        280,
    )
    time.sleep(0.35)
    swipe(
        device,
        width // 2,
        int(height * 0.58),
        width // 2,
        int(height * 0.34),
        240,
    )
    time.sleep(0.3)
    return True


def _expand_matches_sections(device, height: int) -> bool:
    """
    Tap visible Their turn / Hidden headers so collapsed sections open.
    Returns True if a header was tapped.
    """
    from ui_dump import parse_ui_nodes, tap_bounds

    xml_text = dump_ui_xml(device)
    nodes = parse_ui_nodes(xml_text)
    tapped = False
    nav_y = int(height * 0.88)
    for label in ("their turn", "hidden"):
        for node in nodes:
            text = (node.text or "").strip().lower()
            if not text.startswith(label):
                continue
            if node.bounds[1] >= nav_y:
                continue
            # Only tap when the header is mid/lower list (section still closed
            # or just revealed) — not the sticky title under the top bar.
            if node.bounds[1] < int(height * 0.25):
                continue
            print(f"  expanding section: {(node.text or '').strip()}")
            tap_bounds(device, node.bounds)
            time.sleep(0.45)
            tapped = True
            break
    return tapped


def _scroll_matches_to_top(device, width: int, height: int, passes: int = 3) -> None:
    if not _ensure_matches_list(device, width, height, why="before scrolling Matches to top"):
        return
    for _ in range(passes):
        ctx = classify_device_screen(device, height)
        if not ctx.is_matches_list:
            recover_to_matches(
                device,
                width,
                height,
                reason=f"lost Matches while scrolling to top ({ctx.kind})",
            )
            return
        swipe(
            device,
            width // 2,
            int(height * 0.32),
            width // 2,
            int(height * 0.88),
            220,
        )
        time.sleep(0.2)


def _capture_dump(
    run: CaptureRun,
    device,
    *,
    kind: str,
    sequence: int,
    match_name: Optional[str] = None,
    with_screenshots: bool = False,
    meta: Optional[dict] = None,
    xml_text: Optional[str] = None,
) -> str:
    xml = xml_text if xml_text is not None else dump_ui_xml(device)
    save_ui_dump(
        run,
        xml,
        kind=kind,
        sequence=sequence,
        match_name=match_name,
        image_bytes=_maybe_screencap(device, enabled=with_screenshots),
        meta=meta,
    )
    return xml


def _capture_chat_scrolls(
    run: CaptureRun,
    device,
    width: int,
    height: int,
    match_name: str,
    *,
    max_scrolls: int,
    with_screenshots: bool,
) -> int:
    """Save chat UI dumps while scrolling up. Returns frames captured."""
    frames = 0
    seen_fps: Set[str] = set()
    stagnant = 0

    xml = dump_ui_xml(device)
    nodes = parse_ui_nodes(xml)
    if not on_match_conversation_screen(nodes, height, match_name, xml_text=xml):
        ctx = classify_hinge_screen(
            nodes, height, xml_text=xml, expect_match=match_name
        )
        print(
            f"  chat capture abort: not in {match_name}'s chat "
            f"({ctx.kind}: {ctx.detail})"
        )
        return 0

    _capture_dump(
        run,
        device,
        kind="chat",
        sequence=frames,
        match_name=match_name,
        with_screenshots=with_screenshots,
        xml_text=xml,
    )
    seen_fps.add(_xml_fingerprint(xml))
    frames += 1

    for _ in range(max_scrolls):
        # One dump after swipe serves both stagnation check and context guard.
        swipe(
            device,
            width // 2,
            int(height * 0.32),
            width // 2,
            int(height * 0.78),
            220,
        )
        time.sleep(0.2)
        xml = dump_ui_xml(device)
        nodes = parse_ui_nodes(xml)
        if not on_match_conversation_screen(
            nodes, height, match_name, xml_text=xml
        ):
            ctx = classify_hinge_screen(
                nodes, height, xml_text=xml, expect_match=match_name
            )
            print(
                f"  chat capture stop: left conversation "
                f"({ctx.kind}: {ctx.detail})"
            )
            break
        fp = _xml_fingerprint(xml)
        _capture_dump(
            run,
            device,
            kind="chat",
            sequence=frames,
            match_name=match_name,
            with_screenshots=with_screenshots,
            xml_text=xml,
        )
        frames += 1
        if fp in seen_fps:
            stagnant += 1
            if stagnant >= 2:
                break
        else:
            seen_fps.add(fp)
            stagnant = 0
    return frames


def _capture_profile_scrolls(
    run: CaptureRun,
    device,
    width: int,
    height: int,
    match_name: str,
    *,
    max_scrolls: int,
    with_screenshots: bool,
) -> int:
    """Open Profile tab if needed and save profile UI dumps while scrolling."""
    xml = dump_ui_xml(device)
    nodes = parse_ui_nodes(xml)
    ctx = classify_hinge_screen(
        nodes, height, xml_text=xml, expect_match=match_name
    )
    if ctx.is_feed or ctx.kind in {"matches_list", "off_hinge", "unknown"}:
        if not ctx.is_match_conversation:
            print(
                f"  profile capture skipped: wrong screen "
                f"({ctx.kind}: {ctx.detail})"
            )
            return 0

    if not on_match_profile_screen(nodes, height, match_name, xml_text=xml):
        if not open_profile_tab(device, xml_text=xml):
            print("  profile capture skipped: could not tap Profile tab")
            return 0
        xml = dump_ui_xml(device)
        nodes = parse_ui_nodes(xml)
        if not on_match_profile_screen(
            nodes, height, match_name, xml_text=xml
        ) and not on_match_conversation_screen(
            nodes, height, match_name, xml_text=xml
        ):
            ctx = classify_hinge_screen(
                nodes, height, xml_text=xml, expect_match=match_name
            )
            print(
                f"  profile capture skipped: not on match Profile "
                f"({ctx.kind}: {ctx.detail})"
            )
            return 0

    frames = 0
    seen_fps: Set[str] = set()
    stagnant = 0

    _capture_dump(
        run,
        device,
        kind="profile",
        sequence=frames,
        match_name=match_name,
        with_screenshots=with_screenshots,
        xml_text=xml,
    )
    seen_fps.add(_xml_fingerprint(xml))
    frames += 1

    for _ in range(max_scrolls):
        swipe(
            device,
            width // 2,
            int(height * 0.72),
            width // 2,
            int(height * 0.36),
            220,
        )
        time.sleep(0.2)
        xml = dump_ui_xml(device)
        nodes = parse_ui_nodes(xml)
        ctx = classify_hinge_screen(
            nodes, height, xml_text=xml, expect_match=match_name
        )
        if ctx.is_feed or ctx.kind == "off_hinge":
            print(f"  profile capture stop: {ctx.kind}")
            break
        if not (
            on_match_profile_screen(nodes, height, match_name, xml_text=xml)
            or on_match_conversation_screen(
                nodes, height, match_name, xml_text=xml
            )
        ):
            print(f"  profile capture stop: {ctx.kind} ({ctx.detail})")
            break
        fp = _xml_fingerprint(xml)
        _capture_dump(
            run,
            device,
            kind="profile",
            sequence=frames,
            match_name=match_name,
            with_screenshots=with_screenshots,
            xml_text=xml,
        )
        frames += 1
        if fp in seen_fps:
            stagnant += 1
            if stagnant >= 2:
                break
        else:
            seen_fps.add(fp)
            stagnant = 0
    return frames


def run_capture(
    *,
    max_chats: int = DEFAULT_MAX_CHATS,
    skip_new: bool = False,
    skip_profile: bool = False,
    force: bool = False,
    fresh_hours: float = DEFAULT_FRESH_HOURS,
    with_screenshots: bool = False,
    chat_scrolls: int = DEFAULT_CHAT_SCROLLS,
    profile_scrolls: int = DEFAULT_PROFILE_SCROLLS,
    resume: bool = True,
) -> CaptureRun:
    """Phase A entrypoint: device walk + UI dump capture (resumable by default)."""
    acquire_device_lock(owner="sync_capture")
    device = connect_device_auto()
    if not device:
        raise RuntimeError("No device connected")
    width, height = get_screen_resolution(device)

    resumed = False
    run: Optional[CaptureRun] = None
    if resume and not force:
        run = find_resumable_capture_run()
        if run is not None:
            resumed = True
            run.status = "capturing"
            run.meta.update(
                {
                    "max_chats": max_chats,
                    "skip_new": skip_new,
                    "skip_profile": skip_profile,
                    "force": force,
                    "fresh_hours": fresh_hours,
                    "with_screenshots": with_screenshots,
                    "phase": "capture",
                    "resumed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
            )
            abandon_stale_capture_runs(except_id=run.id)
            persist_run_progress(run)
            print(f"Resuming capture run {run.id} → {run.root_dir}")
            last = run.meta.get("last_match")
            done_n = len(completed_match_keys(run))
            print(
                f"  checkpoint: {done_n} done"
                + (f", last={last}" if last else "")
            )

    if run is None:
        abandon_stale_capture_runs()
        run = create_capture_run(
            {
                "max_chats": max_chats,
                "skip_new": skip_new,
                "skip_profile": skip_profile,
                "force": force,
                "fresh_hours": fresh_hours,
                "with_screenshots": with_screenshots,
                "phase": "capture",
                "completed_keys": [],
                "failed_keys": [],
            }
        )
        print(f"Capture run {run.id} → {run.root_dir}")

    print(
        f"Capturing up to {max_chats} match(es)"
        + (" + profiles" if not skip_profile else "")
        + (" + PNGs" if with_screenshots else " (XML only)")
        + ("" if force else f"; skip fresh <{fresh_hours:g}h")
        + (" [RESUME]" if resumed else "")
    )

    open_hinge(device=device, settle_s=0.6)
    open_matches(device, width, height, settle_s=0.3)
    _scroll_matches_to_top(device, width, height)

    # Baseline Matches list dump (one dump, reuse for classify + save).
    baseline_xml = dump_ui_xml(device)
    if _ensure_matches_list(
        device,
        width,
        height,
        why="initial Matches list dump",
        xml_text=baseline_xml,
    ):
        if not classify_device_screen(
            device, height, xml_text=baseline_xml
        ).is_matches_list:
            baseline_xml = dump_ui_xml(device)
        _capture_dump(
            run,
            device,
            kind="matches_list",
            sequence=0,
            with_screenshots=with_screenshots,
            xml_text=baseline_xml,
        )

    already_done = completed_match_keys(run) if (resumed or not force) else set()
    # captured_names tracks quota / visited; seen_names only names considered
    # during this walk. Do NOT seed seen_names with already_done — otherwise
    # resume re-sees checkpoint rows, page_new stays 0, and we stagnant-exit
    # before scrolling to uncaptured matches further down the list.
    captured_names: Set[str] = set(already_done)
    seen_names: Set[str] = set()
    skipped_fresh = int(run.meta.get("skipped_fresh") or 0)
    stagnant_pages = 0
    list_page = int(run.meta.get("list_page") or 0)

    try:
        while len(captured_names) < max_chats and stagnant_pages < 12:
            list_xml = dump_ui_xml(device)
            if not _ensure_matches_list(
                device,
                width,
                height,
                why="before listing Matches",
                xml_text=list_xml,
            ):
                print("Aborting capture: Matches list unavailable")
                break
            # If recovery ran, refresh list XML once.
            list_ctx = classify_device_screen(device, height, xml_text=list_xml)
            if not list_ctx.is_matches_list:
                list_xml = dump_ui_xml(device)

            list_page += 1
            run.meta["list_page"] = list_page
            _capture_dump(
                run,
                device,
                kind="matches_list",
                sequence=list_page,
                with_screenshots=with_screenshots,
                xml_text=list_xml,
            )

            conversations = list_match_conversations(
                device,
                skip_new_matches=skip_new,
                only_your_turn=False,
                xml_text=list_xml,
            )
            page_new = 0

            for conversation in conversations:
                if len(captured_names) >= max_chats:
                    break
                key = name_key(conversation.name)
                if key in seen_names:
                    continue
                if key in {
                    "profile",
                    "chat",
                    "local",
                    "matches",
                    "hinge",
                    "sent",
                    "delivered",
                    "read",
                    "liked",
                    "hidden",
                    "new",
                } or len(key) > 48:
                    continue
                if key.startswith("hidden (") or key.startswith("your turn"):
                    continue
                if key.startswith("their turn"):
                    continue
                # Row center under bottom nav taps Likes You — wait for scroll.
                if not conversation_row_tappable(conversation, height=height):
                    print(
                        f"\n=== defer (under bottom nav): {conversation.name} ==="
                    )
                    # Still counts as list progress so we keep scrolling.
                    page_new += 1
                    continue
                seen_names.add(key)

                page_new += 1
                section = conversation.section or "unknown"
                preview = conversation.preview or ""

                # Resume checkpoint: already captured/skipped in this run.
                if not force and key in already_done:
                    print(
                        f"\n=== skip done: {conversation.name} "
                        f"[{section}] ({len(captured_names)+1}/{max_chats}) ==="
                    )
                    captured_names.add(key)
                    continue

                if not force and match_is_fresh(
                    conversation.name,
                    require_profile=not skip_profile,
                    max_age_hours=fresh_hours,
                ):
                    skipped_fresh += 1
                    print(
                        f"\n=== skip fresh: {conversation.name} "
                        f"[{section}] ({len(captured_names)+1}/{max_chats}) ==="
                    )
                    captured_names.add(key)
                    already_done.add(key)
                    mark_match_progress(
                        run,
                        conversation.name,
                        capture_status="skipped_fresh",
                        section=section,
                        preview=preview,
                        is_new_match=conversation.is_new_match,
                    )
                    continue

                print(
                    f"\n=== capture: {conversation.name} "
                    f"[{section}] ({len(captured_names)+1}/{max_chats}) ==="
                )
                if preview:
                    print(f"Preview: {preview}")

                register_match_meta(
                    run,
                    conversation.name,
                    section=section,
                    preview=preview,
                    is_new_match=conversation.is_new_match,
                    capture_status="capturing",
                )
                persist_run_progress(run)

                def _fail(detail: str) -> None:
                    mark_match_progress(
                        run,
                        conversation.name,
                        capture_status="failed",
                        section=section,
                        preview=preview,
                        is_new_match=conversation.is_new_match,
                        detail=detail,
                    )
                    # Don't consume max_chats quota on failures.
                    captured_names.discard(key)

                if not _ensure_matches_list(
                    device,
                    width,
                    height,
                    why=f"before opening {conversation.name}",
                ):
                    print(f"  skip: not on Matches before {conversation.name}")
                    _fail("not on Matches before open")
                    continue

                if not open_conversation(
                    device, conversation, settle_s=0.35, height=height
                ):
                    print(
                        f"  defer: {conversation.name} row not safely tappable"
                    )
                    seen_names.discard(key)
                    continue
                # Single dump after open: classify + open-check reuse it.
                open_xml = dump_ui_xml(device)
                open_ctx = classify_device_screen(
                    device,
                    height,
                    expect_match=conversation.name,
                    xml_text=open_xml,
                )
                if open_ctx.kind == "off_hinge":
                    print("  skip: left Hinge after opening chat")
                    if not ensure_hinge_foreground(device, settle_s=0.6):
                        recover_to_matches(
                            device,
                            width,
                            height,
                            reason="left Hinge after open",
                            max_backs=1,
                        )
                        _fail("left Hinge after open")
                        continue
                    open_xml = dump_ui_xml(device)
                    open_ctx = classify_device_screen(
                        device,
                        height,
                        expect_match=conversation.name,
                        xml_text=open_xml,
                    )
                if open_ctx.is_feed or open_ctx.is_lost_for_match_sync:
                    print(
                        f"  skip: landed on {open_ctx.kind} instead of "
                        f"{conversation.name}'s chat"
                    )
                    recover_to_matches(
                        device,
                        width,
                        height,
                        reason=(
                            f"wrong screen after opening {conversation.name}: "
                            f"{open_ctx.kind}"
                        ),
                        max_backs=1,
                        xml_text=open_xml,
                    )
                    _fail(f"landed on {open_ctx.kind}")
                    continue

                if not conversation_open_for_match(
                    device,
                    conversation.name,
                    height=height,
                    xml_text=open_xml,
                ):
                    print(
                        f"  skip: not {conversation.name}'s chat "
                        "(stale row / wrong tap)"
                    )
                    recover_to_matches(
                        device,
                        width,
                        height,
                        reason=f"wrong chat after tapping {conversation.name}",
                        max_backs=1,
                        xml_text=open_xml,
                    )
                    _fail("wrong chat / stale tap")
                    continue

                captured_names.add(key)
                chat_frames = _capture_chat_scrolls(
                    run,
                    device,
                    width,
                    height,
                    conversation.name,
                    max_scrolls=chat_scrolls,
                    with_screenshots=with_screenshots,
                )
                print(f"  chat frames={chat_frames}")

                mid = classify_device_screen(
                    device, height, expect_match=conversation.name
                )
                if mid.is_feed or mid.kind == "off_hinge":
                    print(f"  abort match: lost context ({mid.kind})")
                    recover_to_matches(
                        device,
                        width,
                        height,
                        reason=f"lost during chat capture for {conversation.name}",
                        max_backs=1,
                    )
                    _fail(f"lost during chat ({mid.kind})")
                    continue

                profile_frames = 0
                if not skip_profile:
                    profile_frames = _capture_profile_scrolls(
                        run,
                        device,
                        width,
                        height,
                        conversation.name,
                        max_scrolls=profile_scrolls,
                        with_screenshots=with_screenshots,
                    )
                    print(f"  profile frames={profile_frames}")
                    post = classify_device_screen(
                        device, height, expect_match=conversation.name
                    )
                    if post.is_feed or post.kind == "off_hinge":
                        print(f"  abort after profile: {post.kind}")
                        recover_to_matches(
                            device,
                            width,
                            height,
                            reason=(
                                f"lost during profile capture for "
                                f"{conversation.name}"
                            ),
                            max_backs=1,
                        )
                        _fail(f"lost during profile ({post.kind})")
                        continue

                press_back(device, settle_s=0.2, check_hinge=False)
                time.sleep(0.15)
                back_ctx = classify_device_screen(device, height)
                if not back_ctx.is_matches_list:
                    recover_to_matches(
                        device,
                        width,
                        height,
                        reason=f"after capturing {conversation.name}",
                        lost=False,
                        max_backs=1,
                    )
                already_done.add(key)
                mark_match_progress(
                    run,
                    conversation.name,
                    capture_status="done",
                    section=section,
                    preview=preview,
                    is_new_match=conversation.is_new_match,
                    detail=(
                        f"chat_frames={chat_frames} "
                        f"profile_frames={profile_frames}"
                    ),
                )

            if page_new == 0:
                stagnant_pages += 1
                print(
                    f"  list page {list_page}: no new rows "
                    f"(stagnant {stagnant_pages}/12, "
                    f"visible={len(conversations)})"
                )
                # Collapsed Their turn / Hidden blocks scrolling into older rows.
                if stagnant_pages in {1, 3, 6} and _expand_matches_sections(
                    device, height
                ):
                    stagnant_pages = max(0, stagnant_pages - 1)
            else:
                stagnant_pages = 0
            run.meta["stagnant_pages"] = stagnant_pages
            run.meta["skipped_fresh"] = skipped_fresh
            persist_run_progress(run)

            if len(captured_names) < max_chats and stagnant_pages < 12:
                if not _scroll_matches_list(device, width, height):
                    print("Stopping capture: Matches list scroll unsafe")
                    break

        finish_capture_run(
            run,
            status="captured",
            meta_update={
                "matches_visited": len(captured_names),
                "skipped_fresh": skipped_fresh,
                "assets": len(run.assets),
                "completed_keys": sorted(already_done),
            },
        )
    except KeyboardInterrupt:
        run.status = "interrupted"
        finish_capture_run(
            run,
            status="interrupted",
            meta_update={
                "matches_visited": len(captured_names),
                "skipped_fresh": skipped_fresh,
                "assets": len(run.assets),
                "completed_keys": sorted(already_done),
                "error": "KeyboardInterrupt",
            },
        )
        print(
            f"\nInterrupted — progress saved on run {run.id} "
            f"({len(already_done)} done). Re-run to resume."
        )
        raise
    except Exception as exception:
        finish_capture_run(
            run,
            status="interrupted",
            meta_update={
                "matches_visited": len(captured_names),
                "skipped_fresh": skipped_fresh,
                "assets": len(run.assets),
                "completed_keys": sorted(already_done),
                "error": str(exception),
            },
        )
        raise

    print("\n--- capture complete ---")
    print(f"Run: {run.id}")
    print(f"Dir: {run.root_dir}")
    print(f"Matches visited: {len(captured_names)}")
    print(f"Skipped fresh: {skipped_fresh}")
    print(f"Assets saved: {len(run.assets)}")
    return run
