# app/draft_replies.py
"""
Draft replies for Hinge Your Turn, and learn texting style from Matches.

Examples:
  python sync_chats.py                     # full Matches history -> SQLite
  python draft_replies.py --sync-history   # alias for sync_chats
  python draft_replies.py --init-style
  python draft_replies.py --max-chats 2
  python draft_replies.py --all
  python draft_replies.py --all --no-paste
"""

from __future__ import annotations

import argparse
import time
import uuid

from dotenv import load_dotenv

from config import (
    STYLE_INIT_MAX_CHATS,
    YOUR_TURN_MAX_CHATS,
    YOUR_TURN_PASTE_DRAFTS,
)
from data_store import store_draft_reply
from db import finish_run, start_run, store_conversation
from helper_functions import connect_device_auto, get_screen_resolution, open_hinge
from reply_drafter import draft_scored_reply
from style_learner import infer_style_profile, messages_as_dicts
from ui_dump import open_matches, press_back, swipe
from your_turn import (
    ConversationHistory,
    collect_chat_history,
    ensure_matches_your_turn,
    focus_composer_and_type,
    list_match_conversations,
    list_your_turn_conversations,
    open_conversation,
    your_turn_count,
)

# Safety ceiling when --all is set and the UI count can't be read.
ALL_CHATS_FALLBACK_LIMIT = 50


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


def _connect():
    load_dotenv()
    device = connect_device_auto()
    if not device:
        return None, None, None
    width, height = get_screen_resolution(device)
    return device, width, height


def run_init_style(max_chats: int) -> None:
    """Collect Matches chat histories and persist a learned style profile."""
    device, width, height = _connect()
    if not device:
        return

    open_hinge(device=device)
    open_matches(device, width, height)
    # Start near the top of Matches.
    for _ in range(2):
        swipe(
            device,
            width // 2,
            int(height * 0.35),
            width // 2,
            int(height * 0.85),
            300,
        )
        time.sleep(0.8)

    run_id = start_run("init_style", {"max_chats": max_chats})
    print(f"Learning texting style from up to {max_chats} Matches chat(s).")

    processed_names = set()
    histories: list[ConversationHistory] = []
    stagnant_pages = 0

    while len(processed_names) < max_chats and stagnant_pages < 3:
        conversations = list_match_conversations(device, skip_new_matches=True)
        page_new = 0

        for conversation in conversations:
            if len(processed_names) >= max_chats:
                break
            key = conversation.name.lower()
            if key in processed_names:
                continue

            page_new += 1
            processed_names.add(key)
            print(
                f"\n=== style sample: {conversation.name} "
                f"({len(processed_names)}/{max_chats}) ==="
            )
            open_conversation(device, conversation)
            history = collect_chat_history(
                device, width, height, conversation.name
            )
            history.is_new_match = conversation.is_new_match
            print(history.as_transcript())
            store_conversation(
                match_name=conversation.name,
                transcript=history.as_transcript(),
                messages=messages_as_dicts(history.messages),
                source="style_init",
                is_new_match=conversation.is_new_match,
                run_id=run_id,
            )
            if history.messages:
                histories.append(history)

            press_back(device)
            time.sleep(1.0)
            open_matches(device, width, height)

        if page_new == 0:
            stagnant_pages += 1
        else:
            stagnant_pages = 0

        if len(processed_names) < max_chats:
            _scroll_matches_list(device, width, height)

    profile = infer_style_profile(histories)
    finish_run(
        run_id,
        {
            "chats_seen": len(processed_names),
            "chats_with_messages": len(histories),
            "sample_count": profile.get("message_count", 0),
        },
    )
    print("\n--- learned style ---")
    print(profile.get("summary") or profile)
    print(
        f"\nSaved style profile from {len(histories)} chat(s) "
        f"({profile.get('message_count', 0)} of your messages)."
    )


def run(max_chats: int, paste: bool, process_all: bool = False) -> None:
    device, width, height = _connect()
    if not device:
        return

    open_hinge(device=device)
    ensure_matches_your_turn(device, width, height)

    if process_all:
        counted = your_turn_count(device)
        max_chats = counted if counted is not None else ALL_CHATS_FALLBACK_LIMIT
        print(
            "Processing all Your Turn matches"
            + (f" ({counted} listed)." if counted is not None else " (count unknown).")
        )
    else:
        print(f"Processing up to {max_chats} Your Turn match(es).")

    run_id = start_run(
        "your_turn_drafts",
        {"max_chats": max_chats, "paste": paste, "all": process_all},
    )
    processed_names = set()
    stagnant_pages = 0

    while len(processed_names) < max_chats and stagnant_pages < 3:
        conversations = list_your_turn_conversations(device)
        page_new = 0

        for conversation in conversations:
            if len(processed_names) >= max_chats:
                break
            if conversation.name.lower() in processed_names:
                continue

            page_new += 1
            processed_names.add(conversation.name.lower())
            print(f"\n=== {conversation.name} ({len(processed_names)}/{max_chats}) ===")
            print(f"Preview: {conversation.preview}")

            open_conversation(device, conversation)
            history = collect_chat_history(
                device,
                width,
                height,
                conversation.name,
            )
            history.is_new_match = conversation.is_new_match

            print("--- transcript ---")
            print(history.as_transcript())
            print("------------------")

            conversation_id = store_conversation(
                match_name=conversation.name,
                transcript=history.as_transcript(),
                messages=messages_as_dicts(history.messages),
                source="your_turn",
                is_new_match=conversation.is_new_match,
                run_id=run_id,
            )

            try:
                result = draft_scored_reply(history, n_candidates=3)
            except Exception as exception:
                print(f"Draft failed for {conversation.name}: {exception}")
                press_back(device)
                continue

            reply = result["reply"]
            print(f"Draft: {reply}")
            print(f"Score: {result['score']['total']}")
            print(f"Contact stage: {result.get('contact_stage')}")
            for index, candidate in enumerate(result["candidates"], start=1):
                marker = "←" if candidate["reply"] == reply else " "
                print(
                    f"  [c{index}] {candidate['total']:.2f} {marker} {candidate['reply']}"
                )

            pasted = False
            if paste:
                pasted = focus_composer_and_type(device, reply)
                label = "opener" if conversation.is_new_match else "reply"
                print(
                    f"Pasted {label} into composer (not sent)."
                    if pasted
                    else "Could not find composer."
                )

            store_draft_reply(
                draft_id=str(uuid.uuid4()),
                match_name=conversation.name,
                transcript=history.as_transcript(),
                draft_reply=reply,
                pasted=pasted,
                is_new_match=conversation.is_new_match,
                score=result["score"],
                candidates=result["candidates"],
                conversation_id=conversation_id,
                run_id=run_id,
            )

            press_back(device)
            time.sleep(1.0)
            ensure_matches_your_turn(device, width, height)

        if page_new == 0:
            stagnant_pages += 1
        else:
            stagnant_pages = 0

        if len(processed_names) < max_chats:
            _scroll_matches_list(device, width, height)

    finish_run(run_id, {"drafted": len(processed_names)})
    print(f"\nDone. Drafted replies for {len(processed_names)} chat(s).")
    print("Saved to SQLite — nothing was sent.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Draft Hinge Your Turn replies / learn texting style."
    )
    parser.add_argument(
        "--sync-history",
        action="store_true",
        help="Sync all Matches chat histories into SQLite (see sync_chats.py).",
    )
    parser.add_argument(
        "--init-style",
        action="store_true",
        help="Collect Matches chat histories and learn your texting style.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Process every match in Your Turn (reads the on-screen count).",
    )
    parser.add_argument(
        "--max-chats",
        type=int,
        default=None,
        help="Max chats to process (Your Turn drafts or style init).",
    )
    parser.add_argument(
        "--paste",
        action="store_true",
        default=YOUR_TURN_PASTE_DRAFTS,
        help="Paste drafts into the composer without sending (default from env).",
    )
    parser.add_argument(
        "--no-paste",
        action="store_true",
        help="Only save drafts to SQLite.",
    )
    args = parser.parse_args()

    if args.sync_history:
        from sync_chats import DEFAULT_MAX_CHATS, run_sync

        run_sync(
            max_chats=args.max_chats or DEFAULT_MAX_CHATS,
            skip_new=False,
        )
        return

    if args.init_style:
        max_chats = args.max_chats or STYLE_INIT_MAX_CHATS
        run_init_style(max_chats=max_chats)
        return

    paste = False if args.no_paste else args.paste
    max_chats = args.max_chats or YOUR_TURN_MAX_CHATS
    run(max_chats=max_chats, paste=paste, process_all=args.all)


if __name__ == "__main__":
    main()
