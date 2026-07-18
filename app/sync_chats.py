# app/sync_chats.py
"""
Sync full Hinge Matches chat histories + profile text into SQLite.

Examples:
  python sync_chats.py
  python sync_chats.py --max-chats 5
  python sync_chats.py --skip-new
  python sync_chats.py --skip-profile
  python sync_chats.py --force                 # re-scrape even if fresh in DB
  python sync_chats.py --fresh-hours 12
  python migrate.py   # apply schema only
"""

from __future__ import annotations

import argparse
import time

from dotenv import load_dotenv

from config import SQLITE_PATH
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
from ui_dump import ensure_hinge_foreground, open_matches, press_back, swipe
from your_turn import (
    collect_chat_history,
    conversation_open_for_match,
    list_match_conversations,
    open_conversation,
)

# Safety ceiling when the Matches list is huge / count unknown.
DEFAULT_MAX_CHATS = 200
DEFAULT_FRESH_HOURS = 24.0


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
            # Bubble attributed to a different person's name.
            if len(sender) >= 2 and sender != want:
                return True
        lower = text.lower()
        if "you liked " in lower and "'s " in lower:
            # e.g. "You liked Sara's photo."
            try:
                liked = lower.split("you liked ", 1)[1]
                liked_name = liked.split("'s ", 1)[0].strip()
            except IndexError:
                continue
            if liked_name and liked_name != want:
                return True
    return False


def _scroll_matches_list(device, width: int, height: int) -> None:
    swipe(
        device,
        width // 2,
        int(height * 0.78),
        width // 2,
        int(height * 0.35),
        350,
    )
    time.sleep(0.85)


def _scroll_matches_to_top(device, width: int, height: int, passes: int = 3) -> None:
    for _ in range(passes):
        swipe(
            device,
            width // 2,
            int(height * 0.32),
            width // 2,
            int(height * 0.88),
            280,
        )
        time.sleep(0.45)


def run_sync(
    *,
    max_chats: int,
    skip_new: bool = False,
    skip_profile: bool = False,
    force: bool = False,
    fresh_hours: float = DEFAULT_FRESH_HOURS,
) -> None:
    load_dotenv()
    device = connect_device_auto()
    if not device:
        return
    width, height = get_screen_resolution(device)

    open_hinge(device=device, settle_s=2.5)
    open_matches(device, width, height, settle_s=1.0)
    _scroll_matches_to_top(device, width, height)

    run_id = start_run(
        "sync_history",
        {
            "max_chats": max_chats,
            "skip_new": skip_new,
            "skip_profile": skip_profile,
            "force": force,
            "fresh_hours": fresh_hours,
            "sqlite": SQLITE_PATH,
        },
    )
    print(
        f"Syncing up to {max_chats} Matches chat(s)"
        + (" + profiles" if not skip_profile else "")
        + f" into {SQLITE_PATH}"
    )
    if not force:
        print(
            f"Skipping matches synced in the last {fresh_hours:g}h "
            f"with messages"
            + (" + profiles" if not skip_profile else "")
            + " (use --force to re-scrape)"
        )

    synced_names = set()
    seen_names = set()
    skipped_fresh = 0
    stagnant_pages = 0
    total_msg_inserted = 0
    total_profile_inserted = 0

    while len(synced_names) < max_chats and stagnant_pages < 8:
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
            # Skip UI chrome that sometimes looks like a conversation row.
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

            open_conversation(device, conversation, settle_s=1.2)
            if not ensure_hinge_foreground(device, settle_s=2.0):
                print("  skip: could not stay in Hinge after opening chat")
                open_matches(device, width, height, settle_s=0.8)
                continue
            if not conversation_open_for_match(device, conversation.name):
                print(
                    f"  skip: open screen is not {conversation.name}'s chat "
                    "(stale row / wrong tap)"
                )
                press_back(device, settle_s=0.45)
                open_matches(device, width, height, settle_s=0.8)
                continue

            history = collect_chat_history(
                device,
                width,
                height,
                conversation.name,
                settle_bottom=False,
            )
            history.is_new_match = conversation.is_new_match
            if _history_belongs_to_other_match(history, conversation.name):
                print(
                    f"  skip: scraped messages look like another match "
                    f"(not {conversation.name})"
                )
                press_back(device, settle_s=0.45)
                open_matches(device, width, height, settle_s=0.8)
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
            if history.messages:
                first = history.messages[0]
                last = history.messages[-1]
                print(f"  first: {first.sender}: {first.text[:80]}")
                print(f"  last:  {last.sender}: {last.text[:80]}")
            else:
                print("  (no messages)")

            if not skip_profile:
                try:
                    profile_fields = collect_profile_fields(
                        device,
                        width,
                        height,
                        conversation.name,
                    )
                    if profile_fields:
                        inserted, total = upsert_profile_fields(
                            match_id,
                            profile_fields_as_dicts(profile_fields),
                        )
                        total_profile_inserted += inserted
                        print(f"  profile fields={total} (+{inserted} new)")
                        for field in profile_fields[:8]:
                            label = f"{field.label}: " if field.label else ""
                            print(
                                f"    [{field.field_type}] {label}"
                                f"{field.text_content[:90]}"
                            )
                        if len(profile_fields) > 8:
                            print(f"    ... +{len(profile_fields) - 8} more")
                    else:
                        print("  profile fields skipped (empty / wrong screen)")
                except Exception as exception:
                    print(f"  profile scrape failed: {exception}")

            # Leave profile/chat and land back on Matches list.
            press_back(device, settle_s=0.5)
            time.sleep(0.35)
            if not ensure_hinge_foreground(device, settle_s=2.0):
                print("  recovery: forcing Hinge + Matches after chat")
            open_matches(device, width, height, settle_s=0.8)
            time.sleep(0.3)

        if page_new == 0:
            stagnant_pages += 1
        else:
            stagnant_pages = 0

        if len(synced_names) < max_chats:
            _scroll_matches_list(device, width, height)

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
    print("\n--- sync complete ---")
    print(f"Chats visited this run: {len(synced_names)}")
    print(f"Skipped (fresh in DB): {skipped_fresh}")
    print(f"New message rows inserted: {total_msg_inserted}")
    print(f"New profile field rows inserted: {total_profile_inserted}")
    print(
        f"DB totals: {stats['matches']} matches, "
        f"{stats['messages']} messages, "
        f"{stats.get('profile_fields', 0)} profile fields "
        f"({stats.get('matches_with_profiles', 0)} with profiles)"
    )
    print(f"SQLite: {SQLITE_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync all Hinge Matches chats + profiles into SQLite."
    )
    parser.add_argument(
        "--max-chats",
        type=int,
        default=DEFAULT_MAX_CHATS,
        help=f"Max conversations to sync (default {DEFAULT_MAX_CHATS}).",
    )
    parser.add_argument(
        "--skip-new",
        action="store_true",
        help="Skip brand-new matches with no chat yet.",
    )
    parser.add_argument(
        "--skip-profile",
        action="store_true",
        help="Only sync chat history (skip Profile tab).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-scrape even when the match is fresh in SQLite.",
    )
    parser.add_argument(
        "--fresh-hours",
        type=float,
        default=DEFAULT_FRESH_HOURS,
        help=(
            "Skip matches that already have messages (+ profiles unless "
            f"--skip-profile) synced within this many hours "
            f"(default {DEFAULT_FRESH_HOURS:g})."
        ),
    )
    args = parser.parse_args()
    run_sync(
        max_chats=args.max_chats,
        skip_new=args.skip_new,
        skip_profile=args.skip_profile,
        force=args.force,
        fresh_hours=args.fresh_hours,
    )


if __name__ == "__main__":
    main()
