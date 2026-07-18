-- Capture runs + per-screen UI dump / screenshot assets for two-phase sync.

CREATE TABLE IF NOT EXISTS capture_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    root_dir TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'capturing',
    meta_json TEXT
);

CREATE TABLE IF NOT EXISTS capture_assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    match_name TEXT,
    match_name_key TEXT,
    kind TEXT NOT NULL,
    sequence INTEGER NOT NULL DEFAULT 0,
    xml_path TEXT,
    image_path TEXT,
    captured_at TEXT NOT NULL,
    processed_at TEXT,
    process_status TEXT NOT NULL DEFAULT 'pending',
    process_error TEXT,
    meta_json TEXT,
    FOREIGN KEY (run_id) REFERENCES capture_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_capture_assets_run
    ON capture_assets(run_id, kind, sequence);

CREATE INDEX IF NOT EXISTS idx_capture_assets_match
    ON capture_assets(run_id, match_name_key, kind, sequence);

CREATE INDEX IF NOT EXISTS idx_capture_assets_pending
    ON capture_assets(process_status, run_id);
