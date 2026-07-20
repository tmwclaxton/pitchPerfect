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
    open_discover,
    open_hinge,
    tap,
)
from profile_images import collect_profile_images, ensure_images_dir
from profile_scorer import (
    format_scores_for_comment,
    score_profile_images,
    should_like_profile,
)
from prompt_engine import update_template_weights


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

    x_like_button = int(width * 0.90)
    y_like_button = int(height * 0.67)
    x_dislike_button = int(width * 0.15)
    y_dislike_button = int(height * 0.85)

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
            image_paths = collect_profile_images(
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
                scores = {
                    "attractiveness": 0,
                    "slimness": 0,
                    "quirkiness": 0,
                    "ethnicity_fit": 0,
                    "composite": 0,
                    "notes": "Vision scoring failed",
                }

            print(
                "Vision scores =>",
                f"composite={scores.get('composite')},",
                f"attractiveness={scores['attractiveness']},",
                f"slimness={scores['slimness']},",
                f"quirkiness={scores['quirkiness']},",
                f"ethnicity_fit={scores.get('ethnicity_fit')},",
                f"notes={scores['notes']}",
            )

            comment_id = str(uuid.uuid4())
            like_profile = should_like_profile(scores, settings)
            decision = "like" if like_profile else "pass"

            store_profile_scores(
                comment_id=comment_id,
                profile_text=current_profile_text,
                scores=scores,
                decision=decision,
                image_paths=image_paths,
            )

            if like_profile:
                liked += 1
                comment = None
                if do_paste:
                    comment = generate_comment(
                        current_profile_text,
                        vision_notes=format_scores_for_comment(scores),
                    ) or "Hey, I'd love to meet up!"
                    print(f"Generated Comment: {comment}")
                    store_generated_comment(
                        comment_id=comment_id,
                        profile_text=current_profile_text,
                        generated_comment=comment,
                        style_used="vision",
                        profile_scores=scores,
                        decision=decision,
                        image_paths=image_paths,
                    )
                else:
                    print("Like without pasting a comment (--no-paste).")

                tap(device, x_like_button, y_like_button)
                print("Like tapped at:", x_like_button, y_like_button)
                # Comment paste into Hinge like composer is best-effort / optional;
                # we never auto-send chat messages.
            else:
                passed += 1
                if (
                    previous_profile_text == current_profile_text
                    and current_profile_text != ""
                ):
                    print("Pass (same profile encountered again)")
                else:
                    print(
                        f"Pass (composite={scores.get('composite')} "
                        f"< {settings.min_composite} or below floors)"
                    )
                tap(device, x_dislike_button, y_dislike_button)
                print("Pass tapped at:", x_dislike_button, y_dislike_button)

            previous_profile_text = current_profile_text
            time.sleep(2)
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
        help="Generate/store a like comment (never auto-sends chats).",
    )
    parser.add_argument(
        "--no-paste",
        action="store_true",
        help="Like/pass only; skip comment generation.",
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
