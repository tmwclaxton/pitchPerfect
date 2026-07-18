# Pitch Perfect

This project also demonstrates how to automate interactions with Hinge (a dating app) using a combination of the following tools and techniques:

- **ADB (Android Debug Bridge)**: Automate device actions such as taps, swipes, and text input.
- **Computer Vision (OpenCV)**: Detect and locate UI elements on the screen using feature-based and template matching methods.
- **OCR (Tesseract via pytesseract)**: Extract text from screenshots to analyze profile descriptions or other textual content.
- **LLM (NanoGPT)**: Generate personalized, human-like comments based on extracted text content.

By integrating these components, the script can make automated decisions (like or dislike profiles) and even respond with a custom-generated pickup line or comment.

## Demo

[![PitchPerfect Demo: ](https://img.youtube.com/vi/VgES1_QHrR8/maxresdefault.jpg)](https://youtube.com/shorts/VgES1_QHrR8)

## Features

- **Connect to Android Device**: Establish a connection to your Android device over ADB and retrieve screen resolution.
- **Capture Screenshots**: Save current device screen state to an image file.
- **UI Element Detection**: Locate buttons or icons using ORB feature matching and fallback template matching.
- **Text Extraction**: Use Tesseract OCR to read text content from on-screen images.
- **Comment Generation**: Use NanoGPT to create personalized, one-line comments based on the extracted profile text.
- **Automated Actions**: Simulate user input (taps, swipes, text entry) to interact with the app, such as liking or disliking profiles and inputting custom messages.

## Requirements

- **Python 3.x**
- **ADB**:  
  Install the [Android SDK Platform Tools](https://developer.android.com/studio/releases/platform-tools) and ensure `adb` is accessible from your PATH.
- **Device Setup**:

  - Enable Developer Options and USB Debugging on your Android device.
  - Authorize your computer for USB debugging when prompted.

- **Python Libraries**:

  - [pure-python-adb (ppadb)](https://pypi.org/project/pure-python-adb/) for ADB interactions:
    ```bash
    pip install pure-python-adb
    ```
  - [OpenCV](https://pypi.org/project/opencv-python/) for computer vision:
    ```bash
    pip install opencv-python
    ```
  - [Pillow](https://pypi.org/project/Pillow/) for image handling:
    ```bash
    pip install pillow
    ```
  - [pytesseract](https://pypi.org/project/pytesseract/) for OCR (requires Tesseract OCR engine installed on your system):
    ```bash
    pip install pytesseract
    ```
  - [python-dotenv](https://pypi.org/project/python-dotenv/) for environment variables:
    ```bash
    pip install python-dotenv
    ```
  - [requests](https://pypi.org/project/requests/) for NanoGPT API requests:
    ```bash
    pip install requests
    ```

- **Tesseract OCR Engine**:
  - **Windows**: [Download the installer here](https://github.com/UB-Mannheim/tesseract/wiki).
  - **macOS/Linux**: Install via Homebrew (`brew install tesseract`) or your package manager.

## Setup

1. **Add your NanoGPT configuration**:
   Copy `.env-template` to `.env`, then add your NanoGPT key:
   ```env
   NANOGPT_API_KEY=your-api-key
   NANOGPT_BASE_URL=https://nano-gpt.com/api/v1
   NANOGPT_MODEL=deepseek/deepseek-v4-flash:throughput
   ```

2. **Install deps** (host / recommended for USB adb on macOS):
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r app/requirements.txt
   ```

## Chat history + Your Turn drafting

Data lives in SQLite (`app/data/pitchperfect.db` locally, `/data/pitchperfect.db` in Docker): `matches`, `messages`, `profile_fields`, drafts, learned style, run metadata. Schema is versioned under `app/migrations/`.

**Host (preferred with USB):**
```bash
export PATH="/opt/homebrew/bin:$PATH"
cd app
../.venv/bin/python migrate.py                             # apply migrations
../.venv/bin/python sync_chats.py                          # ALL Matches chats + profiles -> SQLite
../.venv/bin/python sync_chats.py --max-chats 5            # smoke sync
../.venv/bin/python sync_chats.py --skip-profile           # chats only
../.venv/bin/python sync_chats.py --force                  # re-scrape even if fresh in DB
../.venv/bin/python sync_chats.py --fresh-hours 12         # skip matches synced in last 12h
../.venv/bin/python draft_replies.py --sync-history        # same as sync_chats
../.venv/bin/python draft_replies.py --init-style --from-db  # learn style from SQLite (no phone)
../.venv/bin/python draft_replies.py --init-style          # re-scrape Matches on device, then learn
../.venv/bin/python draft_replies.py --max-chats 2         # smoke draft
../.venv/bin/python draft_replies.py --all                 # draft all Your Turn (paste, never send)
../.venv/bin/python draft_replies.py --all --no-paste      # save only
```

**Docker** (wireless adb works better than USB-on-mac):
```bash
# On host: adb tcpip 5555 && adb connect PHONE_IP:5555
# Ensure host adb server is reachable (ADB_SERVER_HOST=host.docker.internal).
docker compose --profile tools build
# Named volume `pitchperfect-data` stores /data/pitchperfect.db by default.
docker compose --profile tools run --rm pitchperfect python sync_chats.py
# Use the same host SQLite dir as local runs:
SQLITE_BIND=./app/data docker compose --profile tools run --rm pitchperfect \
  python draft_replies.py --init-style --from-db
docker compose --profile tools run --rm pitchperfect python draft_replies.py --all --no-paste
```

Limitations: macOS USB devices are attached to the host adb server; containers cannot see them unless you forward adb (`adb -a nodaemon server`) and set `ADB_SERVER_HOST=host.docker.internal`. Composer drafts (unsent EditText text) are ignored during sync.
