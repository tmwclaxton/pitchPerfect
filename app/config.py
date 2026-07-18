# app/config.py
import os
from dotenv import load_dotenv

load_dotenv()

NANOGPT_API_KEY = os.getenv("NANOGPT_API_KEY")
NANOGPT_BASE_URL = os.getenv("NANOGPT_BASE_URL", "https://nano-gpt.com/api/v1")
# Match AutoCVApply's fast NanoGPT route (deepseek flash :throughput).
NANOGPT_MODEL = os.getenv(
    "NANOGPT_MODEL", "deepseek/deepseek-v4-flash:throughput"
)
NANOGPT_VISION_MODEL = os.getenv(
    "NANOGPT_VISION_MODEL", "openai/gpt-4.1-mini:speed"
)
PROFILE_MIN_ATTRACTIVENESS = float(os.getenv("PROFILE_MIN_ATTRACTIVENESS", "6"))
PROFILE_MIN_SLIMNESS = float(os.getenv("PROFILE_MIN_SLIMNESS", "5"))
PROFILE_IMAGE_COUNT = int(os.getenv("PROFILE_IMAGE_COUNT", "3"))
YOUR_TURN_MAX_CHATS = int(os.getenv("YOUR_TURN_MAX_CHATS", "13"))
YOUR_TURN_PASTE_DRAFTS = os.getenv("YOUR_TURN_PASTE_DRAFTS", "true").lower() in {
    "1",
    "true",
    "yes",
}
STYLE_INIT_MAX_CHATS = int(os.getenv("STYLE_INIT_MAX_CHATS", "25"))
# Override with SQLITE_PATH=/data/pitchperfect.db in Docker.
SQLITE_PATH = os.getenv(
    "SQLITE_PATH",
    os.path.join(os.path.dirname(__file__), "data", "pitchperfect.db"),
)
# UI dump / screenshot capture batches (gitignored under app/data/).
CAPTURES_DIR = os.getenv(
    "CAPTURES_DIR",
    os.path.join(os.path.dirname(__file__), "data", "captures"),
)
CAPTURE_WORKERS = int(os.getenv("CAPTURE_WORKERS", "4"))
# ADB server host (not the phone). Use host.docker.internal from containers.
ADB_SERVER_HOST = os.getenv("ADB_SERVER_HOST", "127.0.0.1")
# Optional phone LAN IP for wireless adb (tcpip 5555). Empty = use USB/listed devices.
DEVICE_IP = os.getenv("DEVICE_IP", "")
