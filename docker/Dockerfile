# docker/Dockerfile
# Local USB adb is awkward in Docker on macOS — prefer host:
#   cd app && ../.venv/bin/python draft_replies.py --init-style
# Docker path works best with wireless adb (DEVICE_IP) via host.docker.internal.
FROM python:3.9-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    libssl-dev \
    libgl1 \
    tesseract-ocr \
    libtesseract-dev \
    adb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY app/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && python -m spacy download en_core_web_sm

COPY app/ /app/

ENV SQLITE_PATH=/data/pitchperfect.db \
    PYTHONUNBUFFERED=1 \
    ADB_SERVER_HOST=host.docker.internal

RUN mkdir -p /data /app/images

VOLUME ["/data", "/app/images"]

# Default: show CLI help. Override with compose/run command.
CMD ["python", "draft_replies.py", "--help"]
