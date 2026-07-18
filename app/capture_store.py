# app/capture_store.py
"""Filesystem + SQLite scaffolding for two-phase Hinge capture/process sync."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from config import CAPTURES_DIR
from db import connect, name_key
from migrate import ensure_parent_dir


def _utc_now() -> str:
    return datetime.utcnow().isoformat()


def _slug(name: str) -> str:
    cleaned = re.sub(r"[^\w\-]+", "_", (name or "").strip(), flags=re.UNICODE)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return (cleaned or "unknown")[:80]


@dataclass
class CaptureAsset:
    id: Optional[int]
    run_id: int
    match_name: Optional[str]
    kind: str
    sequence: int
    xml_path: Optional[str]
    image_path: Optional[str]
    captured_at: str
    process_status: str = "pending"
    process_error: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def match_name_key(self) -> Optional[str]:
        if not self.match_name:
            return None
        return name_key(self.match_name)


@dataclass
class CaptureRun:
    id: int
    root_dir: str
    started_at: str
    finished_at: Optional[str] = None
    status: str = "capturing"
    meta: Dict[str, Any] = field(default_factory=dict)
    assets: List[CaptureAsset] = field(default_factory=list)
    matches: Dict[str, Dict[str, Any]] = field(default_factory=dict)


def captures_root() -> str:
    ensure_parent_dir(os.path.join(CAPTURES_DIR, ".keep"))
    return CAPTURES_DIR


def create_capture_run(meta: Optional[Dict[str, Any]] = None) -> CaptureRun:
    """Create DB row + on-disk directory for a new capture batch."""
    started = _utc_now()
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO capture_runs (started_at, root_dir, status, meta_json)
            VALUES (?, ?, 'capturing', ?)
            """,
            (started, "", json.dumps(meta or {})),
        )
        run_id = int(cur.lastrowid)
        root = os.path.join(captures_root(), f"{stamp}_run{run_id}")
        os.makedirs(os.path.join(root, "assets"), exist_ok=True)
        conn.execute(
            "UPDATE capture_runs SET root_dir = ? WHERE id = ?",
            (root, run_id),
        )
    run = CaptureRun(
        id=run_id,
        root_dir=root,
        started_at=started,
        status="capturing",
        meta=dict(meta or {}),
    )
    write_manifest(run)
    return run


def finish_capture_run(
    run: CaptureRun,
    *,
    status: str = "captured",
    meta_update: Optional[Dict[str, Any]] = None,
) -> CaptureRun:
    finished = _utc_now()
    if meta_update:
        run.meta.update(meta_update)
    run.finished_at = finished
    run.status = status
    with connect() as conn:
        conn.execute(
            """
            UPDATE capture_runs
            SET finished_at = ?, status = ?, meta_json = ?
            WHERE id = ?
            """,
            (finished, status, json.dumps(run.meta), run.id),
        )
    write_manifest(run)
    return run


def set_capture_run_status(run_id: int, status: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE capture_runs SET status = ? WHERE id = ?",
            (status, run_id),
        )


def persist_run_progress(run: CaptureRun) -> None:
    """Flush run.meta + matches into SQLite and manifest.json (resume cursor)."""
    with connect() as conn:
        conn.execute(
            """
            UPDATE capture_runs
            SET status = ?, meta_json = ?
            WHERE id = ?
            """,
            (run.status, json.dumps(run.meta), run.id),
        )
    write_manifest(run)


def abandon_stale_capture_runs(*, except_id: Optional[int] = None) -> int:
    """Mark older incomplete capture runs abandoned so resume picks the right one."""
    with connect() as conn:
        if except_id is None:
            cur = conn.execute(
                """
                UPDATE capture_runs
                SET status = 'abandoned'
                WHERE status IN ('capturing', 'interrupted', 'failed')
                """
            )
        else:
            cur = conn.execute(
                """
                UPDATE capture_runs
                SET status = 'abandoned'
                WHERE status IN ('capturing', 'interrupted', 'failed')
                  AND id != ?
                """,
                (except_id,),
            )
        return int(cur.rowcount or 0)


def find_resumable_capture_run() -> Optional[CaptureRun]:
    """Latest incomplete capture run that still has an on-disk root."""
    with connect() as conn:
        row = conn.execute(
            """
            SELECT id FROM capture_runs
            WHERE status IN ('capturing', 'interrupted', 'failed')
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    if not row:
        return None
    try:
        run = load_capture_run(run_id=int(row["id"]))
    except FileNotFoundError:
        return None
    if not run.root_dir or not os.path.isdir(run.root_dir):
        return None
    return run


def register_match_meta(
    run: CaptureRun,
    match_name: str,
    *,
    section: str = "unknown",
    preview: str = "",
    is_new_match: bool = False,
    capture_status: Optional[str] = None,
) -> None:
    key = name_key(match_name)
    existing = dict(run.matches.get(key) or {})
    existing.update(
        {
            "name": match_name,
            "section": section,
            "preview": preview,
            "is_new_match": bool(is_new_match),
        }
    )
    if capture_status:
        existing["capture_status"] = capture_status
        existing["updated_at"] = _utc_now()
    run.matches[key] = existing
    match_dir = os.path.join(run.root_dir, "matches", _slug(match_name))
    os.makedirs(match_dir, exist_ok=True)
    meta_path = os.path.join(match_dir, "meta.json")
    with open(meta_path, "w", encoding="utf-8") as handle:
        json.dump(existing, handle, indent=2)


def mark_match_progress(
    run: CaptureRun,
    match_name: str,
    *,
    capture_status: str,
    section: str = "unknown",
    preview: str = "",
    is_new_match: bool = False,
    detail: str = "",
) -> None:
    """
    Record per-match capture progress for resume.
    capture_status: done | skipped_fresh | failed | capturing
    """
    key = name_key(match_name)
    register_match_meta(
        run,
        match_name,
        section=section,
        preview=preview,
        is_new_match=is_new_match,
        capture_status=capture_status,
    )
    if detail:
        run.matches[key]["detail"] = detail

    done = list(run.meta.get("completed_keys") or [])
    failed = list(run.meta.get("failed_keys") or [])
    if capture_status in {"done", "skipped_fresh"}:
        if key not in done:
            done.append(key)
        failed = [item for item in failed if item != key]
    elif capture_status == "failed":
        if key not in failed:
            failed.append(key)
        # Keep retryable: do not add to completed_keys.
    run.meta["completed_keys"] = done
    run.meta["failed_keys"] = failed
    run.meta["last_match"] = match_name
    run.meta["last_match_key"] = key
    run.meta["last_capture_status"] = capture_status
    run.meta["progress_updated_at"] = _utc_now()
    persist_run_progress(run)


def completed_match_keys(run: CaptureRun) -> Set[str]:
    """Keys that should not be re-opened while resuming this run."""
    keys: Set[str] = set(run.meta.get("completed_keys") or [])
    for key, meta in (run.matches or {}).items():
        status = (meta or {}).get("capture_status")
        if status in {"done", "skipped_fresh"}:
            keys.add(key)
    # Also treat matches that already have chat dumps as done.
    for asset in run.assets:
        if asset.match_name and asset.kind == "chat":
            keys.add(name_key(asset.match_name))
    return keys


def save_ui_dump(
    run: CaptureRun,
    xml_text: str,
    *,
    kind: str,
    sequence: int,
    match_name: Optional[str] = None,
    image_bytes: Optional[bytes] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> CaptureAsset:
    """Persist one UIAutomator XML (+ optional PNG) and index it."""
    captured_at = _utc_now()
    if match_name:
        rel_dir = os.path.join("matches", _slug(match_name))
        os.makedirs(os.path.join(run.root_dir, rel_dir), exist_ok=True)
        stem = f"{kind}_{sequence:03d}"
    else:
        rel_dir = "assets"
        os.makedirs(os.path.join(run.root_dir, rel_dir), exist_ok=True)
        stem = f"{sequence:04d}_{kind}"

    xml_rel = os.path.join(rel_dir, f"{stem}.xml")
    xml_abs = os.path.join(run.root_dir, xml_rel)
    with open(xml_abs, "w", encoding="utf-8") as handle:
        handle.write(xml_text or "")

    image_rel = None
    if image_bytes:
        image_rel = os.path.join(rel_dir, f"{stem}.png")
        with open(os.path.join(run.root_dir, image_rel), "wb") as handle:
            handle.write(image_bytes)

    meta = dict(meta or {})
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO capture_assets (
                run_id, match_name, match_name_key, kind, sequence,
                xml_path, image_path, captured_at, process_status, meta_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                run.id,
                match_name,
                name_key(match_name) if match_name else None,
                kind,
                sequence,
                xml_rel,
                image_rel,
                captured_at,
                json.dumps(meta),
            ),
        )
        asset_id = int(cur.lastrowid)

    asset = CaptureAsset(
        id=asset_id,
        run_id=run.id,
        match_name=match_name,
        kind=kind,
        sequence=sequence,
        xml_path=xml_rel,
        image_path=image_rel,
        captured_at=captured_at,
        meta=meta,
    )
    run.assets.append(asset)
    # Cheap incremental manifest so a killed capture is still usable.
    if len(run.assets) % 5 == 0 or kind in {"matches_list", "profile"}:
        write_manifest(run)
    return asset


def write_manifest(run: CaptureRun) -> str:
    path = os.path.join(run.root_dir, "manifest.json")
    payload = {
        "run_id": run.id,
        "root_dir": run.root_dir,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "status": run.status,
        "meta": run.meta,
        "matches": run.matches,
        "assets": [
            {
                "id": asset.id,
                "match_name": asset.match_name,
                "kind": asset.kind,
                "sequence": asset.sequence,
                "xml_path": asset.xml_path,
                "image_path": asset.image_path,
                "captured_at": asset.captured_at,
                "process_status": asset.process_status,
                "process_error": asset.process_error,
                "meta": asset.meta,
            }
            for asset in run.assets
        ],
    }
    ensure_parent_dir(path)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    return path


def load_capture_run(run_id: Optional[int] = None, root_dir: Optional[str] = None) -> CaptureRun:
    """Load a capture run from DB and/or manifest.json."""
    if root_dir:
        manifest_path = os.path.join(root_dir, "manifest.json")
        with open(manifest_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        assets = [
            CaptureAsset(
                id=item.get("id"),
                run_id=int(payload["run_id"]),
                match_name=item.get("match_name"),
                kind=item["kind"],
                sequence=int(item.get("sequence") or 0),
                xml_path=item.get("xml_path"),
                image_path=item.get("image_path"),
                captured_at=item.get("captured_at") or "",
                process_status=item.get("process_status") or "pending",
                process_error=item.get("process_error"),
                meta=item.get("meta") or {},
            )
            for item in payload.get("assets") or []
        ]
        return CaptureRun(
            id=int(payload["run_id"]),
            root_dir=payload.get("root_dir") or root_dir,
            started_at=payload.get("started_at") or "",
            finished_at=payload.get("finished_at"),
            status=payload.get("status") or "captured",
            meta=payload.get("meta") or {},
            assets=assets,
            matches=payload.get("matches") or {},
        )

    if run_id is None:
        with connect() as conn:
            row = conn.execute(
                """
                SELECT id FROM capture_runs
                ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
        if not row:
            raise FileNotFoundError("No capture runs in SQLite")
        run_id = int(row["id"])

    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM capture_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if not row:
            raise FileNotFoundError(f"capture_run {run_id} not found")
        asset_rows = conn.execute(
            """
            SELECT * FROM capture_assets
            WHERE run_id = ?
            ORDER BY id ASC
            """,
            (run_id,),
        ).fetchall()

    root = row["root_dir"]
    matches: Dict[str, Dict[str, Any]] = {}
    manifest_path = os.path.join(root, "manifest.json")
    if os.path.isfile(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as handle:
            matches = json.load(handle).get("matches") or {}

    assets = []
    for item in asset_rows:
        meta = {}
        try:
            meta = json.loads(item["meta_json"] or "{}")
        except json.JSONDecodeError:
            meta = {}
        assets.append(
            CaptureAsset(
                id=int(item["id"]),
                run_id=run_id,
                match_name=item["match_name"],
                kind=item["kind"],
                sequence=int(item["sequence"] or 0),
                xml_path=item["xml_path"],
                image_path=item["image_path"],
                captured_at=item["captured_at"],
                process_status=item["process_status"] or "pending",
                process_error=item["process_error"],
                meta=meta,
            )
        )
    try:
        meta = json.loads(row["meta_json"] or "{}")
    except json.JSONDecodeError:
        meta = {}
    return CaptureRun(
        id=run_id,
        root_dir=root,
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        status=row["status"],
        meta=meta,
        assets=assets,
        matches=matches,
    )


def mark_assets_processed(
    asset_ids: List[int],
    *,
    status: str = "done",
    error: Optional[str] = None,
) -> None:
    if not asset_ids:
        return
    now = _utc_now()
    with connect() as conn:
        for asset_id in asset_ids:
            conn.execute(
                """
                UPDATE capture_assets
                SET processed_at = ?, process_status = ?, process_error = ?
                WHERE id = ?
                """,
                (now, status, error, asset_id),
            )


def read_asset_xml(run: CaptureRun, asset: CaptureAsset) -> str:
    if not asset.xml_path:
        return ""
    path = asset.xml_path
    if not os.path.isabs(path):
        path = os.path.join(run.root_dir, path)
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def latest_capture_run_dir() -> Optional[str]:
    root = captures_root()
    if not os.path.isdir(root):
        return None
    dirs = [
        os.path.join(root, name)
        for name in os.listdir(root)
        if os.path.isdir(os.path.join(root, name))
        and os.path.isfile(os.path.join(root, name, "manifest.json"))
    ]
    if not dirs:
        return None
    dirs.sort(key=lambda path: os.path.getmtime(path), reverse=True)
    return dirs[0]
