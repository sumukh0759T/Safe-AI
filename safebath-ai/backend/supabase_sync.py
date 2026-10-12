"""
Offline-first Supabase sync worker.

Runs as `safebath-worker.service` on the Pi. Reads the durable outbox
(backend.outbox) and:

  * pushes emergency events / caretaker acks to Supabase via REST
    (service-role key, server-side only — never in firmware or browser)
  * drives web-push deliveries to caregivers (backend.push_service)

Non-goals / behavior:
  * NEVER blocks or starves the local safety pipeline: if the cloud is
    unreachable the local alarm, local dashboard and local caretaker route
    already happened at emergency time. Items just stay 'queued' with
    exponential backoff and are recovered after reboot (SQLite + lease).
  * Unit tests inject a fake client (`--once` mode), so connectivity is not
    required to verify the sync logic.
  * 401/403 from Supabase = bad/missing service-role key: the worker logs a
    clear reason and stops retrying that batch until the error resolves (the
    row keeps its attempts instead of burning them on a config error).

Idempotency on the cloud side: every upload carries the same `event_id` and is
sent with `Prefer: resolution=merge-duplicates` so Supabase upserts on the
unique `event_id` (see supabase/migrations/0001_*.sql). Re-uploads after a
crash/restart are therefore harmless.

Environment (server-side only):
  SUPABASE_URL                e.g. https://xyz.supabase.co
  SUPABASE_SERVICE_ROLE_KEY   service-role secret (0600 env file)
  VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY / PUSH_SUBJECT
  SQLITE_PATH                 default data/events.db
"""
import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from backend.db import get_conn
from backend import outbox, push_service

SUPABASE_TABLES = {
    "emergency": "emergency_events",
    "ack": "emergency_events",
    "alert": "emergency_events",   # unused now, kept for forward-compat
}
CACHE_FRESH_SECONDS = 60.0
PING_TIMEOUT_SECONDS = 4.0


def get_db_path() -> str:
    return os.environ.get("SQLITE_PATH", outbox.get_db_path())


class NotConfigured(RuntimeError):
    """Supabase env is missing — nothing to sync until the Pi operator sets it."""


class SupabaseClient:
    """Thin REST client over urllib (no extra deps on the Pi)."""

    def __init__(self, url: str, service_role_key: str,
                 table: str = "emergency_events") -> None:
        if not url or not service_role_key:
            raise NotConfigured("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY unset")
        self.base = url.rstrip("/")
        self.key = service_role_key
        self.table = table

    def _headers(self, prefer: str) -> Dict[str, str]:
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": prefer,
            "Accept": "application/json",
        }

    def upsert(self, rows: List[Dict]) -> List[Dict]:
        """Upsert rows keyed on `event_id` (idempotent cloud-side). Returns a
        per-row result list: {"ok": bool, "error": str|None}."""
        if not rows:
            return []
        req = urllib.request.Request(
            f"{self.base}/rest/v1/{self.table}",
            data=json.dumps(rows).encode("utf-8"),
            headers=self._headers("resolution=merge-duplicates,return=minimal"),
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=PING_TIMEOUT_SECONDS) as r:
                if r.status >= 400:
                    return [{"ok": False, "error": f"http {r.status}"}
                            for _ in rows]
                return [{"ok": True, "error": None} for _ in rows]
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise PermissionError(f"Supabase auth failed ({e.code})")
            return [{"ok": False, "error": f"http {e.code}"} for _ in rows]
        except Exception as e:
            return [{"ok": False, "error": str(e)} for _ in rows]

    def ping(self) -> int:
        """Authenticated round-trip (GET /rest/v1/) — proves both reachability
        AND that the service-role key is accepted. Returns HTTP status."""
        req = urllib.request.Request(
            f"{self.base}/rest/v1/{self.table}",
            headers={"apikey": self.key,
                     "Authorization": f"Bearer {self.key}",
                     "Accept": "application/json"},
            method="GET")
        try:
            with urllib.request.urlopen(req, timeout=PING_TIMEOUT_SECONDS) as r:
                return r.status
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise PermissionError(f"Supabase auth failed ({e.code})")
            return e.code
        except Exception:
            return 0


def make_client(env: Optional[Dict[str, str]] = None) -> SupabaseClient:
    env = env or os.environ
    return SupabaseClient(env.get("SUPABASE_URL", ""),
                          env.get("SUPABASE_SERVICE_ROLE_KEY", ""))


def _emergency_row(db_path: str, event_id: str) -> Optional[Dict]:
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT event_id, ts, severity, state, trigger_name, message, "
            "evidence, device_ids, score, band, alert_id, local_alarm_fired, "
            "ack_ts, ack_by, ack_count, sync_status, synced_at, created_at "
            "FROM emergency_events WHERE event_id=?",
            (event_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _row_to_cloud(row: sqlite3.Row, kind: str) -> Dict[str, Any]:
    if kind == "emergency":
        return dict(row)
    # ack: merge-duplicates keeps the rest of the row intact, just patches
    # acknowledgement fields so the remote state matches local truth.
    payload = json.loads(row["payload"] or "{}")
    return {
        "event_id": payload.get("event_id", row["event_id"]),
        "state": payload.get("status", "acknowledged"),
        "ack_ts": payload.get("ack_ts"),
        "ack_by": payload.get("by", "caretaker"),
    }


# --------------------------------------------------------------------------- #
# One sync pass (unit-testable: `--once` + injectable client)
# --------------------------------------------------------------------------- #
def sync_once(db_path: Optional[str] = None, client=None,
              now: Optional[float] = None,
              send_push: bool = True) -> Dict[str, int]:
    """Process pending outbox rows + due push deliveries. Returns counts.

    client: injectable (tests provide a fake). Default = live SupabaseClient
    from env; raises NotConfigured if env missing (caller decides handling).
    """
    db_path = db_path or get_db_path()
    now = now if now is not None else time.time()
    report = {"synced": 0, "failed": 0, "not_configured": 0,
              "push_accepted": 0, "push_retried": 0,
              "errors": []}

    if client is None:
        try:
            client = make_client()
        except NotConfigured:
            client = None
    # If there is nothing to send we must not force a key check.
    pending = outbox.pending(db_path, now=now, limit=50)
    if pending and client is None:
        report["not_configured"] = len(pending)
        report["errors"].append("supabase env not configured; outbox stays queued")

    if client is not None:
        rows_to_send = [_row_to_cloud(r, r["kind"]) for r in pending]
        try:
            results = client.upsert(rows_to_send) if rows_to_send else []
        except PermissionError as e:
            # bad/missing service-role key: hold items (do NOT burn attempts)
            report["errors"].append(f"auth error, holding outbox: {e}")
            results = []
            pending = []          # skip the mark loop below
        for item, result in zip(pending, results):
            if result["ok"]:
                outbox.mark_synced(db_path, item["id"], now=now)
                report["synced"] += 1
            else:
                outbox.mark_failed(db_path, item["id"],
                                   result.get("error", "unknown"),
                                   now=now)
                report["failed"] += 1
                report["errors"].append(f"{item['event_id']}: "
                                        f"{result.get('error')}")

    if send_push:
        for d in push_service.queue_due_deliveries(db_path, now=now):
            event = _emergency_row(db_path, d["event_id"]) or \
                {"event_id": d["event_id"], "message": "SafeBath emergency"}
            payload = push_service.build_payload(event)
            status = push_service.send_one(db_path, d, payload, now=now)
            if status == "accepted":
                report["push_accepted"] += 1
            else:
                report["push_retried"] += 1
    return report


# --------------------------------------------------------------------------- #
# Reachability cache (written by worker + probe; read by dashboard)
# --------------------------------------------------------------------------- #
def _write_health(db_path: str, reachable: bool, http_status: Optional[int],
                  reason: str, latency_ms: Optional[float],
                  now: Optional[float] = None) -> None:
    now = now if now is not None else time.time()
    from backend.db import init_db
    init_db(db_path)
    conn = get_conn(db_path)
    try:
        conn.execute(
            "INSERT INTO cloud_health(id, last_check, reachable, http_status, "
            "reason, latency_ms) VALUES (1,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET last_check=excluded.last_check, "
            "reachable=excluded.reachable, http_status=excluded.http_status, "
            "reason=excluded.reason, latency_ms=excluded.latency_ms",
            (now, int(reachable), http_status,
             reason[:300] if reason else None, latency_ms))
        conn.commit()
    finally:
        conn.close()


def _read_health(db_path: str) -> Optional[Dict]:
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT last_check, reachable, http_status, reason, latency_ms "
            "FROM cloud_health WHERE id=1").fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def probe_cloud(db_path: Optional[str] = None,
                client=None) -> Dict[str, Any]:
    """One live reachability probe (never blocks the API: 4s timeout)."""
    db_path = db_path or get_db_path()
    started = time.time()
    if client is None:
        try:
            client = make_client()
        except NotConfigured:
            _write_health(db_path, False, None, "not_configured", None)
            return {"configured": False, "reachable": None,
                    "reason": "supabase env not configured"}
    try:
        status = client.ping()
        latency = (time.time() - started) * 1000.0
        if status == 200:
            _write_health(db_path, True, 200, None, latency)
            return {"configured": True, "reachable": True,
                    "http_status": 200, "latency_ms": round(latency, 1)}
        _write_health(db_path, False, status,
                      f"http {status}", latency)
        return {"configured": True, "reachable": False,
                "http_status": status, "reason": f"http {status}"}
    except PermissionError as e:
        latency = (time.time() - started) * 1000.0
        _write_health(db_path, False, 403, str(e), latency)
        return {"configured": True, "reachable": False,
                "http_status": 403, "reason": str(e)}
    except Exception as e:
        latency = (time.time() - started) * 1000.0
        _write_health(db_path, False, None, str(e), latency)
        return {"configured": True, "reachable": False, "reason": str(e)}


def cloud_status(db_path: Optional[str] = None,
                 now: Optional[float] = None) -> Dict[str, Any]:
    """Fresh cached value if available, else a fast live probe. Read-only for
    the dashboard; never makes the request hang (4s cap)."""
    db_path = db_path or get_db_path()
    now = now if now is not None else time.time()
    from backend.db import init_db
    try:
        init_db(db_path)
        cached = _read_health(db_path)
    except Exception:
        cached = None
    if cached and now - cached["last_check"] < CACHE_FRESH_SECONDS:
        return {"configured": True,
                "reachable": bool(cached["reachable"]),
                "http_status": cached["http_status"],
                "reason": cached["reason"],
                "latency_ms": cached["latency_ms"],
                "last_check": cached["last_check"]}
    return probe_cloud(db_path)


# --------------------------------------------------------------------------- #
# Worker loop (runs as safebath-worker.service on the Pi)
# --------------------------------------------------------------------------- #
def run_worker(db_path: Optional[str] = None, interval: float = 30.0,
               client=None, max_loops: Optional[int] = None) -> None:
    db_path = db_path or get_db_path()
    loops = 0
    if client is None:
        try:
            client = make_client()
        except NotConfigured:
            print("[worker] SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY unset — "
                  "cloud sync disabled. Local safety pipeline unaffected.")
    print(f"[worker] starting against {db_path}; "
          f"client={'env' if client else 'NONE (cloud disabled)'}")
    while max_loops is None or loops < max_loops:
        loops += 1
        if client is not None:
            try:
                report = sync_once(db_path, client=client)
            except Exception as e:
                print(f"[worker] sync pass failed: {e}")
                report = {"errors": [str(e)]}
            if report["synced"] or report["failed"]:
                print(f"[worker] pass {loops}: synced={report['synced']} "
                      f"failed={report['failed']} "
                      f"push_accepted={report['push_accepted']}")
        else:
            probe_cloud(db_path)   # keeps the dashboard honest about cloud-down
        time.sleep(interval)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=None)
    ap.add_argument("--interval", type=float, default=30.0)
    ap.add_argument("--once", action="store_true",
                    help="process pending outbox/pushes then exit")
    ap.add_argument("--loops", type=int, default=None)
    ap.add_argument("--probe", action="store_true",
                    help="run one reachability probe then exit")
    args = ap.parse_args()
    db = args.db or get_db_path()
    if args.probe:
        print(json.dumps(probe_cloud(db), indent=2))
    elif args.once:
        try:
            report = sync_once(db)
        except Exception as e:
            report = {"errors": [str(e)]}
        print(json.dumps(report, indent=2, default=str))
    else:
        run_worker(db, interval=args.interval, max_loops=args.loops)
