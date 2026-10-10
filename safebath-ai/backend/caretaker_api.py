"""
Caretaker API — delivers danger alerts to the caregiver's browser.

Endpoints:
  GET  /api/caretaker/alerts    — active + recent alerts (last 50)
  GET  /api/caretaker/stream    — SSE stream; fires on new alert or state change
  POST /api/caretaker/ack/{id}  — mark an alert acknowledged
  GET  /api/caretaker/status    — patient status snapshot

Run alongside api_server (same uvicorn process):
  from backend.caretaker_api import caretaker_app
  app.mount("/caretaker", caretaker_app)
"""
import json
import os
import sqlite3
import sys
import time
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from backend.db import DEFAULT_DB, get_conn, init_db
from backend import emergency
from backend import push_service

DB_PATH = os.environ.get("SQLITE_PATH", DEFAULT_DB)

caretaker_app = FastAPI(title="SafeBath Caretaker")

# human-readable descriptions for each trigger
_TRIGGER_MSG = {
    "mat_sustained_pressure": "Patient lying on bath mat — possible fall",
    "mat_centroid_jump":      "Sudden movement on bath mat — possible stumble",
    "fall":                   "ConvLSTM fall classifier triggered",
    "radar_collapse":         "Radar detects motionless presence — possible collapse",
    "thud_confirmed":         "Impact thud detected (audio + radar confirmed)",
    "sustained_cry":          "Sustained distress cry detected",
    "tof_collapse":           "ToF sensors detect collapse posture",
    "scald_shutoff":          "Hot water + unattended — solenoid shut off",
    "unattended_hot_fill":    "Hot water flow detected without user present",
}


def _severity_from_flags(flags_csv: str, score: float) -> str:
    """Map fusion flags + score to caretaker-facing severity."""
    if score >= 60:
        return "critical"
    if score >= 30:
        return "high"
    if flags_csv:
        return "medium"
    return "normal"


def _insert_alert(conn: sqlite3.Connection, ts: float, severity: str,
                  trigger_name: str, message: str,
                  event_id: Optional[str] = None) -> int:
    cur = conn.execute(
        "INSERT INTO alerts(ts,severity,trigger_name,message,event_id) "
        "VALUES (?,?,?,?,?)",
        (ts, severity, trigger_name, message, event_id))
    conn.commit()
    return cur.lastrowid


@caretaker_app.get("/alerts")
def get_alerts(limit: int = 50, include_acknowledged: bool = True):
    conn = get_conn(DB_PATH)
    init_db(DB_PATH)
    if include_acknowledged:
        rows = conn.execute(
            "SELECT id,ts,severity,trigger_name,message,active,event_id,"
            "acknowledged_at,acknowledged_by FROM alerts "
            "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT id,ts,severity,trigger_name,message,active,event_id,"
            "acknowledged_at,acknowledged_by FROM alerts "
            "WHERE active=1 ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return {"alerts": [dict(r) for r in rows]}


@caretaker_app.post("/ack/{alert_id}")
def acknowledge(alert_id: int, by: str = "caretaker"):
    conn = get_conn(DB_PATH)
    row = conn.execute("SELECT id,active,event_id FROM alerts WHERE id=?",
                       (alert_id,)).fetchone()
    if not row:
        raise HTTPException(404, "alert not found")
    if not row["active"]:
        return {"ok": True, "message": "already acknowledged"}
    conn.execute("UPDATE alerts SET active=0, acknowledged_at=?, "
                 "acknowledged_by=? WHERE id=?",
                 (time.time(), by, alert_id))
    conn.commit()
    # if this alert is the face of an emergency event, acknowledge that too
    # (offline-local update + durable outbox ack for cloud sync)
    if row["event_id"]:
        try:
            emergency.acknowledge(DB_PATH, row["event_id"], by=by)
        except Exception as e:
            print(f"[caretaker] ack sync failed for {row['event_id']}: {e}")
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Emergency events (the truthful local source of truth for the caretaker UI)
# --------------------------------------------------------------------------- #
@caretaker_app.get("/emergencies")
def get_emergencies(limit: int = 50, state: Optional[str] = None):
    init_db(DB_PATH)
    conn = get_conn(DB_PATH)
    if state:
        rows = conn.execute(
            "SELECT * FROM emergency_events WHERE state=? "
            "ORDER BY ts DESC LIMIT ?", (state, limit)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM emergency_events ORDER BY ts DESC LIMIT ?",
            (limit,)).fetchall()
    out = [dict(r) for r in rows]
    for e in out:
        try:
            e["evidence"] = json.loads(e.get("evidence") or "{}")
        except (TypeError, ValueError):
            e["evidence"] = {}
    return {"emergencies": out}


@caretaker_app.post("/emergencies/{event_id}/ack")
def acknowledge_emergency(event_id: str, by: str = "caretaker"):
    """Acknowledge an emergency event. This marks the *notification* handled;
    it does NOT claim the physical emergency ended (resolution is the fusion
    engine's separate decision). Local state updates instantly; the ack is
    queued for cloud sync and push deliveries are marked acknowledged."""
    ok = emergency.acknowledge(DB_PATH, event_id, by=by)
    if not ok:
        raise HTTPException(404, "emergency event not found")
    try:
        push_service.mark_delivery_acknowledged(DB_PATH, event_id)
    except Exception as e:
        print(f"[caretaker] delivery ack failed: {e}")
    return {"ok": True, "event_id": event_id, "state": "acknowledged"}


# --------------------------------------------------------------------------- #
# Caregiver web-push (VAPID public key is served to the PWA so it can build
# a subscription; the private key never leaves the server env).
# --------------------------------------------------------------------------- #
@caretaker_app.get("/push/vapid-public-key")
def push_vapid_public_key():
    keys = push_service.load_keys()
    return {"public_key": keys.get("public_key", ""),
            "configured": bool(keys)}


@caretaker_app.post("/push/subscribe")
async def push_subscribe(request: Request):
    body = await request.json()
    try:
        sub_id = push_service.subscribe(
            DB_PATH, body["endpoint"], body["keys"]["p256dh"],
            body["keys"]["auth"])
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(400, f"invalid subscription body: {e}")
    return {"ok": True, "subscription_id": sub_id}


@caretaker_app.post("/push/unsubscribe")
async def push_unsubscribe(request: Request):
    body = await request.json()
    endpoint = body.get("endpoint")
    if endpoint:
        push_service.unsubscribe(DB_PATH, endpoint)
    return {"ok": True}


@caretaker_app.get("/status")
def patient_status():
    conn = get_conn(DB_PATH)
    init_db(DB_PATH)
    risk = conn.execute(
        "SELECT score,band,state,active_flags,ts FROM risk_state "
        "ORDER BY id DESC LIMIT 1").fetchone()
    active = conn.execute(
        "SELECT COUNT(*) FROM alerts WHERE active=1").fetchone()[0]
    last_alert = conn.execute(
        "SELECT id,ts,severity,trigger_name,message FROM alerts "
        "ORDER BY id DESC LIMIT 1").fetchone()
    return {
        "risk_score": risk["score"] if risk else 0,
        "band": risk["band"] if risk else "normal",
        "state": risk["state"] if risk else "resolved",
        "active_flags": risk["active_flags"] if risk else "",
        "active_alerts": active,
        "last_alert": dict(last_alert) if last_alert else None,
        "last_risk_update": risk["ts"] if risk else 0,
    }


# ---- SSE endpoint: streams new alerts + state changes in real time -------- #

@caretaker_app.get("/stream")
async def event_stream():
    """Server-Sent Events stream for caretaker dashboard."""
    init_db(DB_PATH)
    conn = get_conn(DB_PATH)
    last_check = time.time()
    last_alert_id = conn.execute(
        "SELECT COALESCE(MAX(id),0) FROM alerts").fetchone()[0]
    last_risk_ts = conn.execute(
        "SELECT COALESCE(MAX(ts),0) FROM risk_state").fetchone()[0]

    def generate():
        nonlocal last_check, last_alert_id, last_risk_ts
        while True:
            try:
                now = time.time()
                # check for new alerts
                rows = conn.execute(
                    "SELECT id,ts,severity,trigger_name,message,active "
                    "FROM alerts WHERE id > ? ORDER BY id", (last_alert_id,)
                ).fetchall()
                for r in rows:
                    last_alert_id = r["id"]
                    data = json.dumps({
                        "type": "alert", "id": r["id"], "ts": r["ts"],
                        "severity": r["severity"],
                        "trigger": r["trigger_name"],
                        "message": r["message"], "active": r["active"],
                    })
                    yield f"data: {data}\n\n"

                # check for risk_state changes
                risk = conn.execute(
                    "SELECT ts,score,band,state,active_flags "
                    "FROM risk_state WHERE ts > ? ORDER BY ts DESC LIMIT 1",
                    (last_risk_ts,)).fetchone()
                if risk:
                    last_risk_ts = risk["ts"]
                    data = json.dumps({
                        "type": "risk_update",
                        "score": risk["score"], "band": risk["band"],
                        "state": risk["state"],
                        "flags": risk["active_flags"],
                    })
                    yield f"data: {data}\n\n"

                # heartbeat every 15s so client knows connection is alive
                if now - last_check >= 15:
                    yield f"data: {json.dumps({'type':'ping'})}\n\n"
                    last_check = now

            except Exception:
                pass
            time.sleep(1)

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


def dispatch_alert(result: dict, conn: Optional[sqlite3.Connection] = None,
                   db_path: Optional[str] = None) -> Optional[str]:
    """Called by fusion_engine main() after each tick with actions.

    For every genuine escalation ('escalate_alert') it:
      1. creates ONE durable emergency_events row (idempotent — sensor
         retries / repeated dispatches within the dedup window never
         duplicate it; repeat escalation only when explicitly configured)
      2. fires the LOCAL alarm immediately (offline-first, never waits for
         cloud)
      3. enqueues the event into the outbox for async Supabase sync
      4. queues caregiver web-push deliveries
      5. creates the caretaker alert row linked to the emergency event

    Returns the emergency event_id when a NEW emergency was created,
    else None (duplicate retry or no emergency this tick).
    """
    if not result:
        return None
    own_conn = conn is None
    db_path = db_path or DB_PATH
    if own_conn:
        conn = get_conn(db_path)
    event_ids: set = set()
    try:
        ts = result.get("ts", time.time())
        score = result.get("score", 0)
        flags = result.get("flags", {})
        actions = result.get("actions", [])
        state = result.get("state", "resolved")
        active_flags = [k for k, v in flags.items() if v]
        sev = _severity_from_flags(",".join(active_flags), score)
        seen: set = set()

        for a in actions:
            if a["action"] == "escalate_alert":
                trigger = a.get("reason") or (active_flags[0] if active_flags else "unknown")
                if trigger in seen:
                    continue                        # one alert per trigger/tick
                seen.add(trigger)
                emg, created = emergency.create_if_new(db_path, result,
                                                       conn=conn)
                if emg is None:
                    continue
                eid = emg["event_id"]
                event_ids.add(eid)
                if not created:
                    # duplicate sensor retry / repeated dispatch for the same
                    # unresolved emergency: never re-alarm or re-notify.
                    continue
                alert_id = _insert_alert(conn, ts, emg["severity"], trigger,
                                         emg["message"], event_id=eid)
                # link both sides so ack/alert stay in sync
                conn.execute("UPDATE emergency_events SET alert_id=? "
                             "WHERE event_id=?", (alert_id, eid))
                conn.commit()
                emergency.fire_local_alarm(db_path, emg)
                emergency.queue_notifications(db_path, emg)
            elif a["action"] == "request_voice_triage":
                trigger = a.get("prompt", "are_you_ok")
                if trigger in seen:
                    continue
                seen.add(trigger)
                msg = _TRIGGER_MSG.get(trigger, f"Voice check: {trigger}")
                _insert_alert(conn, ts, sev, trigger, msg, event_id=None)

        # voice_pending / alerting states without an explicit escalation action
        # still deserve an alert the first time they appear (e.g. ToF collapse
        # that set voice_pending without an action row). Dedupes against alerts
        # created in the same tick above via event_ids.
        if state in ("voice_pending", "alerting") and active_flags and \
                not event_ids:
            existing = conn.execute(
                "SELECT id FROM alerts WHERE ts > ? AND ts < ?",
                (ts - 3.0, ts + 0.5)).fetchone()
            if not existing:
                trigger = active_flags[0]
                msg = _TRIGGER_MSG.get(trigger, f"Risk elevated: {trigger}")
                _insert_alert(conn, ts, _severity_from_flags(
                    ",".join(active_flags), score), trigger, msg, event_id=None)

        return next(iter(event_ids)) if event_ids else None
    finally:
        if own_conn:
            conn.close()
