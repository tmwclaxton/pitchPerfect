import hashlib
import os
import time
from typing import List

from helper_functions import capture_screenshot, swipe


def ensure_images_dir() -> str:
    images_dir = "images"
    os.makedirs(images_dir, exist_ok=True)
    return images_dir


def collect_profile_images(
    device,
    width: int,
    height: int,
    count: int = 3,
    filename_prefix: str = "profile",
) -> List[str]:
    ensure_images_dir()

    x_swipe = int(width * 0.15)
    y1_swipe = int(height * 0.5)
    y2_swipe = int(y1_swipe * 0.75)

    paths: List[str] = []
    seen_hashes = set()

    for index in range(count):
        path = capture_screenshot(device, f"{filename_prefix}_{index}")
        digest = hashlib.md5(open(path, "rb").read()).hexdigest()

        if digest not in seen_hashes:
            seen_hashes.add(digest)
            paths.append(path)

        if index < count - 1:
            swipe(device, x_swipe, y1_swipe, x_swipe, y2_swipe)
            time.sleep(1.2)

    return paths
