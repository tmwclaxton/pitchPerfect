# app/sync_chats.py
"""
Sync full Hinge Matches chat histories into SQLite.

Examples:
  python sync_chats.py
  python sync_chats.py --max-chats 5
  python sync_chats.py --skip-new
  python migrate.py   # apply schema only
"""

from __future__ import annotations

import argparse
import time

from dotenv import load_dotenv

from config import SQLITE_PATH
from db import finish_run, start_run, sync_stats, upsert_match_history
from helper_functions import connect_device_auto, get_screen_resolution, open_hinge
from style_learner import messages_as_dicts
from ui_dump import open_matches, press_back, swipe
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
        {"max_chats": max_chats, "skip_new": skip_new, "sqlite": SQLITE_PATH},
    )
    print(f"Syncing up to {max_chats} Matches chat(s) into {SQLITE_PATH}")

    processed_names = set()
    stagnant_pages = 0
    total_inserted = 0
    total_messages = 0

    while len(processed_names) < max_chats and stagnant_pages < 4:
        conversations = list_match_conversations(
            device,
            skip_new_matches=skip_new,
            only_your_turn=False,
        )
        page_new = 0

        for conversation in conversations:
            if len(processed_names) >= max_chats:
                break
            key = conversation.name.lower()
            if key in processed_names:
                continue
            # Skip UI chrome that sometimes looks like a conversation row.
            if key in {"profile", "local", "matches", "hinge"} or len(key) > 48:
                processed_names.add(key)
                continue

            page_new += 1
            processed_names.add(key)
            section = conversation.section or "unknown"
            print(
                f"\n=== sync: {conversation.name} "
                f"[{section}] ({len(processed_names)}/{max_chats}) ==="
            )
            if conversation.preview:
                print(f"Preview: {conversation.preview}")

            open_conversation(device, conversation)
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
            total_inserted += int(result["inserted"])
            total_messages += int(result["message_count"])
            print(
                f"Saved match_id={result['match_id']} "
                f"messages={result['message_count']} "
                f"(+{result['inserted']} new)"
            )
            if history.messages:
                # Short preview of ends of the thread.
                first = history.messages[0]
                last = history.messages[-1]
                print(f"  first: {first.sender}: {first.text[:80]}")
                print(f"  last:  {last.sender}: {last.text[:80]}")
            else:
                print("  (no messages)")

            press_back(device)
            time.sleep(1.0)
            open_matches(device, width, height)

        if page_new == 0:
            stagnant_pages += 1
        else:
            stagnant_pages = 0

        if len(processed_names) < max_chats:
            _scroll_matches_list(device, width, height)

    stats = sync_stats()
    finish_run(
        run_id,
        {
            "chats_synced": len(processed_names),
            "messages_inserted": total_inserted,
            "db_matches": stats["matches"],
            "db_messages": stats["messages"],
        },
    )
    print("\n--- sync complete ---")
    print(f"Chats visited this run: {len(processed_names)}")
    print(f"New message rows inserted: {total_inserted}")
    print(
        f"DB totals: {stats['matches']} matches, "
        f"{stats['messages']} messages "
        f"({stats['matches_with_messages']} with history)"
    )
    print(f"SQLite: {SQLITE_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync all Hinge Matches chat histories into SQLite."
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
    args = parser.parse_args()
    run_sync(max_chats=args.max_chats, skip_new=args.skip_new)


if __name__ == "__main__":
    main()
