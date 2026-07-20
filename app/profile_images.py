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
    swipe(device, x, y1, y2, PROFILE_SCROLL_DURATION_MS)
    time.sleep(PROFILE_SCROLL_SETTLE_S)


def reverse_profile_scroll(
    device,
    width: int,
    height: int,
    *,
    start_ratio: float = PROFILE_SCROLL_END_RATIO,
    end_ratio: float = PROFILE_SCROLL_START_RATIO,
) -> None:
    """Opposite of `_profile_scroll` — move back toward earlier photos."""
    x = int(width * 0.5)
    y1 = int(height * start_ratio)
    y2 = int(height * end_ratio)
    swipe(device, x, y1, y2, PROFILE_SCROLL_DURATION_MS)
    time.sleep(PROFILE_SCROLL_SETTLE_S)


def navigate_to_profile_image(
    device,
    width: int,
    height: int,
    *,
    target_depth: int,
    current_depth: int,
) -> int:
    """
    Move the Discover profile stack between scroll depths.

    Depth is the number of forward profile scrolls from the first capture
    (see collect_profile_images). Returns the depth landed on.
    """
    if current_depth < 0:
        current_depth = 0
    if target_depth < 0:
        target_depth = 0

    while current_depth > target_depth:
        reverse_profile_scroll(device, width, height)
        current_depth -= 1
    while current_depth < target_depth:
        _profile_scroll(device, width, height)
        current_depth += 1
    return current_depth


def _file_md5(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.md5(handle.read()).hexdigest()


def collect_profile_images(
    device,
    width: int,
    height: int,
    count: int = 3,
    filename_prefix: str = "profile",
) -> Tuple[List[str], List[int], int]:
    """
    Capture `count` screenshots with large vertical gaps between them.

    Returns (paths, capture_depths, final_depth):
      - paths: exactly `count` files in capture order (index 0 = top/first)
      - capture_depths: scroll depth when each path was taken
      - final_depth: depth after collection (where the UI is left)

    Depth lets autoswipe navigate back to the best-scored photo even when
    duplicate-frame retries add extra forward scrolls.
    """
    ensure_images_dir()

    paths: List[str] = []
    capture_depths: List[int] = []
    previous_digest: Optional[str] = None
    depth = 0

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
                depth += 1
                path = capture_screenshot(
                    device, f"{filename_prefix}_{index}_r{attempt}"
                )
                digest = _file_md5(path)
                if digest != previous_digest:
                    break

        paths.append(path)
        capture_depths.append(depth)
        previous_digest = digest

        if index < count - 1:
            _profile_scroll(device, width, height)
            depth += 1

    return paths, capture_depths, depth


def scroll_gap_px(height: int) -> int:
    """Vertical travel in pixels for the default profile scroll."""
    return abs(
        int(height * PROFILE_SCROLL_START_RATIO) - int(height * PROFILE_SCROLL_END_RATIO)
    )
