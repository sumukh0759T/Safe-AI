"""
SQLite persistence + per-device sequence tracking for the SafeBath v1
device telemetry protocol.

Reuses the existing `events` / `invalid_events` tables (so fusion_engine and
the /api/latest dashboard contract keep working byte-for-byte) and adds the
protocol-specific tables:

  telemetry_devices     one row per device: current boot_id / last_seq /
                        last_seen / firmware_version (session state)
  telemetry_heartbeats  heartbeat messages (never written to `events`)
  telemetry_diagnostics diagnostic messages (never written to `events`)

WAL journal mode is enabled so the ingest writer can run alongside
fusion_engine and api_server readers against the same SQLite file.
"""
import os
import sqlite3
import time
from typing import Dict, Optional, Tuple

from backend.db import DEFAULT_DB, get_conn, init_db

_SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry_devices (
    device_id TEXT PRIMARY KEY,
    module TEXT NOT NULL,
    name TEXT NOT NULL,
    firmware_version TEXT,
    boot_id TEXT,
    last_seq INTEGER NOT NULL DEFAULT -1,
    last_uptime_ms INTEGER NOT NULL DEFAULT 0,
    last_seen REAL,
    first_seen REAL NOT NULL,
    last_status TEXT NOT NULL DEFAULT 'ok'
);

CREATE TABLE IF NOT EXISTS telemetry_heartbeats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    ts REAL NOT NULL,
    boot_id TEXT,
    sequence INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'ok',
    payload TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_heartbeats_device ON telemetry_heartbeats(device_id);

CREATE TABLE IF NOT EXISTS telemetry_diagnostics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    ts REAL NOT NULL,
    boot_id TEXT,
    sequence INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_diagnostics_device ON telemetry_diagnostics(device_id);
"""


def get_db_path() -> str:
    """Same precedence as backend.db.DEFAULT_DB (SQLITE_PATH env first)."""
    return os.environ.get("SQLITE_PATH", DEFAULT_DB)


def init_telemetry(db_path: Optional[str] = None) -> None:
    """Create base tables (events/invalid_events) + telemetry tables, enable WAL."""
    db_path = db_path or get_db_path()
    init_db(db_path)
    conn = get_conn(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _cap(text: str, limit: int = 2000) -> str:
    return text if len(text) <= limit else text[:limit] + "...[truncated]"


def insert_event(db_path: str, ts: float, topic: str, payload: str) -> int:
    """Insert an accepted message into `events`; returns its row id."""
    conn = get_conn(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO events(ts, topic, payload) VALUES (?,?,?)",
            (ts, topic, payload))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def insert_invalid(db_path: str, ts: float, topic: str, payload: str,
                   error: str) -> None:
    """Record a rejected packet in `invalid_events` (mirrors mqtt_bridge)."""
    conn = get_conn(db_path)
    try:
        conn.execute(
            "INSERT INTO invalid_events(ts, topic, payload, error) "
            "VALUES (?,?,?,?)", (ts, topic, _cap(payload), _cap(error)))
        conn.commit()
    except sqlite3.OperationalError:
        # DB briefly locked by another writer — never fail an HTTP rejection
        # because the audit write couldn't land.
        pass
    finally:
        conn.close()


def insert_heartbeat(db_path: str, device_id: str, ts: float, boot_id: str,
                     sequence: int, status: str, payload: str) -> int:
    conn = get_conn(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO telemetry_heartbeats"
            "(device_id, ts, boot_id, sequence, status, payload) "
            "VALUES (?,?,?,?,?,?)",
            (device_id, ts, boot_id, sequence, status, payload))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def insert_diagnostic(db_path: str, device_id: str, ts: float, boot_id: str,
                      sequence: int, payload: str) -> int:
    conn = get_conn(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO telemetry_diagnostics"
            "(device_id, ts, boot_id, sequence, payload) "
            "VALUES (?,?,?,?,?)",
            (device_id, ts, boot_id, sequence, payload))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_device_state(db_path: str, device_id: str, module: str, name: str,
                        firmware_version: str, boot_id: str, sequence: int,
                        uptime_ms: int, status: str, ts: float) -> None:
    """Upsert the per-device session row. first_seen is preserved on update."""
    conn = get_conn(db_path)
    try:
        conn.execute(
            "INSERT INTO telemetry_devices"
            "(device_id, module, name, firmware_version, boot_id, last_seq, "
            " last_uptime_ms, last_seen, first_seen, last_status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(device_id) DO UPDATE SET "
            " module=excluded.module, name=excluded.name, "
            " firmware_version=excluded.firmware_version, "
            " boot_id=excluded.boot_id, last_seq=excluded.last_seq, "
            " last_uptime_ms=excluded.last_uptime_ms, "
            " last_seen=excluded.last_seen, "
            " last_status=excluded.last_status",
            (device_id, module, name, firmware_version, boot_id, sequence,
             uptime_ms, ts, ts, status))
        conn.commit()
    finally:
        conn.close()


class SequenceTracker:
    """Per-boot duplicate detection.

    A device's `sequence` is a per-boot monotonic counter that restarts when
    the ESP32 reboots; the (boot_id, sequence) pair is therefore unique per
    device. The tracker keeps an in-process cache (seeded from
    telemetry_devices) so a duplicate is a cheap dict lookup, then persists
    the new session state through update_device_state on accept.
    """

    def __init__(self) -> None:
        self._state: Dict[str, Tuple[Optional[str], int]] = {}
        self._seeded = False

    def reset(self) -> None:
        self._state.clear()
        self._seeded = False

    def ensure_seeded(self, db_path: str) -> None:
        if self._seeded:
            return
        conn = get_conn(db_path)
        try:
            for row in conn.execute(
                    "SELECT device_id, boot_id, last_seq FROM telemetry_devices"):
                self._state[row["device_id"]] = (row["boot_id"], row["last_seq"])
        finally:
            conn.close()
        self._seeded = True

    def check(self, device_id: str, boot_id: str, sequence: int) -> Tuple[bool, Optional[str]]:
        """(ok, reason). reason is None when ok, else a machine-readable code."""
        cur = self._state.get(device_id)
        if cur is None:
            return True, None
        last_boot, last_seq = cur
        if last_boot is not None and last_boot == boot_id:
            if sequence == last_seq:
                return False, "duplicate_sequence"
            if sequence < last_seq:
                return False, "out_of_order_sequence"
        return True, None

    def record(self, device_id: str, boot_id: str, sequence: int) -> None:
        self._state[device_id] = (boot_id, sequence)


_tracker = SequenceTracker()


def get_tracker() -> SequenceTracker:
    return _tracker


def list_devices(db_path: str, registry: Dict[str, object]) -> list:
    """Merge static registry with live session state for GET /api/v1/devices."""
    conn = get_conn(db_path)
    try:
        rows = {r["device_id"]: r for r in conn.execute(
            "SELECT device_id, module, name, firmware_version, boot_id, "
            "last_seq, last_uptime_ms, last_seen, first_seen, last_status "
            "FROM telemetry_devices")}
    finally:
        conn.close()

    now = time.time()
    out = []
    for device_id, dev in registry.items():
        s = rows.get(device_id)
        last_seen = s["last_seen"] if s else None
        out.append({
            "device_id": device_id,
            "module": dev.module,
            "name": dev.name,
            "registered": True,
            "firmware_version": (s["firmware_version"] if s and s["firmware_version"]
                                 else dev.firmware_version),
            "boot_id": s["boot_id"] if s else None,
            "last_seq": s["last_seq"] if s else None,
            "last_uptime_ms": s["last_uptime_ms"] if s else None,
            "last_seen": last_seen,
            "first_seen": s["first_seen"] if s else None,
            "last_status": (s["last_status"] if s else "ok"),
            "online": bool(last_seen is not None and now - last_seen <= 90.0),
        })
    return out


def health_counts(db_path: str) -> dict:
    conn = get_conn(db_path)
    try:
        events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        invalid = conn.execute("SELECT COUNT(*) FROM invalid_events").fetchone()[0]
        devices = conn.execute("SELECT COUNT(*) FROM telemetry_devices").fetchone()[0]
    finally:
        conn.close()
    return {"events": events, "invalid_events": invalid, "active_devices": devices}


def latest_heartbeat(db_path: str) -> Optional[float]:
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT MAX(ts) AS ts FROM telemetry_heartbeats").fetchone()
        return row["ts"] if row else None
    finally:
        conn.close()