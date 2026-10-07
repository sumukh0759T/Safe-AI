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

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from backend.db import DEFAULT_DB, get_conn, init_db

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
                  trigger_name: str, message: str) -> int:
    cur = conn.execute(
        "INSERT INTO alerts(ts,severity,trigger_name,message) VALUES (?,?,?,?)",
        (ts, severity, trigger_name, message))
    conn.commit()
    return cur.lastrowid


@caretaker_app.get("/alerts")
def get_alerts(limit: int = 50, include_acknowledged: bool = True):
    conn = get_conn(DB_PATH)
    init_db(DB_PATH)
    if include_acknowledged:
        rows = conn.execute(
            "SELECT id,ts,severity,trigger_name,message,active,"
            "acknowledged_at,acknowledged_by FROM alerts "
            "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT id,ts,severity,trigger_name,message,active,"
            "acknowledged_at,acknowledged_by FROM alerts "
            "WHERE active=1 ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return {"alerts": [dict(r) for r in rows]}


@caretaker_app.post("/ack/{alert_id}")
def acknowledge(alert_id: int, by: str = "caretaker"):
    conn = get_conn(DB_PATH)
    row = conn.execute("SELECT id,active FROM alerts WHERE id=?",
                       (alert_id,)).fetchone()
    if not row:
        raise HTTPException(404, "alert not found")
    if not row["active"]:
        return {"ok": True, "message": "already acknowledged"}
    conn.execute("UPDATE alerts SET active=0, acknowledged_at=?, "
                 "acknowledged_by=? WHERE id=?",
                 (time.time(), by, alert_id))
    conn.commit()
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


def dispatch_alert(result: dict, conn: Optional[sqlite3.Connection] = None):
    """Called by fusion_engine main() after each tick with actions.
    Creates alert rows for escalation actions and high-severity flags."""
    if not result:
        return
    own_conn = conn is None
    if own_conn:
        conn = get_conn(DB_PATH)
    try:
        ts = result.get("ts", time.time())
        score = result.get("score", 0)
        flags = result.get("flags", {})
        actions = result.get("actions", [])
        state = result.get("state", "resolved")
        active_flags = [k for k, v in flags.items() if v]

        # create alert for every escalation or voice triage request action
        for a in actions:
            if a["action"] in ("escalate_alert", "request_voice_triage"):
                sev = _severity_from_flags(",".join(active_flags), score)
                trigger = a.get("reason") or a.get("prompt") or active_flags[0] if active_flags else "unknown"
                msg = _TRIGGER_MSG.get(trigger, f"Alert: {trigger}")
                _insert_alert(conn, ts, sev, trigger, msg)

        # create alerts when state is non-resolved AND any flags are active
        # (catches the initial trigger that sets voice_pending without an action)
        if state in ("voice_pending", "alerting") and active_flags:
            existing = conn.execute(
                "SELECT id FROM alerts WHERE ts > ? AND ts < ?",
                (ts - 3.0, ts + 0.5)).fetchone()
            if not existing:
                trigger = active_flags[0]
                msg = _TRIGGER_MSG.get(trigger, f"Risk elevated: {trigger}")
                _insert_alert(conn, ts, _severity_from_flags(
                    ",".join(active_flags), score), trigger, msg)

    finally:
        if own_conn:
            conn.close()
