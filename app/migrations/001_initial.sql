-- Pitch Perfect initial schema: matches, messages, drafts, style, runs.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    meta_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL UNIQUE,
    hinge_id TEXT,
    section TEXT,
    is_new_match INTEGER NOT NULL DEFAULT 0,
    list_preview TEXT,
    message_count INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT NOT NULL,
    last_synced_at TEXT,
    meta_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL,
    sender TEXT NOT NULL,
    body TEXT NOT NULL,
    timestamp_label TEXT,
    position INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    scraped_at TEXT NOT NULL,
    raw_json TEXT,
    UNIQUE (match_id, content_hash),
    FOREIGN KEY (match_id) REFERENCES matches(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_match_pos
    ON messages(match_id, position);
CREATE INDEX IF NOT EXISTS idx_matches_section
    ON matches(section);
CREATE INDEX IF NOT EXISTS idx_matches_synced
    ON matches(last_synced_at);

CREATE TABLE IF NOT EXISTS draft_replies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id TEXT NOT NULL UNIQUE,
    match_name TEXT NOT NULL,
    match_id INTEGER,
    conversation_id INTEGER,
    transcript TEXT NOT NULL,
    draft_reply TEXT NOT NULL,
    pasted INTEGER NOT NULL DEFAULT 0,
    is_new_match INTEGER NOT NULL DEFAULT 0,
    score_json TEXT,
    candidates_json TEXT,
    created_at TEXT NOT NULL,
    run_id INTEGER,
    FOREIGN KEY (match_id) REFERENCES matches(id),
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS idx_drafts_name
    ON draft_replies(match_name);

CREATE TABLE IF NOT EXISTS style_profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    profile_json TEXT NOT NULL,
    sample_count INTEGER NOT NULL DEFAULT 0,
    conversations_used INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
