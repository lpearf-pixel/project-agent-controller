PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    evidence_ref TEXT NOT NULL,
    UNIQUE(project_id, run_id, source_id, sequence)
);

CREATE INDEX IF NOT EXISTS idx_events_project_time
ON events(project_id, occurred_at, sequence);

CREATE TABLE IF NOT EXISTS source_cursors (
    project_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    device INTEGER NOT NULL,
    inode INTEGER NOT NULL,
    byte_offset INTEGER NOT NULL CHECK (byte_offset >= 0),
    sequence INTEGER NOT NULL CHECK (sequence >= 0),
    updated_at TEXT NOT NULL,
    PRIMARY KEY(project_id, source_id)
);

CREATE TABLE IF NOT EXISTS controller_state (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    state TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT OR IGNORE INTO controller_state (singleton, state, updated_at)
VALUES (1, 'ACTIVE', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

CREATE TABLE IF NOT EXISTS control_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL UNIQUE,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    previous_state TEXT NOT NULL,
    next_state TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);
