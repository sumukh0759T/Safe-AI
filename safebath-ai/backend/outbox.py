"""
Durable outbox for the offline-first cloud sync path.

Anything that must eventually reach Supabase (emergency events, caretaker
acknowledgements) is enqueued here *before* any network attempt is made.
The queue lives in SQLite next to the live data, so pending items survive a
process restart — nothing blocks the local safety pipeline while the worker
talks to the cloud.

Idempotency:
  * `event_id` is UNIQUE. enqueue() uses INSERT OR IGNORE, so duplicate
    sensor retries or a worker double-dispatch can never create two rows.
  * The worker re-uploads with the same event_id; Supabase's unique
    constraint (see supabase/migrations/) makes the cloud side idempotent too.

Retry policy:
  * attempts increments per attempt; next_attempt_at = now + backoff
    (min(base^attempts, cap) + jitter) ...
  * ... until attempts >= MAX_ATTEMPTS, after which the row is marked
    'failed' (still listed by counts() and surfaced on the dashboard) —
    giving up a poisoned item so one bad event cannot stall the whole queue.
  * 'in_flight' rows whose lease (next_attempt_at) has expired are retried:
    if the worker crashed mid-send, the row is recovered after reboot.
"""
import os
import random
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional

from backend.db import DEFAULT_DB, get_conn

MAX_ATTEMPTS = 8
BACKOFF_BASE = 2
BACKOFF_CAP = 3600.0          # 1 h ceiling
IN_FLIGHT_LEASE_SECONDS = 120.0
JITTER = 0.25                 # +-25%


def get_db_path() -> str:
    return os.environ.get("SQLITE_PATH", DEFAULT_DB)


def new_event_id(prefix: str = "emg") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def _ensure_tables(db_path: str) -> None:
    from backend.db import init_db
    init_db(db_path)
    conn = get_conn(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    finally:
        conn.close()


def enqueue(db_path: str, kind: str, entity_id: int,
            event_id: Optional[str] = None,
            payload: Optional[Dict[str, Any]] = None) -> str:
    """Insert one row. If event_id already exists, does nothing (idempotent)."""
    _ensure_tables(db_path)
    event_id = event_id or new_event_id("job")
    import json
    now = time.time()
    conn = get_conn(db_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO outbox"
            "(event_id, kind, entity_id, payload, status, attempts, "
            " next_attempt_at, created_at) "
            "VALUES (?,?,?,?,'queued',0,?,?)",
            (event_id, kind, entity_id,
             json.dumps(payload or {}, default=str), now, now))
        conn.commit()
    finally:
        conn.close()
    return event_id


def pending(db_path: str, now: Optional[float] = None,
            limit: int = 100) -> List[sqlite3.Row]:
    """Rows due for a retry right now: queued, failed-with-attempts-left,
    or in_flight whose lease expired (worker crash recovery)."""
    _ensure_tables(db_path)
    now = now if now is not None else time.time()
    conn = get_conn(db_path)
    try:
        return conn.execute(
            "SELECT id, event_id, kind, entity_id, payload, status, attempts, "
            "       next_attempt_at, last_error, synced_at, created_at "
            "FROM outbox "
            "WHERE attempts < ? AND ("
            "   (status IN ('queued','failed') AND next_attempt_at <= ?) "
            "   OR (status='in_flight' AND next_attempt_at <= ?)) "
            "ORDER BY next_attempt_at LIMIT ?",
            (MAX_ATTEMPTS, now, now, limit)).fetchall()
    finally:
        conn.close()


def mark_in_flight(db_path: str, row_id: int,
                   now: Optional[float] = None) -> None:
    """Claim a row before sending; next_attempt_at becomes the retry lease so
    a crash mid-send recovers (it is retried, and cloud-side idempotency
    makes the duplicate harmless)."""
    now = now if now is not None else time.time()
    conn = get_conn(db_path)
    try:
        conn.execute(
            "UPDATE outbox SET status='in_flight', next_attempt_at=? "
            "WHERE id=?", (now + IN_FLIGHT_LEASE_SECONDS, row_id))
        conn.commit()
    finally:
        conn.close()


def _backoff(attempts: int) -> float:
    delay = min(BACKOFF_BASE ** attempts, BACKOFF_CAP)
    return delay * (1.0 + random.uniform(-JITTER, JITTER))


def mark_failed(db_path: str, row_id: int, error: str,
                now: Optional[float] = None) -> None:
    now = now if now is not None else time.time()
    conn = get_conn(db_path)
    try:
        row = conn.execute("SELECT attempts FROM outbox WHERE id=?",
                           (row_id,)).fetchone()
        attempts = ((row["attempts"] if row else 0) + 1)
        if attempts >= MAX_ATTEMPTS:
            status = "failed"
            next_at = now
        else:
            status = "queued"
            next_at = now + _backoff(attempts)
        conn.execute(
            "UPDATE outbox SET status=?, attempts=?, next_attempt_at=?, "
            "last_error=? WHERE id=?",
            (status, attempts, next_at, error[:500], row_id))
        conn.commit()
    finally:
        conn.close()


def mark_synced(db_path: str, row_id: int,
                now: Optional[float] = None) -> None:
    now = now if now is not None else time.time()
    conn = get_conn(db_path)
    try:
        conn.execute(
            "UPDATE outbox SET status='synced', synced_at=?, "
            "last_error=NULL, next_attempt_at=? WHERE id=?",
            (now, now, row_id))
        conn.commit()
    finally:
        conn.close()


def counts(db_path: str) -> Dict[str, int]:
    _ensure_tables(db_path)
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT status, COUNT(*) AS n FROM outbox GROUP BY status").fetchall()
        out = {"queued": 0, "in_flight": 0, "synced": 0, "failed": 0}
        for r in row:
            out[r["status"]] = r["n"]
        out["total"] = sum(out.values())
        return out
    finally:
        conn.close()


def oldest_pending_ts(db_path: str) -> Optional[float]:
    _ensure_tables(db_path)
    conn = get_conn(db_path)
    try:
        r = conn.execute(
            "SELECT MIN(created_at) AS t FROM outbox "
            "WHERE status IN ('queued','in_flight','failed')").fetchone()
        return r["t"] if r else None
    finally:
        conn.close()


def pending_by_kind(db_path: str, kind: str, now: Optional[float] = None,
                    limit: int = 100) -> List[sqlite3.Row]:
    now = now if now is not None else time.time()
    _ensure_tables(db_path)
    conn = get_conn(db_path)
    try:
        return conn.execute(
            "SELECT id, event_id, kind, entity_id, payload, status, attempts, "
            "next_attempt_at, last_error, synced_at, created_at "
            "FROM outbox "
            "WHERE kind=? AND attempts < ? AND ("
            "   (status IN ('queued','failed') AND next_attempt_at <= ?) "
            "   OR (status='in_flight' AND next_attempt_at <= ?)) "
            "ORDER BY next_attempt_at LIMIT ?",
            (kind, MAX_ATTEMPTS, now, now, limit)).fetchall()
    finally:
        conn.close()