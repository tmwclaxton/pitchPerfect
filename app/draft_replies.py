# app/draft_replies.py
"""
Draft replies for Hinge Your Turn, and learn texting style from Matches.

Examples:
  python sync_chats.py                     # full Matches history -> SQLite
  python draft_replies.py --sync-history   # alias for sync_chats
  python draft_replies.py --init-style
  python draft_replies.py --init-style --from-db   # learn style from SQLite only
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
    NANOGPT_MODEL,
    STYLE_INIT_MAX_CHATS,
    YOUR_TURN_MAX_CHATS,
    YOUR_TURN_PASTE_DRAFTS,
)
from data_store import store_draft_reply
from db import (
    finish_run,
    list_recent_drafts,
    pasted_draft_match_names,
    start_run,
    store_conversation,
)
from device_lock import acquire_device_lock
from helper_functions import connect_device_auto, get_screen_resolution, open_hinge
from reply_drafter import draft_scored_reply
from style_learner import histories_from_db, infer_style_profile, messages_as_dicts
from ui_dump import ensure_hinge_foreground, open_matches, press_back, swipe
from your_turn import (
    ConversationHistory,
    collect_chat_history,
    conversation_open_for_match,
    conversation_row_tappable,
    ensure_matches_your_turn,
    focus_composer_and_type,
    list_match_conversations,
    list_your_turn_conversations,
    open_conversation,
    your_turn_count,
)

# Safety ceiling when --all is set and the UI count can't be read.
ALL_CHATS_FALLBACK_LIMIT = 50
# Matches list often needs several scrolls past already-handled rows.
STAGNANT_PAGE_LIMIT = 12


def _scroll_matches_list(device, width: int, height: int) -> None:
    # Keep swipes inside the conversation RecyclerView (not bottom nav).
    # Dual swipe matches sync_capture — single short swipes often don't advance.
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


def _connect():
    load_dotenv()
    acquire_device_lock(owner="draft_replies")
    device = connect_device_auto()
    if not device:
        return None, None, None
    width, height = get_screen_resolution(device)
    return device, width, height


def run_init_style_from_db(max_chats: int) -> None:
    """Learn texting style from chats already saved in SQLite (no device)."""
    run_id = start_run("init_style_db", {"max_chats": max_chats, "source": "sqlite"})
    histories = histories_from_db(max_chats=max_chats, min_you_messages=1)
    print(
        f"Learning texting style from {len(histories)} saved chat(s) "
        f"(cap {max_chats}) in SQLite."
    )
    if not histories:
        finish_run(run_id, {"chats_seen": 0, "sample_count": 0})
        print("No usable chats in SQLite yet — run sync_chats.py first.")
        return

    for history in histories:
        you_n = sum(1 for m in history.messages if m.sender.lower() == "you")
        print(f"  style sample: {history.name} ({you_n} of your messages)")

    profile = infer_style_profile(histories)
    finish_run(
        run_id,
        {
            "chats_seen": len(histories),
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


def run_init_style(max_chats: int, *, from_db: bool = False) -> None:
    """Collect Matches chat histories and persist a learned style profile."""
    if from_db:
        run_init_style_from_db(max_chats)
        return

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
            if not open_conversation(
                device, conversation, height=height
            ):
                print(f"  defer: {conversation.name} under bottom nav")
                processed_names.discard(key)
                continue
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


def _scroll_matches_to_your_turn(device, width: int, height: int) -> None:
    """Fling Matches upward until Your turn count is readable."""
    ensure_hinge_foreground(device, settle_s=0.6)
    open_matches(device, width, height, settle_s=0.5)
    ensure_matches_your_turn(device, width, height, seek_top=True)
    for _ in range(10):
        if your_turn_count(device) is not None:
            return
        swipe(
            device,
            width // 2,
            int(height * 0.34),
            width // 2,
            int(height * 0.78),
            260,
        )
        time.sleep(0.25)
    ensure_matches_your_turn(device, width, height, seek_top=True)


def run(
    max_chats: int,
    paste: bool,
    process_all: bool = False,
    *,
    force: bool = False,
) -> None:
    device, width, height = _connect()
    if not device:
        return

    open_hinge(device=device, settle_s=0.6, force=True)
    _scroll_matches_to_your_turn(device, width, height)

    max_stagnant = STAGNANT_PAGE_LIMIT if process_all else 3
    counted = None
    print(f"NanoGPT model: {NANOGPT_MODEL}")
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
    # Already-pasted names skip re-draft. Do not seed seen_names with them —
    # that stagnant-exits before scrolling to unhandled rows further down.
    already_pasted = set() if force else pasted_draft_match_names()
    if force:
        print("Force mode: re-drafting even if already pasted.")
    elif already_pasted:
        print(f"Skipping {len(already_pasted)} already-pasted match(es).")
    # Anti-sameness: avoid repeating recent plan lines across matches.
    recent_draft_texts = [
        str(row.get("draft_reply") or "")
        for row in list_recent_drafts(30)
        if (row.get("draft_reply") or "").strip()
    ]
    seen_names: set[str] = set()
    drafted_names: set[str] = set()
    skipped_pasted = 0
    stagnant_pages = 0
    list_page = 0

    def _accounted() -> int:
        return len(drafted_names) + skipped_pasted

    while _accounted() < max_chats and stagnant_pages < max_stagnant:
        if not ensure_hinge_foreground(device, settle_s=0.5):
            print("Aborting drafts: could not keep Hinge foreground")
            break
        ensure_matches_your_turn(device, width, height)
        conversations = list_your_turn_conversations(device)
        # After leaving Hinge (e.g. Instagram), Matches can dump empty until
        # we force-reopen and scroll back to Your turn.
        if not conversations:
            print("  empty Your Turn list; hard-recovering Matches")
            open_hinge(device=device, settle_s=0.6, force=True)
            _scroll_matches_to_your_turn(device, width, height)
            conversations = list_your_turn_conversations(device)
        page_new = 0
        list_page += 1

        for conversation in conversations:
            if _accounted() >= max_chats:
                break
            key = conversation.name.lower()
            if key in seen_names:
                continue

            if not conversation_row_tappable(conversation, height=height):
                print(f"  defer: {conversation.name} under bottom nav")
                page_new += 1
                continue

            page_new += 1
            seen_names.add(key)

            if key in already_pasted:
                skipped_pasted += 1
                print(
                    f"\n=== skip pasted: {conversation.name} "
                    f"({_accounted()}/{max_chats}) ==="
                )
                continue

            print(f"\n=== {conversation.name} ({_accounted() + 1}/{max_chats}) ===")
            print(f"Preview: {conversation.preview}")

            if not open_conversation(device, conversation, height=height):
                print(f"  defer: {conversation.name} under bottom nav")
                seen_names.discard(key)
                continue
            if not conversation_open_for_match(
                device, conversation.name, height=height
            ):
                print(f"  skip: not in {conversation.name}'s chat after tap")
                seen_names.discard(key)
                open_hinge(device=device, settle_s=0.6, force=True)
                _scroll_matches_to_your_turn(device, width, height)
                continue

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
                result = draft_scored_reply(
                    history,
                    n_candidates=2,
                    recent_drafts=recent_draft_texts,
                    use_model_judge=False,
                )
            except Exception as exception:
                print(f"Draft failed for {conversation.name}: {exception}")
                press_back(device, check_hinge=False)
                ensure_hinge_foreground(device, settle_s=0.5)
                ensure_matches_your_turn(device, width, height)
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
                if conversation_open_for_match(
                    device, conversation.name, height=height
                ):
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
            drafted_names.add(key)
            if pasted:
                already_pasted.add(key)
            if reply:
                recent_draft_texts.insert(0, reply)

            press_back(device, check_hinge=False)
            time.sleep(0.4)
            ensure_hinge_foreground(device, settle_s=0.5)
            ensure_matches_your_turn(device, width, height)

        if page_new == 0:
            stagnant_pages += 1
            print(
                f"  list page {list_page}: no new rows "
                f"(stagnant {stagnant_pages}/{max_stagnant}, "
                f"visible={len(conversations)})"
            )
            if not conversations and stagnant_pages in {1, 3, 6, 9}:
                open_hinge(device=device, settle_s=0.6, force=True)
                _scroll_matches_to_your_turn(device, width, height)
            elif conversations and stagnant_pages in {2, 5, 8}:
                # Stuck on the same visible rows — one hard seek-top then resume.
                print("  stagnant with repeats; re-seek Your turn then scroll")
                ensure_matches_your_turn(device, width, height, seek_top=True)
        else:
            stagnant_pages = 0

        if _accounted() < max_chats and stagnant_pages < max_stagnant:
            if conversations:
                _scroll_matches_list(device, width, height)
                # Extra fling when we've already seen everyone on-screen.
                if page_new == 0:
                    _scroll_matches_list(device, width, height)
            else:
                # Scrolling an empty/non-Matches dump does nothing useful.
                open_hinge(device=device, settle_s=0.6, force=True)
                _scroll_matches_to_your_turn(device, width, height)

    finish_run(
        run_id,
        {
            "drafted": len(drafted_names),
            "skipped_pasted": skipped_pasted,
            "listed": counted,
        },
    )
    print(
        f"\nDone. Drafted replies for {len(drafted_names)} chat(s)"
        + (f", skipped {skipped_pasted} already-pasted" if skipped_pasted else "")
        + "."
    )
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
        "--from-db",
        action="store_true",
        help="With --init-style: learn from SQLite only (no phone/adb).",
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
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-draft matches even if a pasted draft already exists.",
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
        run_init_style(max_chats=max_chats, from_db=args.from_db)
        return

    paste = False if args.no_paste else args.paste
    max_chats = args.max_chats or YOUR_TURN_MAX_CHATS
    run(
        max_chats=max_chats,
        paste=paste,
        process_all=args.all,
        force=args.force,
    )


if __name__ == "__main__":
    main()
