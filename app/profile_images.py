import hashlib
import os
import time
from typing import List, Optional, Tuple

from helper_functions import capture_screenshot, swipe

# Large vertical travel so consecutive Discover photos are different frames.
# Old gap (~12% of height) produced near-duplicate screenshots.
PROFILE_SCROLL_START_RATIO = 0.82
PROFILE_SCROLL_END_RATIO = 0.22
PROFILE_SCROLL_DURATION_MS = 280
PROFILE_SCROLL_SETTLE_S = 1.35
# Extra swipe attempts when the next frame still looks like the previous.
MAX_SCROLL_RETRIES = 2


def ensure_images_dir() -> str:
    images_dir = "images"
    os.makedirs(images_dir, exist_ok=True)
    return images_dir


def _profile_scroll(
    device,
    width: int,
    height: int,
    *,
    start_ratio: float = PROFILE_SCROLL_START_RATIO,
    end_ratio: float = PROFILE_SCROLL_END_RATIO,
) -> None:
    """Big upward swipe through the profile photo / prompt stack."""
    x = int(width * 0.5)
    y1 = int(height * start_ratio)
    y2 = int(height * end_ratio)
    swipe(device, x, y1, x, y2, PROFILE_SCROLL_DURATION_MS)
    time.sleep(PROFILE_SCROLL_SETTLE_S)


def _file_md5(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.md5(handle.read()).hexdigest()


def collect_profile_images(
    device,
    width: int,
    height: int,
    count: int = 3,
    filename_prefix: str = "profile",
) -> List[str]:
    """
    Capture `count` screenshots with large vertical gaps between them.

    Swipes ~60% of screen height each step (vs the previous short nudge) so
    vision sees distinct photos instead of overlapping crops of the same one.
    """
    ensure_images_dir()

    paths: List[str] = []
    seen_hashes = set()
    previous_digest: Optional[str] = None

    for index in range(count):
        path = capture_screenshot(device, f"{filename_prefix}_{index}")
        digest = _file_md5(path)

        # If this frame matches the last one, scroll harder and recapture.
        if previous_digest is not None and digest == previous_digest:
            for attempt in range(MAX_SCROLL_RETRIES):
                _profile_scroll(
                    device,
                    width,
                    height,
                    start_ratio=0.88,
                    end_ratio=0.15,
                )
                path = capture_screenshot(
                    device, f"{filename_prefix}_{index}_r{attempt}"
                )
                digest = _file_md5(path)
                if digest != previous_digest:
                    break

        if digest not in seen_hashes:
            seen_hashes.add(digest)
            paths.append(path)
            previous_digest = digest
        elif paths:
            # Keep at least one path even if duplicate; prefer uniqueness.
            previous_digest = digest

        if index < count - 1:
            _profile_scroll(device, width, height)

    return paths


def scroll_gap_px(height: int) -> int:
    """Vertical travel in pixels for the default profile scroll."""
    return abs(
        int(height * PROFILE_SCROLL_START_RATIO) - int(height * PROFILE_SCROLL_END_RATIO)
    )
