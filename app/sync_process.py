# app/sync_process.py
"""
Phase B: parse captured UI dumps offline (threaded) into SQLite.

Uses the same UIAutomator parsers as live sync — no OCR required.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from capture_store import (
    CaptureAsset,
    CaptureRun,
    finish_capture_run,
    load_capture_run,
    mark_assets_processed,
    read_asset_xml,
    set_capture_run_status,
    write_manifest,
)
from config import CAPTURE_WORKERS
from db import (
    name_key,
    start_run,
    finish_run,
    sync_stats,
    upsert_match_history,
    upsert_profile_fields,
)
from profile_scraper import extract_profile_fields_from_nodes, profile_fields_as_dicts
from style_learner import messages_as_dicts
from ui_dump import parse_ui_nodes
from your_turn import ChatMessage, _message_key, _parse_messages_from_nodes


_CHROME_SENDERS = {
    "you",
    "prompt",
    "chat",
    "profile",
    "duration",
    "photo prompt",
    "voice note",
}


def _is_chrome_sender(sender: str) -> bool:
    """UI chrome labels that are not another match's name."""
    lowered = (sender or "").strip().lower()
    if not lowered or lowered in _CHROME_SENDERS:
        return True
    if lowered.endswith(" prompt") or lowered.startswith("duration"):
        return True
    return False


def _history_belongs_to_other_match(
    messages: Sequence[ChatMessage], match_name: str
) -> bool:
    want = (match_name or "").strip().lower()
    if not want or not messages:
        return False
    for message in messages:
        text = (message.text or "").strip()
        sender = (message.sender or "").strip().lower()
        if (
            sender
            and sender != want
            and not _is_chrome_sender(sender)
            and len(sender) >= 2
        ):
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


def _merge_chat_messages(xml_texts: Sequence[str]) -> List[ChatMessage]:
    """
    Merge chat frames oldest→newest the same way live scroll capture does:
    earlier frames are newer (viewport); later scroll frames reveal older msgs.
    """
    ordered: List[ChatMessage] = []
    seen: Set[str] = set()
    for xml_text in xml_texts:
        nodes = parse_ui_nodes(xml_text)
        on_screen, _ = _parse_messages_from_nodes(nodes)
        new_messages = []
        for message in on_screen:
            key = _message_key(message.sender, message.text)
            if key in seen:
                continue
            seen.add(key)
            new_messages.append(message)
        if not new_messages:
            continue
        # Frames after upward scrolls expose older messages → prepend.
        if ordered:
            ordered = new_messages + ordered
        else:
            ordered = new_messages
    return ordered


def _merge_profile_fields(xml_texts: Sequence[str], match_name: str) -> List[dict]:
    ordered = []
    seen: Set[str] = set()
    for xml_text in xml_texts:
        nodes = parse_ui_nodes(xml_text)
        batch = extract_profile_fields_from_nodes(nodes, match_name=match_name)
        for field in batch:
            key = (
                f"{field.field_type}|{(field.label or '').lower()}|"
                f"{field.text_content.lower()}"
            )
            if key in seen:
                continue
            seen.add(key)
            ordered.append(field)
    return profile_fields_as_dicts(ordered)


def _assets_for_match(
    run: CaptureRun, match_key: str
) -> Tuple[List[CaptureAsset], List[CaptureAsset]]:
    chat: List[CaptureAsset] = []
    profile: List[CaptureAsset] = []
    for asset in run.assets:
        if name_key(asset.match_name or "") != match_key:
            continue
        if asset.kind == "chat":
            chat.append(asset)
        elif asset.kind == "profile":
            profile.append(asset)
    chat.sort(key=lambda item: (item.sequence, item.id or 0))
    profile.sort(key=lambda item: (item.sequence, item.id or 0))
    return chat, profile


def process_one_match(
    run: CaptureRun,
    match_key: str,
    match_meta: Dict[str, Any],
    *,
    sync_run_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Parse one match's captured dumps and upsert into SQLite."""
    match_name = match_meta.get("name") or match_key
    chat_assets, profile_assets = _assets_for_match(run, match_key)
    asset_ids = [
        int(asset.id)
        for asset in (*chat_assets, *profile_assets)
        if asset.id is not None
    ]
    result: Dict[str, Any] = {
        "match_name": match_name,
        "chat_frames": len(chat_assets),
        "profile_frames": len(profile_assets),
        "messages_inserted": 0,
        "message_count": 0,
        "profile_inserted": 0,
        "profile_count": 0,
        "skipped": False,
        "error": None,
    }
    if not chat_assets and not profile_assets:
        result["skipped"] = True
        result["error"] = "no captured frames"
        return result

    # Resume: skip matches whose assets were already processed successfully.
    actionable = [a for a in (*chat_assets, *profile_assets)]
    if actionable and all(
        (a.process_status or "") in {"done", "skipped"} for a in actionable
    ):
        result["skipped"] = True
        result["error"] = f"already processed ({actionable[0].process_status})"
        return result

    try:
        chat_xmls = [read_asset_xml(run, asset) for asset in chat_assets]
        messages = _merge_chat_messages(chat_xmls)
        if _history_belongs_to_other_match(messages, match_name):
            mark_assets_processed(
                asset_ids, status="skipped", error="cross-thread messages"
            )
            result["skipped"] = True
            result["error"] = "cross-thread messages"
            return result

        history = upsert_match_history(
            match_name,
            messages_as_dicts(messages),
            section=match_meta.get("section") or "unknown",
            is_new_match=bool(match_meta.get("is_new_match")),
            list_preview=match_meta.get("preview") or None,
            run_id=sync_run_id,
            meta={"capture_run_id": run.id},
        )
        match_id = int(history["match_id"])
        result["messages_inserted"] = int(history["inserted"])
        result["message_count"] = int(history["message_count"])

        if profile_assets:
            profile_xmls = [read_asset_xml(run, asset) for asset in profile_assets]
            fields = _merge_profile_fields(profile_xmls, match_name)
            if fields:
                inserted, total = upsert_profile_fields(match_id, fields)
                result["profile_inserted"] = inserted
                result["profile_count"] = total

        mark_assets_processed(asset_ids, status="done")
    except Exception as exception:
        mark_assets_processed(asset_ids, status="error", error=str(exception))
        result["error"] = str(exception)
    return result


def run_process(
    *,
    run_id: Optional[int] = None,
    root_dir: Optional[str] = None,
    workers: int = CAPTURE_WORKERS,
) -> Dict[str, Any]:
    """Phase B entrypoint: threaded offline parse → SQLite."""
    run = load_capture_run(run_id=run_id, root_dir=root_dir)
    set_capture_run_status(run.id, "processing")
    print(f"Processing capture run {run.id} from {run.root_dir}")
    print(f"Workers: {workers}")

    # Build match list from meta, falling back to asset names.
    matches: Dict[str, Dict[str, Any]] = dict(run.matches or {})
    for asset in run.assets:
        if not asset.match_name:
            continue
        key = name_key(asset.match_name)
        if key not in matches:
            matches[key] = {
                "name": asset.match_name,
                "section": "unknown",
                "preview": "",
                "is_new_match": False,
            }

    if not matches:
        print("No match captures to process.")
        finish_capture_run(run, status="processed", meta_update={"matches": 0})
        return {"matches": 0}

    sync_run_id = start_run(
        "process_capture",
        {
            "capture_run_id": run.id,
            "root_dir": run.root_dir,
            "workers": workers,
            "match_count": len(matches),
        },
    )

    results: List[Dict[str, Any]] = []
    workers = max(1, int(workers))

    def _job(item: Tuple[str, Dict[str, Any]]) -> Dict[str, Any]:
        key, meta = item
        return process_one_match(run, key, meta, sync_run_id=sync_run_id)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_job, item): item[0] for item in sorted(matches.items())
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                outcome = future.result()
            except Exception as exception:
                outcome = {
                    "match_name": key,
                    "error": str(exception),
                    "skipped": True,
                    "messages_inserted": 0,
                    "profile_inserted": 0,
                    "message_count": 0,
                    "profile_count": 0,
                    "chat_frames": 0,
                    "profile_frames": 0,
                }
            results.append(outcome)
            name = outcome.get("match_name") or key
            if outcome.get("error") and outcome.get("skipped"):
                print(f"  skip {name}: {outcome['error']}")
            elif outcome.get("error"):
                print(f"  error {name}: {outcome['error']}")
            else:
                print(
                    f"  {name}: msgs={outcome.get('message_count')} "
                    f"(+{outcome.get('messages_inserted')}) "
                    f"profile={outcome.get('profile_count')} "
                    f"(+{outcome.get('profile_inserted')}) "
                    f"[chat_frames={outcome.get('chat_frames')} "
                    f"profile_frames={outcome.get('profile_frames')}]"
                )

    msg_inserted = sum(int(item.get("messages_inserted") or 0) for item in results)
    profile_inserted = sum(int(item.get("profile_inserted") or 0) for item in results)
    stats = sync_stats()
    finish_run(
        sync_run_id,
        {
            "capture_run_id": run.id,
            "matches_processed": len(results),
            "messages_inserted": msg_inserted,
            "profile_fields_inserted": profile_inserted,
            "db_matches": stats["matches"],
            "db_messages": stats["messages"],
            "db_profile_fields": stats.get("profile_fields", 0),
        },
    )
    # Refresh asset statuses on the in-memory run for manifest.
    refreshed = load_capture_run(run_id=run.id)
    refreshed.status = "processed"
    write_manifest(refreshed)
    finish_capture_run(
        refreshed,
        status="processed",
        meta_update={
            "matches_processed": len(results),
            "messages_inserted": msg_inserted,
            "profile_fields_inserted": profile_inserted,
        },
    )

    print("\n--- process complete ---")
    print(f"Matches processed: {len(results)}")
    print(f"New message rows: {msg_inserted}")
    print(f"New profile field rows: {profile_inserted}")
    print(
        f"DB totals: {stats['matches']} matches, "
        f"{stats['messages']} messages, "
        f"{stats.get('profile_fields', 0)} profile fields"
    )
    return {
        "matches": len(results),
        "messages_inserted": msg_inserted,
        "profile_fields_inserted": profile_inserted,
        "results": results,
        "capture_run_id": run.id,
        "root_dir": run.root_dir,
    }
