# app/sync_chats.py
"""
Sync full Hinge Matches chat histories + profile text into SQLite.

Examples:
  python sync_chats.py
  python sync_chats.py --max-chats 5
  python sync_chats.py --skip-new
  python sync_chats.py --skip-profile
  python migrate.py   # apply schema only
"""

from __future__ import annotations

import argparse
import time

from dotenv import load_dotenv

from config import SQLITE_PATH
from db import (
    finish_run,
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
    list_match_conversations,
    open_conversation,
)

# Safety ceiling when the Matches list is huge / count unknown.
DEFAULT_MAX_CHATS = 200


def _scroll_matches_list(device, width: int, height: int) -> None:
    swipe(
        device,
        width // 2,
        int(height * 0.75),
        width // 2,
        int(height * 0.40),
        400,
    )
    time.sleep(1.2)


def _scroll_matches_to_top(device, width: int, height: int, passes: int = 4) -> None:
    for _ in range(passes):
        swipe(
            device,
            width // 2,
            int(height * 0.35),
            width // 2,
            int(height * 0.85),
            300,
        )
        time.sleep(0.7)


def run_sync(
    *,
    max_chats: int,
    skip_new: bool = False,
    skip_profile: bool = False,
) -> None:
    load_dotenv()
    device = connect_device_auto()
    if not device:
        return
    width, height = get_screen_resolution(device)

    open_hinge(device=device)
    open_matches(device, width, height)
    _scroll_matches_to_top(device, width, height)

    run_id = start_run(
        "sync_history",
        {
            "max_chats": max_chats,
            "skip_new": skip_new,
            "skip_profile": skip_profile,
            "sqlite": SQLITE_PATH,
        },
    )
    print(
        f"Syncing up to {max_chats} Matches chat(s)"
        + (" + profiles" if not skip_profile else "")
        + f" into {SQLITE_PATH}"
    )

    synced_names = set()
    seen_names = set()
    stagnant_pages = 0
    total_msg_inserted = 0
    total_profile_inserted = 0

    while len(synced_names) < max_chats and stagnant_pages < 4:
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
            if key in {"profile", "chat", "local", "matches", "hinge"} or len(key) > 48:
                continue

            page_new += 1
            synced_names.add(key)
            section = conversation.section or "unknown"
            print(
                f"\n=== sync: {conversation.name} "
                f"[{section}] ({len(synced_names)}/{max_chats}) ==="
            )
            if conversation.preview:
                print(f"Preview: {conversation.preview}")

            open_conversation(device, conversation)
            if not ensure_hinge_foreground(device):
                print("  skip: could not stay in Hinge after opening chat")
                open_matches(device, width, height)
                continue

            history = collect_chat_history(
                device, width, height, conversation.name
            )
            history.is_new_match = conversation.is_new_match

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
            press_back(device)
            time.sleep(0.8)
            if not ensure_hinge_foreground(device):
                print("  recovery: forcing Hinge + Matches after chat")
            open_matches(device, width, height)
            time.sleep(0.6)

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
            "messages_inserted": total_msg_inserted,
            "profile_fields_inserted": total_profile_inserted,
            "db_matches": stats["matches"],
            "db_messages": stats["messages"],
            "db_profile_fields": stats.get("profile_fields", 0),
        },
    )
    print("\n--- sync complete ---")
    print(f"Chats visited this run: {len(synced_names)}")
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
    args = parser.parse_args()
    run_sync(
        max_chats=args.max_chats,
        skip_new=args.skip_new,
        skip_profile=args.skip_profile,
    )


if __name__ == "__main__":
    main()
