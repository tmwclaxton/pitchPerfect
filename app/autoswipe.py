#!/usr/bin/env python3
"""
Discover autoswipe using saved filters + NanoGPT vision composite scoring.

Examples:
  python setup_autoswipe.py --preset asian_baddies
  python autoswipe.py
  python autoswipe.py --max-swipes 5 --no-paste
  python main.py --setup --preset asian_baddies
  python main.py --max-swipes 3
"""

from __future__ import annotations

import argparse
import time
import uuid

from dotenv import load_dotenv

from autoswipe_config import format_settings, load_settings
from data_store import (
    calculate_template_success_rates,
    store_generated_comment,
    store_profile_scores,
)
from db import finish_run, start_run
from device_lock import acquire_device_lock
from helper_functions import (
    capture_screenshot,
    connect_device_auto,
    extract_text_from_image,
    generate_comment,
    get_screen_resolution,
    input_text,
    open_discover,
    open_hinge,
    swipe,
    tap,
)
from profile_images import (
    collect_profile_images,
    ensure_images_dir,
    navigate_to_profile_image,
)
from profile_scorer import (
    format_scores_for_comment,
    like_decision_reason,
    pick_best_image_index,
    score_profile_images,
    vision_failure_scores,
)
from prompt_engine import update_template_weights
from ui_dump import dump_ui_xml, find_nodes, parse_ui_nodes, press_back, tap_bounds

# Discover like-with-comment text (not Matches chat — never auto-sends replies).
DISCOVER_LIKE_COMMENT = "cutie x"


def _discover_nodes(device):
    return parse_ui_nodes(dump_ui_xml(device))


def _first_desc(nodes, *needles: str):
    """First node whose content-desc contains any needle (case-insensitive)."""
    for needle in needles:
        matches = find_nodes(nodes, desc_contains=needle)
        if matches:
            return matches[0]
    return None


def _like_photo_node(nodes, *, prefer_photo: bool):
    """
    Prefer a real photo heart ('Like photo') over prompt likes when commenting
    on the best-scored image.
    """
    matches = find_nodes(nodes, desc_contains="Like photo")
    if not matches:
        return None
    photos = [
        n
        for n in matches
        if "prompt" not in (n.content_desc or "").lower()
    ]
    if prefer_photo and photos:
        return photos[0]
    if photos:
        return photos[0]
    return matches[0]


def _find_like_button(
    device,
    width: int,
    height: int,
    *,
    prefer_photo: bool = False,
    max_hunt: int = 7,
):
    """
    Locate Like photo / Like photo prompt on the current profile card.

    When prefer_photo is set (best-image targeting), keep hunts small so we do
    not scroll away from the chosen photo. Otherwise reverse-scroll a few times
    after deep image collection so a heart control is on-screen again.
    """
    x = width // 2
    hunts = max(1, int(max_hunt))
    for attempt in range(hunts):
        nodes = _discover_nodes(device)
        like_btn = _like_photo_node(nodes, prefer_photo=prefer_photo)
        if like_btn is not None:
            return like_btn
        if hunts == 1:
            break
        # Small local nudge when locked to a photo; broader recovery otherwise.
        if prefer_photo:
            swipe(device, x, int(height * 0.55), x, int(height * 0.40), 220)
        elif attempt < 4:
            swipe(device, x, int(height * 0.28), x, int(height * 0.78), 280)
        else:
            swipe(device, x, int(height * 0.78), x, int(height * 0.28), 280)
        time.sleep(0.85)
    return None


def _type_discover_like_comment(device, comment: str) -> bool:
    """Focus Edit comment on the Discover like sheet and type `comment`."""
    nodes = _discover_nodes(device)
    edit = _first_desc(nodes, "Edit comment")
    if edit is None:
        print("Warning: Edit comment not found on like sheet.")
        return False
    tap_bounds(device, edit.bounds)
    time.sleep(0.35)
    # Clear any leftover draft, then type.
    device.shell("input keycombination 113 29")  # Ctrl+A
    time.sleep(0.08)
    device.shell("input keyevent 67")  # DEL
    time.sleep(0.1)
    input_text(device, comment)
    time.sleep(0.35)
    print(f"Typed Discover like comment: {comment!r}")
    return True


def tap_discover_like(
    device,
    width: int,
    height: int,
    *,
    comment: str = DISCOVER_LIKE_COMMENT,
    best_image_index: int | None = None,
    target_depth: int | None = None,
    current_depth: int | None = None,
) -> bool:
    """
    Like the current Discover profile via accessibility targets.

    When best_image_index/target_depth are set, scroll back to that captured
    photo first so the like-comment lands on the highest-scored image (not
    whatever frame is left after collection). Opens the like sheet, types the
    Discover like comment, then Send like. Never auto-sends Matches chat replies.
    """
    targeting_best = (
        best_image_index is not None
        and target_depth is not None
        and current_depth is not None
    )
    if targeting_best:
        landed = navigate_to_profile_image(
            device,
            width,
            height,
            target_depth=int(target_depth),
            current_depth=int(current_depth),
        )
        print(
            f"Navigated to best photo index {best_image_index} "
            f"(scroll depth {landed}) before like-comment"
        )

    like_btn = _find_like_button(
        device,
        width,
        height,
        prefer_photo=bool(targeting_best),
        max_hunt=3 if targeting_best else 7,
    )
    if like_btn is not None:
        tap_bounds(device, like_btn.bounds)
        print(f"Like tapped via UI: {like_btn.content_desc} {like_btn.bounds}")
    else:
        x, y = int(width * 0.86), int(height * 0.58)
        tap(device, x, y)
        print(f"Like tapped at fallback coords: {x}, {y}")

    time.sleep(1.2)
    nodes = _discover_nodes(device)
    send = _first_desc(nodes, "Send like")
    if send is None:
        # Sheet may need a moment, or like tap missed — retry once on any like.
        time.sleep(0.6)
        nodes = _discover_nodes(device)
        send = _first_desc(nodes, "Send like")
    if send is None:
        print("Warning: Send like not found after like tap; pressing back.")
        press_back(device, settle_s=0.4, check_hinge=False)
        return False

    if comment:
        _type_discover_like_comment(device, comment)
        nodes = _discover_nodes(device)
        send = _first_desc(nodes, "Send like") or send

    tap_bounds(device, send.bounds)
    print(f"Send like tapped: {send.bounds}")
    time.sleep(1.2)
    return True


def tap_discover_pass(device, width: int, height: int) -> bool:
    """Pass/skip the current Discover profile via Skip button when available."""
    nodes = _discover_nodes(device)
    skip = _first_desc(nodes, "Skip ")
    if skip is None:
        # Skip sits near the bottom; reverse-scroll once if we are mid-profile.
        swipe(
            device,
            width // 2,
            int(height * 0.30),
            width // 2,
            int(height * 0.75),
            280,
        )
        time.sleep(0.8)
        nodes = _discover_nodes(device)
        skip = _first_desc(nodes, "Skip ")
    if skip is not None:
        tap_bounds(device, skip.bounds)
        print(f"Pass tapped via UI: {skip.content_desc} {skip.bounds}")
        time.sleep(1.0)
        return True

    x, y = int(width * 0.12), int(height * 0.83)
    tap(device, x, y)
    print(f"Pass tapped at fallback coords: {x}, {y}")
    time.sleep(1.0)
    return False


def run_autoswipe(
    *,
    max_swipes: int | None = None,
    paste_comment: bool | None = None,
) -> None:
    load_dotenv()
    settings = load_settings()
    swipes = max_swipes if max_swipes is not None else settings.max_swipes
    do_paste = (
        settings.paste_comment if paste_comment is None else paste_comment
    )

    print("Autoswipe settings:\n" + format_settings(settings))
    print(f"\nRunning up to {swipes} swipe(s); paste_comment={do_paste}\n")

    acquire_device_lock(owner="autoswipe")
    device = connect_device_auto()
    if not device:
        return

    ensure_images_dir()
    width, height = get_screen_resolution(device)

    open_hinge(device=device)
    open_discover(device, width, height)

    previous_profile_text = ""
    success_rates = calculate_template_success_rates()
    update_template_weights(success_rates)

    run_id = start_run(
        "autoswipe",
        {
            "preset": settings.preset,
            "max_swipes": swipes,
            "min_composite": settings.min_composite,
            "ethnicity_preference": settings.ethnicity_preference,
        },
    )
    liked = 0
    passed = 0

    try:
        for index in range(swipes):
            print(f"\n--- Profile {index + 1}/{swipes} ---")
            image_paths, capture_depths, final_depth = collect_profile_images(
                device,
                width,
                height,
                count=settings.profile_image_count,
            )
            screenshot_path = (
                image_paths[0]
                if image_paths
                else capture_screenshot(device, "screen")
            )

            current_profile_text = extract_text_from_image(screenshot_path).strip()
            if not current_profile_text:
                print("Warning: OCR returned empty text.")

            try:
                scores = score_profile_images(image_paths, settings=settings)
            except Exception as exception:
                print(f"Vision scoring failed: {exception}")
                scores = vision_failure_scores(
                    f"Vision scoring failed: {exception}",
                    image_count=len(image_paths),
                )

            best_idx = pick_best_image_index(scores, len(image_paths))
            scores["best_image_index"] = best_idx
            if capture_depths and 0 <= best_idx < len(capture_depths):
                best_depth = int(capture_depths[best_idx])
            else:
                best_depth = 0
            per_image = scores.get("image_scores") or []
            per_image_bits = ", ".join(
                f"{row.get('index')}:{row.get('attractiveness')}"
                for row in per_image
                if isinstance(row, dict)
            )

            print(
                "Vision scores =>",
                f"composite={scores.get('composite')},",
                f"attractiveness={scores['attractiveness']},",
                f"slimness={scores['slimness']},",
                f"quirkiness={scores['quirkiness']},",
                f"ethnicity_fit={scores.get('ethnicity_fit')},",
                f"best_image_index={best_idx}",
                f"best_depth={best_depth}",
                f"image_attractiveness=[{per_image_bits}]",
                f"notes={scores['notes']}",
            )

            comment_id = str(uuid.uuid4())
            like_profile, decision_reason = like_decision_reason(scores, settings)
            decision = "like" if like_profile else "pass"
            print(f"Decision: {decision_reason}")

            store_profile_scores(
                comment_id=comment_id,
                profile_text=current_profile_text,
                scores=scores,
                decision=decision,
                image_paths=image_paths,
            )

            if like_profile:
                liked += 1
                # Discover likes always send DISCOVER_LIKE_COMMENT on the like sheet.
                # Matches chat replies are never auto-sent.
                ui_comment = DISCOVER_LIKE_COMMENT
                if do_paste:
                    # Optional local-only vision note (not typed into Hinge).
                    generated = generate_comment(
                        current_profile_text,
                        vision_notes=format_scores_for_comment(scores),
                    )
                    if generated:
                        print(f"Generated Comment (local only): {generated}")
                store_generated_comment(
                    comment_id=comment_id,
                    profile_text=current_profile_text,
                    generated_comment=ui_comment,
                    style_used="discover_like",
                    profile_scores=scores,
                    decision=decision,
                    image_paths=image_paths,
                )
                tap_discover_like(
                    device,
                    width,
                    height,
                    comment=ui_comment,
                    best_image_index=best_idx,
                    target_depth=best_depth,
                    current_depth=final_depth,
                )
            else:
                passed += 1
                print(f"Pass — {decision_reason}")
                tap_discover_pass(device, width, height)

            previous_profile_text = current_profile_text
            time.sleep(1.0)
    finally:
        finish_run(
            run_id,
            {"liked": liked, "passed": passed, "swipes": liked + passed},
        )

    success_rates = calculate_template_success_rates()
    update_template_weights(success_rates)
    print("Final success rates:", success_rates)
    print(f"Autoswipe finished. liked={liked} passed={passed}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run Discover autoswipe with saved scoring filters."
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Configure filters (delegates to setup_autoswipe.py).",
    )
    parser.add_argument(
        "--preset",
        type=str,
        default=None,
        help="With --setup: apply a named preset.",
    )
    parser.add_argument(
        "--max-swipes",
        type=int,
        default=None,
        help="Override max swipes for this run.",
    )
    parser.add_argument(
        "--paste",
        action="store_true",
        default=None,
        help="Also generate/store a vision comment locally (Discover UI still uses cutie x).",
    )
    parser.add_argument(
        "--no-paste",
        action="store_true",
        help="Skip local vision comment generation (Discover likes still send cutie x).",
    )
    parser.add_argument(
        "--show-settings",
        action="store_true",
        help="Print saved settings and exit.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.setup:
        from setup_autoswipe import main as setup_main

        setup_argv = []
        if args.preset:
            setup_argv.extend(["--preset", args.preset])
        else:
            setup_argv.append("--interactive")
        raise SystemExit(setup_main(setup_argv))

    if args.show_settings:
        print(format_settings(load_settings()))
        return

    paste = None
    if args.no_paste:
        paste = False
    elif args.paste:
        paste = True

    run_autoswipe(max_swipes=args.max_swipes, paste_comment=paste)


if __name__ == "__main__":
    main()
