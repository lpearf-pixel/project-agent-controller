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

CREATE TABLE IF NOT EXISTS task_runs (
    run_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    state TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 3),
    classification TEXT,
    exit_code INTEGER,
    stdout TEXT NOT NULL DEFAULT '',
    stderr TEXT NOT NULL DEFAULT '',
    output_truncated INTEGER NOT NULL DEFAULT 0 CHECK (output_truncated IN (0, 1)),
    created_at TEXT NOT NULL,
    finished_at TEXT,
    UNIQUE(project_id, task_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS task_attempts (
    run_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL CHECK (attempt_number BETWEEN 1 AND 3),
    classification TEXT NOT NULL,
    exit_code INTEGER,
    stdout TEXT NOT NULL,
    stderr TEXT NOT NULL,
    output_truncated INTEGER NOT NULL CHECK (output_truncated IN (0, 1)),
    occurred_at TEXT NOT NULL,
    PRIMARY KEY(run_id, attempt_number),
    FOREIGN KEY(run_id) REFERENCES task_runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS runner_circuits (
    project_id TEXT PRIMARY KEY,
    consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
    opened_at TEXT,
    probe_in_progress INTEGER NOT NULL DEFAULT 0 CHECK (probe_in_progress IN (0, 1))
);

CREATE TABLE IF NOT EXISTS incidents (
    incident_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    event_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL CHECK (occurrence_count > 0),
    first_event_json TEXT NOT NULL,
    last_event_json TEXT NOT NULL,
    UNIQUE(project_id, fingerprint)
);

CREATE TABLE IF NOT EXISTS incident_samples (
    incident_id TEXT NOT NULL,
    slot INTEGER NOT NULL CHECK (slot BETWEEN 0 AND 2),
    event_json TEXT NOT NULL,
    PRIMARY KEY(incident_id, slot),
    FOREIGN KEY(incident_id) REFERENCES incidents(incident_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS knowledge_entries (
    entry_id TEXT PRIMARY KEY,
    entry_type TEXT NOT NULL,
    project_id TEXT,
    status TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    technologies_json TEXT NOT NULL,
    components_json TEXT NOT NULL,
    workflows_json TEXT NOT NULL,
    risk_tags_json TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    source_path TEXT NOT NULL,
    content_sha256 TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_fingerprints (
    entry_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    PRIMARY KEY(entry_id, fingerprint),
    FOREIGN KEY(entry_id) REFERENCES knowledge_entries(entry_id) ON DELETE CASCADE
);

CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_entries_fts USING fts5(
    entry_id UNINDEXED,
    title,
    summary,
    technologies,
    components,
    workflows,
    risk_tags
);

CREATE TABLE IF NOT EXISTS knowledge_quarantine (
    source_path TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    content_sha256 TEXT NOT NULL
);
