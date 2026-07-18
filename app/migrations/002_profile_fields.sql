-- Profile text captured from each match's Profile tab.

CREATE TABLE IF NOT EXISTS profile_fields (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL,
    field_type TEXT NOT NULL,
    label TEXT,
    text_content TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT NOT NULL,
    scraped_at TEXT NOT NULL,
    raw_json TEXT,
    UNIQUE (match_id, content_hash),
    FOREIGN KEY (match_id) REFERENCES matches(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_profile_fields_match
    ON profile_fields(match_id, sort_order);

CREATE INDEX IF NOT EXISTS idx_profile_fields_type
    ON profile_fields(match_id, field_type);
