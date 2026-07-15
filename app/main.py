# app/main.py

import time
import uuid
from dotenv import load_dotenv

from prompt_engine import update_template_weights
from config import PROFILE_IMAGE_COUNT

from helper_functions import (
    connect_device,
    get_screen_resolution,
    open_hinge,
    open_discover,
    capture_screenshot,
    extract_text_from_image,
    generate_comment,
    tap,
)
from profile_images import collect_profile_images, ensure_images_dir
from profile_scorer import (
    format_scores_for_comment,
    score_profile_images,
    should_like_profile,
)
from data_store import (
    store_generated_comment,
    store_profile_scores,
    calculate_template_success_rates,
)


def main():
    device = connect_device("127.0.0.1")
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

    for _ in range(10):
        image_paths = collect_profile_images(
            device,
            width,
            height,
            count=PROFILE_IMAGE_COUNT,
        )
        screenshot_path = image_paths[0] if image_paths else capture_screenshot(
            device, "screen"
        )

        current_profile_text = extract_text_from_image(screenshot_path).strip()
        if not current_profile_text:
            print("Warning: OCR returned empty text.")

        try:
            scores = score_profile_images(image_paths)
        except Exception as exception:
            print(f"Vision scoring failed: {exception}")
            scores = {
                "attractiveness": 0,
                "slimness": 0,
                "quirkiness": 0,
                "notes": "Vision scoring failed",
            }

        print(
            "Vision scores =>",
            f"attractiveness={scores['attractiveness']},",
            f"slimness={scores['slimness']},",
            f"quirkiness={scores['quirkiness']},",
            f"notes={scores['notes']}",
        )

        comment_id = str(uuid.uuid4())
        like_profile = should_like_profile(scores)
        decision = "like" if like_profile else "pass"

        store_profile_scores(
            comment_id=comment_id,
            profile_text=current_profile_text,
            scores=scores,
            decision=decision,
            image_paths=image_paths,
        )

        if like_profile:
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

            tap(device, x_like_button, y_like_button)
            print("Like tapped at:", x_like_button, y_like_button)
        else:
            if (
                previous_profile_text == current_profile_text
                and current_profile_text != ""
            ):
                print("Pass (same profile encountered again)")
            else:
                print("Pass (below vision thresholds)")

            tap(device, x_dislike_button, y_dislike_button)
            print("Pass tapped at:", x_dislike_button, y_dislike_button)

        previous_profile_text = current_profile_text
        time.sleep(2)

    success_rates = calculate_template_success_rates()
    update_template_weights(success_rates)
    print("Final success rates:", success_rates)
    print("Main loop finished.")


if __name__ == "__main__":
    main()
