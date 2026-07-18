# app/device_lock.py
"""Exclusive flock so only one process drives the phone at a time."""

from __future__ import annotations

import atexit
import fcntl
import os
import sys
from typing import Optional, TextIO

from config import CAPTURES_DIR
from migrate import ensure_parent_dir

_lock_fh: Optional[TextIO] = None


def lock_path() -> str:
    ensure_parent_dir(os.path.join(CAPTURES_DIR, ".keep"))
    return os.path.join(CAPTURES_DIR, "sync_device.lock")


def acquire_device_lock(*, owner: str = "sync") -> None:
    """
    Block other sync/draft processes from using the device concurrently.
    Exits the process if another holder already owns the lock.
    """
    global _lock_fh
    if _lock_fh is not None:
        return
    path = lock_path()
    fh = open(path, "a+", encoding="utf-8")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        holder = ""
        try:
            fh.seek(0)
            holder = (fh.read() or "").strip()
        except Exception:
            pass
        fh.close()
        detail = f" ({holder})" if holder else ""
        print(
            f"ABORT: another process already owns the device lock{detail}.\n"
            f"  lockfile: {path}\n"
            "  Kill the other sync_chats/draft_replies run before starting another."
        )
        sys.exit(2)
    fh.seek(0)
    fh.truncate()
    fh.write(f"pid={os.getpid()} owner={owner}\n")
    fh.flush()
    _lock_fh = fh
    atexit.register(release_device_lock)
    print(f"Device lock acquired (pid={os.getpid()}, owner={owner})")


def release_device_lock() -> None:
    global _lock_fh
    if _lock_fh is None:
        return
    try:
        fcntl.flock(_lock_fh.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        _lock_fh.close()
    except Exception:
        pass
    _lock_fh = None
