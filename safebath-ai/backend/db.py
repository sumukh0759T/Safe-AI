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

-- Offline-first safety: one durable row per genuine emergency. `event_id` is
-- the idempotency key used for cloud sync + push dedup (never repeated).
CREATE TABLE IF NOT EXISTS emergency_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    ts REAL NOT NULL,
    severity TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'active',
    trigger_name TEXT NOT NULL,
    message TEXT NOT NULL,
    evidence TEXT NOT NULL DEFAULT '{}',
    device_ids TEXT NOT NULL DEFAULT '',
    score REAL NOT NULL DEFAULT 0,
    band TEXT NOT NULL DEFAULT 'normal',
    alert_id INTEGER,
    local_alarm_fired INTEGER NOT NULL DEFAULT 0,
    ack_ts REAL,
    ack_by TEXT,
    ack_count INTEGER NOT NULL DEFAULT 0,
    sync_status TEXT NOT NULL DEFAULT 'pending',
    synced_at REAL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_emergency_ts ON emergency_events(ts);
CREATE INDEX IF NOT EXISTS idx_emergency_state ON emergency_events(state);
CREATE INDEX IF NOT EXISTS idx_emergency_sync ON emergency_events(sync_status);

-- Durable outbox: anything that must reach Supabase goes through here,
-- survives restarts (SQLite), retries with backoff, dedupes on event_id.
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at REAL NOT NULL,
    last_error TEXT,
    synced_at REAL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(status, next_attempt_at);

-- Web-push subscriptions (caretaker PWA). Server-side VAPID only.
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint TEXT NOT NULL UNIQUE,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_seen REAL,
    enabled INTEGER NOT NULL DEFAULT 1
);

-- One row per (event, subscription): queued -> attempted -> accepted/failed.
-- 'acknowledged' reflects the caregiver tapping the notification.
CREATE TABLE IF NOT EXISTS notification_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    subscription_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at REAL,
    last_error TEXT,
    sent_at REAL,
    acknowledged_at REAL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notif_delivery_event ON notification_deliveries(event_id);
CREATE INDEX IF NOT EXISTS idx_notif_delivery_status ON notification_deliveries(status, next_attempt_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_notif_event_sub ON notification_deliveries(event_id, subscription_id);

-- Voice Bridge over HTTP: Board 5 tof event or authorized dashboard command ->
-- claimable request. Playback lifecycle is additive; Board 5 claims and reports
-- received/started/completed/failed while Board 4 leases a listen and stores reply.
CREATE TABLE IF NOT EXISTS voice_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    ts REAL NOT NULL,
    prompt TEXT NOT NULL DEFAULT 'are_you_ok',
    claimed_by TEXT,
    claimed_at REAL,
    played_by TEXT,
    played_at REAL,
    source TEXT NOT NULL DEFAULT 'sensor',
    target_device_id TEXT,
    playback_status TEXT NOT NULL DEFAULT 'queued',
    playback_error TEXT,
    playback_updated_at REAL,
    reply TEXT,
    reply_ts REAL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_voice_requests_open ON voice_requests(expires_at, reply);

-- Explicitly shared voice-test conversation turns shown in the caretaker UI.
CREATE TABLE IF NOT EXISTS voice_conversation_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user','assistant')),
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_voice_conversation_ts ON voice_conversation_messages(ts);

-- Shared cache of the last cloud-reachability check, written by whoever runs
-- a check (api_server live probe or the sync worker) and read by the
-- dashboard — so both processes agree on "cloud up/down" without hammering.
CREATE TABLE IF NOT EXISTS cloud_health (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_check REAL NOT NULL,
    reachable INTEGER NOT NULL,
    http_status INTEGER,
    reason TEXT,
    latency_ms REAL
);
"""


def get_conn(db_path: str = DEFAULT_DB) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str,
                   ddl: str) -> None:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db(db_path: str = DEFAULT_DB) -> None:
    conn = get_conn(db_path)
    try:
        conn.executescript(_SCHEMA)
        # forward migrations for pre-existing databases
        _ensure_column(conn, "alerts", "event_id", "TEXT")
        _ensure_column(conn, "voice_requests", "played_by", "TEXT")
        _ensure_column(conn, "voice_requests", "played_at", "REAL")
        _ensure_column(conn, "voice_requests", "source", "TEXT NOT NULL DEFAULT 'sensor'")
        _ensure_column(conn, "voice_requests", "target_device_id", "TEXT")
        _ensure_column(conn, "voice_requests", "playback_status", "TEXT NOT NULL DEFAULT 'queued'")
        _ensure_column(conn, "voice_requests", "playback_error", "TEXT")
        _ensure_column(conn, "voice_requests", "playback_updated_at", "REAL")
        conn.execute(
            "UPDATE voice_requests SET playback_status='claimed' "
            "WHERE played_by IS NOT NULL AND playback_status='queued'")
        conn.commit()
    finally:
        conn.close()
