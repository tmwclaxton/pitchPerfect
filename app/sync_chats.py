# app/sync_chats.py
"""
Sync Hinge Matches chats + profiles into SQLite (two-phase by default).

Phase A (capture): walk the device, save UIAutomator XML dumps (+ optional PNGs).
Phase B (process): parse dumps in a thread pool and upsert into SQLite.

Examples:
  python sync_chats.py                      # capture then process
  python sync_chats.py --capture-only
  python sync_chats.py --process-only
  python sync_chats.py --process-only --run-dir data/captures/…_run3
  python sync_chats.py --max-chats 5 --workers 6
  python sync_chats.py --screenshots        # also save PNGs during capture
  python sync_chats.py --live               # old single-phase device loop
  python migrate.py
"""

from __future__ import annotations

import argparse
import time

from dotenv import load_dotenv

from config import CAPTURE_WORKERS, SQLITE_PATH
from db import (
    finish_run,
    match_is_fresh,
    start_run,
    sync_stats,
    upsert_match_history,
    upsert_profile_fields,
)
from helper_functions import connect_device_auto, get_screen_resolution, open_hinge
from profile_scraper import collect_profile_fields, profile_fields_as_dicts
from style_learner import messages_as_dicts
from sync_capture import DEFAULT_FRESH_HOURS, DEFAULT_MAX_CHATS, run_capture
from sync_process import run_process
from ui_dump import (
    classify_device_screen,
    ensure_hinge_foreground,
    open_matches,
    press_back,
    recover_to_matches,
    swipe,
)
from your_turn import (
    collect_chat_history,
    conversation_open_for_match,
    list_match_conversations,
    open_conversation,
)


def _history_belongs_to_other_match(history, match_name: str) -> bool:
    """Detect cross-thread scrapes via 'You liked Other's photo' bubbles."""
    want = (match_name or "").strip().lower()
    if not want or not history.messages:
        return False
    for message in history.messages:
        text = (message.text or "").strip()
        sender = (message.sender or "").strip().lower()
        if sender not in {"you", want} and sender not in {
            "prompt",
            "chat",
            "profile",
        }:
            if len(sender) >= 2 and sender != want:
                return True
        lower = text.lower()
        if "you liked " in lower and "'s " in lower:
            try:
                liked = lower.split("you liked ", 1)[1]
                liked_name = liked.split("'s ", 1)[0].strip()
            except IndexError:
                continue
            if liked_name and liked_name != want:
                return True
    return False


def _ensure_matches_list(device, width: int, height: int, *, why: str) -> bool:
    ctx = classify_device_screen(device, height)
    if ctx.is_matches_list:
        return True
    return recover_to_matches(
        device,
        width,
        height,
        reason=f"{why} (saw {ctx.kind}: {ctx.detail})",
    )


def _scroll_matches_list(device, width: int, height: int) -> bool:
    if not _ensure_matches_list(device, width, height, why="before Matches list scroll"):
        print("  skip list scroll: could not recover Matches list")
        return False
    swipe(
        device,
        width // 2,
        int(height * 0.78),
        width // 2,
        int(height * 0.35),
        350,
    )
    time.sleep(0.85)
    return True


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
            280,
        )
        time.sleep(0.45)


def run_sync_live(
    *,
    max_chats: int,
    skip_new: bool = False,
    skip_profile: bool = False,
    force: bool = False,
    fresh_hours: float = DEFAULT_FRESH_HOURS,
) -> None:
    """Legacy single-phase sync (parse + upsert inside the device loop)."""
    load_dotenv()
    device = connect_device_auto()
    if not device:
        return
    width, height = get_screen_resolution(device)

    open_hinge(device=device, settle_s=2.5)
    open_matches(device, width, height, settle_s=1.0)
    _scroll_matches_to_top(device, width, height)

    run_id = start_run(
        "sync_history_live",
        {
            "max_chats": max_chats,
            "skip_new": skip_new,
            "skip_profile": skip_profile,
            "force": force,
            "fresh_hours": fresh_hours,
            "sqlite": SQLITE_PATH,
            "mode": "live",
        },
    )
    print(
        f"[live] Syncing up to {max_chats} Matches chat(s)"
        + (" + profiles" if not skip_profile else "")
        + f" into {SQLITE_PATH}"
    )

    synced_names = set()
    seen_names = set()
    skipped_fresh = 0
    stagnant_pages = 0
    total_msg_inserted = 0
    total_profile_inserted = 0

    while len(synced_names) < max_chats and stagnant_pages < 8:
        if not _ensure_matches_list(
            device, width, height, why="before listing Matches conversations"
        ):
            print("Aborting sync page: Matches list unavailable")
            break

        conversations = list_match_conversations(
            device,
            skip_new_matches=skip_new,
            only_your_turn=False,
        )
        page_new = 0

        for conversation in conversations:
            if len(synced_names) >= max_chats:
                break
            key = conversation.name.lower()
            if key in seen_names:
                continue
            seen_names.add(key)
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

            page_new += 1
            synced_names.add(key)
            section = conversation.section or "unknown"

            if not force and match_is_fresh(
                conversation.name,
                require_profile=not skip_profile,
                max_age_hours=fresh_hours,
            ):
                skipped_fresh += 1
                print(
                    f"\n=== skip fresh: {conversation.name} "
                    f"[{section}] ({len(synced_names)}/{max_chats}) ==="
                )
                continue

            print(
                f"\n=== sync: {conversation.name} "
                f"[{section}] ({len(synced_names)}/{max_chats}) ==="
            )
            if conversation.preview:
                print(f"Preview: {conversation.preview}")

            if not _ensure_matches_list(
                device,
                width,
                height,
                why=f"before opening {conversation.name}",
            ):
                print(f"  skip: not on Matches list before {conversation.name}")
                continue

            open_conversation(device, conversation, settle_s=1.2)
            if not ensure_hinge_foreground(device, settle_s=2.0):
                print("  skip: could not stay in Hinge after opening chat")
                recover_to_matches(
                    device, width, height, reason="left Hinge after opening chat"
                )
                continue

            open_ctx = classify_device_screen(
                device, height, expect_match=conversation.name
            )
            if open_ctx.is_feed or open_ctx.is_lost_for_match_sync:
                print(
                    f"  skip: landed on {open_ctx.kind} instead of "
                    f"{conversation.name}'s chat ({open_ctx.detail})"
                )
                recover_to_matches(
                    device,
                    width,
                    height,
                    reason=(
                        f"wrong screen after opening {conversation.name}: "
                        f"{open_ctx.kind}"
                    ),
                )
                continue

            if not conversation_open_for_match(
                device, conversation.name, height=height
            ):
                print(
                    f"  skip: open screen is not {conversation.name}'s chat "
                    "(stale row / wrong tap)"
                )
                recover_to_matches(
                    device,
                    width,
                    height,
                    reason=f"wrong chat after tapping {conversation.name}",
                )
                continue

            history = collect_chat_history(
                device,
                width,
                height,
                conversation.name,
                settle_bottom=False,
            )
            mid_ctx = classify_device_screen(
                device, height, expect_match=conversation.name
            )
            if mid_ctx.is_feed or mid_ctx.kind == "off_hinge":
                print(
                    f"  skip: lost context during chat scrape "
                    f"({mid_ctx.kind}: {mid_ctx.detail})"
                )
                recover_to_matches(
                    device,
                    width,
                    height,
                    reason=f"lost during chat scrape for {conversation.name}",
                )
                continue

            history.is_new_match = conversation.is_new_match
            if _history_belongs_to_other_match(history, conversation.name):
                print(
                    f"  skip: scraped messages look like another match "
                    f"(not {conversation.name})"
                )
                recover_to_matches(
                    device,
                    width,
                    height,
                    reason=f"cross-thread scrape for {conversation.name}",
                )
                continue

            result = upsert_match_history(
                conversation.name,
                messages_as_dicts(history.messages),
                section=section,
                is_new_match=conversation.is_new_match,
                list_preview=conversation.preview or None,
                run_id=run_id,
            )
            match_id = int(result["match_id"])
            total_msg_inserted += int(result["inserted"])
            print(
                f"Saved match_id={match_id} "
                f"messages={result['message_count']} "
                f"(+{result['inserted']} new)"
            )

            if not skip_profile:
                pre_profile = classify_device_screen(
                    device, height, expect_match=conversation.name
                )
                if not pre_profile.is_match_conversation:
                    print(
                        f"  profile skipped: not in {conversation.name}'s chat "
                        f"({pre_profile.kind}: {pre_profile.detail})"
                    )
                    recover_to_matches(
                        device,
                        width,
                        height,
                        reason=(
                            f"not in conversation before profile for "
                            f"{conversation.name}"
                        ),
                    )
                    continue
                try:
                    profile_fields = collect_profile_fields(
                        device,
                        width,
                        height,
                        conversation.name,
                    )
                    post_profile = classify_device_screen(
                        device, height, expect_match=conversation.name
                    )
                    if post_profile.is_feed or post_profile.kind == "off_hinge":
                        print(
                            f"  profile aborted: wandered to {post_profile.kind}"
                        )
                        recover_to_matches(
                            device,
                            width,
                            height,
                            reason=(
                                f"lost during profile scrape for "
                                f"{conversation.name}"
                            ),
                        )
                        continue
                    if profile_fields:
                        inserted, total = upsert_profile_fields(
                            match_id,
                            profile_fields_as_dicts(profile_fields),
                        )
                        total_profile_inserted += inserted
                        print(f"  profile fields={total} (+{inserted} new)")
                    else:
                        print("  profile fields skipped (empty / wrong screen)")
                except Exception as exception:
                    print(f"  profile scrape failed: {exception}")
                    recover_to_matches(
                        device,
                        width,
                        height,
                        reason=f"profile exception for {conversation.name}",
                    )
                    continue

            press_back(device, settle_s=0.5)
            time.sleep(0.35)
            recover_to_matches(
                device,
                width,
                height,
                reason=f"after syncing {conversation.name}",
                lost=False,
            )

        if page_new == 0:
            stagnant_pages += 1
        else:
            stagnant_pages = 0

        if len(synced_names) < max_chats:
            if not _scroll_matches_list(device, width, height):
                print("Stopping sync: Matches list scroll unsafe")
                break

    stats = sync_stats()
    finish_run(
        run_id,
        {
            "chats_synced": len(synced_names),
            "skipped_fresh": skipped_fresh,
            "messages_inserted": total_msg_inserted,
            "profile_fields_inserted": total_profile_inserted,
            "db_matches": stats["matches"],
            "db_messages": stats["messages"],
            "db_profile_fields": stats.get("profile_fields", 0),
        },
    )
    print("\n--- live sync complete ---")
    print(f"Chats visited: {len(synced_names)}")
    print(f"DB totals: {stats['matches']} matches, {stats['messages']} messages")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description=(
            "Two-phase Hinge Matches sync: capture UI dumps on-device, "
            "then process them offline in a thread pool."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--capture-only",
        action="store_true",
        help="Only Phase A: walk device and save UI dumps.",
    )
    mode.add_argument(
        "--process-only",
        action="store_true",
        help="Only Phase B: parse an existing capture run into SQLite.",
    )
    mode.add_argument(
        "--live",
        action="store_true",
        help="Legacy single-phase sync (parse while driving the phone).",
    )
    parser.add_argument(
        "--run-id",
        type=int,
        default=None,
        help="Capture run id for --process-only (default: latest).",
    )
    parser.add_argument(
        "--run-dir",
        type=str,
        default=None,
        help="Capture directory for --process-only (manifest.json).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=CAPTURE_WORKERS,
        help=f"Thread pool size for Phase B (default {CAPTURE_WORKERS}).",
    )
    parser.add_argument(
        "--screenshots",
        action="store_true",
        help="Also save PNG screencaps during Phase A (slower).",
    )
    parser.add_argument(
        "--max-chats",
        type=int,
        default=DEFAULT_MAX_CHATS,
        help=f"Max conversations to capture/sync (default {DEFAULT_MAX_CHATS}).",
    )
    parser.add_argument(
        "--skip-new",
        action="store_true",
        help="Skip brand-new matches with no chat yet.",
    )
    parser.add_argument(
        "--skip-profile",
        action="store_true",
        help="Skip Profile tab capture/processing.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-capture even when the match is fresh in SQLite.",
    )
    parser.add_argument(
        "--fresh-hours",
        type=float,
        default=DEFAULT_FRESH_HOURS,
        help=(
            "Skip matches synced within this many hours "
            f"(default {DEFAULT_FRESH_HOURS:g})."
        ),
    )
    args = parser.parse_args()

    if args.live:
        run_sync_live(
            max_chats=args.max_chats,
            skip_new=args.skip_new,
            skip_profile=args.skip_profile,
            force=args.force,
            fresh_hours=args.fresh_hours,
        )
        return

    if args.process_only:
        run_process(
            run_id=args.run_id,
            root_dir=args.run_dir,
            workers=args.workers,
        )
        return

    # Default: capture then process (or capture-only).
    capture_run = run_capture(
        max_chats=args.max_chats,
        skip_new=args.skip_new,
        skip_profile=args.skip_profile,
        force=args.force,
        fresh_hours=args.fresh_hours,
        with_screenshots=args.screenshots,
    )
    if args.capture_only:
        print("Capture-only done; run with --process-only to upsert SQLite.")
        return

    run_process(run_id=capture_run.id, workers=args.workers)


if __name__ == "__main__":
    main()
