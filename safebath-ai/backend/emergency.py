"""
Emergency event lifecycle — offline-first.

Called by the fusion engine exactly when a genuine emergency condition passes
(a fusion 'escalate_alert' action, or an alerting / voice_pending state with
confirmed flags). Responsibilities:

  * create one durable emergency_events row (idempotent: repeated sensor
    retries or re-dispatches within the dedup window never duplicate it)
  * fire the *local* alarm output immediately (configurable command via
    SAFEBATH_LOCAL_ALARM; never blocks on cloud)
  * enqueue the event into the outbox for async Supabase sync
  * queue web-push notification deliveries for every enabled caregiver
    subscription (each (event_id, subscription) is unique -> no dup pushes)
  * track acknowledgements and push them through the same outbox

The local safety pipeline (rules -> event saved -> local alarm -> local
dashboard/caretaker route) never depends on internet or Supabase.
"""
import json
import os
import sqlite3
import subprocess
import time
from typing import Dict, List, Optional, Tuple

from backend.db import get_conn

# Flag -> owning device (used for the caretaker "room/device" display).
FLAG_DEVICES: Dict[str, str] = {
    "fall_risk": "mat-01",
    "possible_collapse": "radar-01",
    "tof_collapse": "tof-01",
    "acoustic_distress": "audio-01",
    "thermal_freeze": "tof-01",
    "toilet_biomarker": "toilet-01",
}

DEFAULT_DEDUP_SECONDS = 60.0        # one emergency per trigger per window
DEFAULT_REPEAT_SECONDS = 0.0        # 0 = no repeat escalation within window


def _severity(score: float, flags: Dict[str, bool]) -> str:
    if score >= 60:
        return "critical"
    if score >= 30:
        return "high"
    if any(flags.values()):
        return "medium"
    return "normal"


def _primary_trigger(result: Dict) -> Tuple[str, str]:
    """Return (trigger_name, message) for a fusion result."""
    from backend.caretaker_api import _TRIGGER_MSG

    flags = {k: bool(v) for k, v in result.get("flags", {}).items()}
    active_flags = [k for k, v in flags.items() if v]
    trigger = "unknown"
    for a in result.get("actions", []):
        if a.get("action") == "escalate_alert" and a.get("reason"):
            trigger = a["reason"]
            break
    else:
        if active_flags:
            trigger = active_flags[0]
    message = _TRIGGER_MSG.get(trigger, f"Safety alert: {trigger}")
    return trigger, message


def _device_ids(flags: Dict[str, bool]) -> List[str]:
    return sorted({FLAG_DEVICES[k] for k, v in flags.items()
                   if v and k in FLAG_DEVICES})


def _row_to_dict(row: sqlite3.Row, evidence_json: Optional[str] = None) -> Dict:
    d = dict(row)
    d["evidence"] = json.loads(evidence_json or d.get("evidence") or "{}")
    return d


def create_if_new(db_path: str, result: Dict,
                  conn: Optional[sqlite3.Connection] = None,
                  now: Optional[float] = None,
                  dedup_seconds: float = DEFAULT_DEDUP_SECONDS,
                  repeat_seconds: float = DEFAULT_REPEAT_SECONDS
                  ) -> Tuple[Optional[Dict], bool]:
    """
    Insert an emergency event for `result` unless one is already logged.

    Returns (row_dict, created):
      * (row, True)   a NEW emergency was created
      * (row, False)  a recent unresolved emergency for the same trigger
                      already exists — this is a duplicate retry, silently
                      folded in (update evidence only)
    """
    from backend import outbox

    now = now if now is not None else time.time()
    own_conn = conn is None
    if own_conn:
        conn = get_conn(db_path)

    flags = {k: bool(v) for k, v in result.get("flags", {}).items()}
    score = float(result.get("score", 0))
    trigger, message = _primary_trigger(result)
    dev_ids = _device_ids(flags)
    evidence = {
        "ts": result.get("ts", now),
        "score": score,
        "band": result.get("band", "normal"),
        "state": result.get("state", "resolved"),
        "trigger": trigger,
        "flags": {k: bool(v) for k, v in flags.items()},
        "actions": [a for a in result.get("actions", [])
                    if a.get("action", "").startswith(("escalate", "close_"))],
        "device_ids": dev_ids,
    }

    try:
        # --- dedup: same trigger, still active/acknowledged, inside window ----
        # An existing unresolved (or acknowledged-but-not-resolved) emergency
        # for the same trigger within the window means this packet is a
        # duplicate sensor retry — fold it in silently instead of re-alarming.
        window = now - dedup_seconds
        last = conn.execute(
            "SELECT id, event_id, ts, created_at, state, trigger_name, "
            "evidence FROM emergency_events "
            "WHERE trigger_name=? AND state IN ('active','acknowledged') "
            "  AND created_at > ? ORDER BY id DESC LIMIT 1",
            (trigger, window)).fetchone()
        if last is not None:
            # repeat escalation only when explicitly configured
            if repeat_seconds <= 0 or now - last["created_at"] < repeat_seconds:
                try:
                    ev = json.loads(last["evidence"]) or {}
                    ev.setdefault("folded", []).append(evidence)
                    conn.execute(
                        "UPDATE emergency_events SET evidence=? WHERE id=?",
                        (json.dumps(ev), last["id"]))
                    conn.commit()
                except Exception:
                    pass
                return _row_to_dict(last), False

        event_id = outbox.new_event_id("emg")
        created_at = now
        try:
            cur = conn.execute(
                "INSERT INTO emergency_events"
                "(event_id, ts, severity, state, trigger_name, message, "
                " evidence, device_ids, score, band, local_alarm_fired, "
                " synced_at, created_at) "
                "VALUES (?,?,?, 'active', ?,?,?,?,?,?,0,NULL,?)",
                (event_id, evidence["ts"], _severity(score, flags), trigger,
                 message, json.dumps(evidence), ",".join(dev_ids),
                 score, evidence["band"], created_at))
            conn.commit()
        except sqlite3.IntegrityError:
            # raced with another dispatcher — treat as existing
            row = conn.execute(
                "SELECT * FROM emergency_events WHERE event_id=?",
                (event_id,)).fetchone()
            return _row_to_dict(row), False

        row = conn.execute(
            "SELECT * FROM emergency_events WHERE id=?",
            (cur.lastrowid,)).fetchone()
        return _row_to_dict(row), True
    finally:
        if own_conn:
            conn.close()


def fire_local_alarm(db_path: str, row: Dict,
                     now: Optional[float] = None) -> None:
    """
    Local alarm output — runs IMMEDIATELY and does not wait for anything.

    * always logs the alarm to the service console/journald
    * if SAFEBATH_LOCAL_ALARM is set to a command (e.g. a GPIO/buzzer/siren
      script path), it is launched detached (`cmd <event_id> <trigger>`).
    * marks local_alarm_fired so the dashboard can show it was raised
      locally even if the cloud path is down.
    """
    from backend import outbox
    now = now if now is not None else time.time()
    cmd = os.environ.get("SAFEBATH_LOCAL_ALARM", "").strip()
    trigger = row.get("trigger_name", "unknown")
    event_id = row.get("event_id", "?")
    print(f"[safebath] LOCAL ALARM event={event_id} trigger={trigger} "
          f"cmd={cmd or '(none)'}")
    if cmd:
        try:
            subprocess.Popen([cmd, event_id, trigger])
        except Exception as e:
            print(f"[safebath] local alarm command failed: {e}")
    conn = get_conn(db_path)
    try:
        conn.execute(
            "UPDATE emergency_events SET local_alarm_fired=1, "
            "sync_status='pending' WHERE event_id=?", (event_id,))
        conn.commit()
    finally:
        conn.close()
    outbox.enqueue(db_path, "emergency", 0, event_id=event_id,
                   payload={"event_id": event_id, "trigger": trigger,
                            "ts": row.get("ts", now),
                            "severity": row.get("severity", "high")})


def queue_notifications(db_path: str, row: Dict,
                        now: Optional[float] = None) -> int:
    """Create queued push deliveries for every enabled subscription.

    (event_id, subscription_id) is UNIQUE, so a duplicate event can never
    enqueue a second push for the same caregiver. Returns the number queued.
    The actual sending lives in the sync worker (backend/supabase_sync.py);
    nothing here touches the network.
    """
    now = now if now is not None else time.time()
    conn = get_conn(db_path)
    n = 0
    try:
        subs = conn.execute(
            "SELECT id FROM push_subscriptions WHERE enabled=1").fetchall()
        for s in subs:
            cur = conn.execute(
                "INSERT OR IGNORE INTO notification_deliveries"
                "(event_id, subscription_id, status, attempts, "
                " next_attempt_at, created_at) "
                "VALUES (?,?, 'queued', 0, ?, ?)",
                (row.get("event_id"), s["id"], now, now))
            n += cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return n


def link_alert(db_path: str, event_id: str, alert_id: int) -> None:
    conn = get_conn(db_path)
    try:
        conn.execute("UPDATE emergency_events SET alert_id=? "
                     "WHERE event_id=?", (alert_id, event_id))
        conn.execute("UPDATE alerts SET event_id=? WHERE id=?",
                     (event_id, alert_id))
        conn.commit()
    finally:
        conn.close()


def list_events(db_path: str, limit: int = 50) -> List[Dict]:
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM emergency_events ORDER BY ts DESC LIMIT ?",
            (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def acknowledge(db_path: str, target: str, by: str = "caretaker",
                now: Optional[float] = None,
                conn: Optional[sqlite3.Connection] = None) -> bool:
    """
    Acknowledge an emergency event (target = event_id). Marks state
    'acknowledged' locally AND enqueues an idempotent cloud-sync ack.
    Acknowledgement is explicitly NOT a claim that the physical emergency
    ended — resolution is a separate fusion decision.
    """
    from backend import outbox
    now = now if now is not None else time.time()
    own = conn is None
    conn = conn or get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT id FROM emergency_events WHERE event_id=?",
            (target,)).fetchone()
        if not row:
            return False
        conn.execute(
            "UPDATE emergency_events SET state='acknowledged', ack_ts=?, "
            "ack_by=?, ack_count=ack_count+1 WHERE event_id=?",
            (now, by, target))
        conn.commit()
        # local state is durable now; enqueue the (idempotent) cloud-sync ack.
        # ack-<event_id> is unique in outbox, so repeated taps never duplicate.
        outbox.enqueue(db_path, "ack", row["id"],
                       event_id=f"ack-{target}",
                       payload={"event_id": target, "by": by,
                                "ack_ts": now, "status": "acknowledged"})
        return True
    finally:
        if own:
            conn.close()