"""SQLite schema (stdlib sqlite3: no ORM needed for four small tables)."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS unanswered_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    session_id TEXT,
    created_at TEXT NOT NULL,
    retrieval_result TEXT,
    fallback_reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_status (
    url TEXT PRIMARY KEY,
    label TEXT,
    page_type TEXT,
    status TEXT NOT NULL,            -- ok | error | never
    content_hash TEXT,
    chunk_count INTEGER DEFAULT 0,
    last_checked_at TEXT,
    last_success_at TEXT,
    last_error TEXT
);
CREATE TABLE IF NOT EXISTS refresh_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    trigger TEXT NOT NULL,
    updated INTEGER, unchanged INTEGER, failed INTEGER,
    summary_json TEXT
);
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    consent INTEGER NOT NULL,
    session_id TEXT,
    created_at TEXT NOT NULL
);
"""
