"""SQLite schema. Business data lives in four tables; reports are derived from them."""
from __future__ import annotations

import sqlite3

DDL = """
CREATE TABLE IF NOT EXISTS ingested_files (
    file_key   TEXT PRIMARY KEY,           -- '<drop>/<relative path>'
    kind       TEXT NOT NULL CHECK (kind IN ('leads','events')),
    sha256     TEXT NOT NULL,
    seq        INTEGER NOT NULL,           -- ingest order
    stats_json TEXT NOT NULL               -- per-file counters (rows, malformed, dupes...)
);

-- One row per lead identity (workflow_id, phone). PII is never plaintext.
CREATE TABLE IF NOT EXISTS leads (
    id           INTEGER PRIMARY KEY,
    workflow_id  TEXT NOT NULL,
    phone_hash   TEXT NOT NULL,            -- HMAC(key, '+91XXXXXXXXXX')
    phone_last4  TEXT NOT NULL,            -- only to render the masked phone in reports
    external_ref TEXT NOT NULL,            -- schema field marked "reference"
    batch_id     TEXT NOT NULL,            -- file stem of the first valid row; permanent
    fields_json  TEXT NOT NULL,            -- normalised fields; pii fields are Fernet tokens
    UNIQUE (workflow_id, phone_hash)
);

CREATE TABLE IF NOT EXISTS lead_rejections (
    source_file TEXT NOT NULL REFERENCES ingested_files(file_key),
    row_number  INTEGER NOT NULL,
    workflow_id TEXT NOT NULL,
    batch_id    TEXT NOT NULL,
    reason      TEXT NOT NULL,
    PRIMARY KEY (source_file, row_number)
);

-- Append-only interaction ledger. Every non-malformed, non-duplicate event is stored,
-- *including* ones that cannot be linked yet: linking is done at report time, so an event
-- that arrived before its lead counts as soon as the lead shows up.
CREATE TABLE IF NOT EXISTS events (
    event_id    TEXT PRIMARY KEY,
    event_type  TEXT NOT NULL,
    workflow_id TEXT,
    call_id     TEXT,
    occurred_us INTEGER NOT NULL,          -- UTC microseconds since epoch
    phone_hash  TEXT,                      -- NULL if no / invalid phone
    payload_json TEXT NOT NULL,
    source_file TEXT NOT NULL REFERENCES ingested_files(file_key)
);
CREATE INDEX IF NOT EXISTS events_call ON events (workflow_id, call_id);

CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events ledger is append-only'); END;
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(DDL)
    return conn
