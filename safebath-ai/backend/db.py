"""Shared SQLite helpers for all backend processes (bridge/fusion/api)."""
import os
import sqlite3

DEFAULT_DB = os.environ.get("SQLITE_PATH", "data/events.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    topic TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_topic ON events(topic);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);

CREATE TABLE IF NOT EXISTS risk_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    score REAL NOT NULL,
    band TEXT NOT NULL,
    state TEXT NOT NULL,
    active_flags TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS state_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    reason TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS invalid_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    topic TEXT NOT NULL,
    payload TEXT NOT NULL,
    error TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    severity TEXT NOT NULL,
    trigger_name TEXT NOT NULL,
    message TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    acknowledged_at REAL,
    acknowledged_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_active ON alerts(active);
CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(ts);
"""


def get_conn(db_path: str = DEFAULT_DB) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str = DEFAULT_DB) -> None:
    conn = get_conn(db_path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()
