"""
REST API the React dashboard polls — the missing link between SafeBathApp.jsx
Live mode and real data (SOFTWARE_BUILD_SPEC Sections 6.5/8).

Run:  uvicorn backend.api_server:app --reload --port 8000

Response shape (superset of the spec — extra keys are additive, so the
frontend contract stays stable):
  risk_score, band, state, modules{...}, events[...],
  mat_mode ("four_zone"|"grid"), mat_zone_order[4], mat_zone_contract_version,
  mat_zones {zones[4], count, order, contract_version, calibration, ts}|null,
  mat_zone_recent[[4], ...]
  mat_frame {values[72], rows, cols}, mat_frames_recent[[72], ...]  (LEGACY
    compatibility only — empty unless old 12x6 grid firmware still reports)
  risk_history [{ts, score}]
The four-zone mat (2x2 Velostat) is the primary shape; the 72-cell grid is a
retained, explicitly-versioned legacy shape and is never reinterpreted as zones.
If the DB is empty (bring-up day), a clearly-labeled mock seed is inserted once
so Live mode demos end-to-end without hardware.
"""
import json
import os
import sqlite3
import sys
import time

# allow direct execution / module-style uvicorn from any cwd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.db import DEFAULT_DB, get_conn, init_db
from backend.schemas import (MAT_CELL_COUNT, MAT_COLS, MAT_ROWS,
                             MAT_ZONE_CONTRACT_VERSION, MAT_ZONE_COUNT,
                             MAT_ZONE_ORDER, MAT_ZONE_TOPIC)
from backend.telemetry_store import init_telemetry

from backend.caretaker_api import caretaker_app
from backend.ingestion_api import api_v1_router
from backend.voice_test_api import router as voice_test_router

app = FastAPI(title="SafeBath AI")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])
app.mount("/caretaker", caretaker_app)
app.include_router(api_v1_router)
app.include_router(voice_test_router)

DB_PATH = os.environ.get("SQLITE_PATH", DEFAULT_DB)
# device health windows (seconds since last message)
DEVICE_ONLINE_AFTER = 90.0
DEVICE_STALE_AFTER = 600.0
SIMULATED_DEVICES = {d.strip() for d in
                     os.environ.get("SAFEBATH_SIMULATED_DEVICES", "").split(",")
                     if d.strip()}
MODULE_TOPICS = [
    ("mat", "sensor/mat/frame"),
    ("radar", "sensor/radar/status"),
    ("toilet", "sensor/toilet/reading"),
    ("audio", "sensor/audio/event"),
    ("tof", "sensor/tof_thermal/status"),
]
DANGER_TOPICS = {"sensor/radar/status", "sensor/tof_thermal/status",
                 "sensor/audio/event"}


def _device_statuses(db_path: str) -> list:
    """Per-device online/offline/stale/simulated status for the dashboard."""
    from backend.device_manager import device_manager
    now = time.time()
    conn = get_conn(db_path)
    sess = {}
    try:
        try:
            for r in conn.execute(
                    "SELECT device_id, last_seen, last_status FROM "
                    "telemetry_devices"):
                sess[r["device_id"]] = r
        except sqlite3.OperationalError:
            sess = {}     # protocol tables not created yet (bare DB)
    finally:
        conn.close()
    out = []
    for device_id, dev in device_manager.devices.items():
        row = sess.get(device_id)
        last_seen = row["last_seen"] if row else None
        age = (now - last_seen) if last_seen is not None else None
        if last_seen is None:
            status = "offline"
        elif age <= DEVICE_ONLINE_AFTER:
            status = "online"
        elif age <= DEVICE_STALE_AFTER:
            status = "stale"
        else:
            status = "offline"
        simulated = device_id in SIMULATED_DEVICES or \
            device_id.lower().startswith("sim-")
        out.append({
            "device_id": device_id,
            "module": dev.module,
            "name": dev.name,
            "status": status,
            "last_seen": last_seen,
            "age_seconds": round(age) if age is not None else None,
            "sensor_status": row["last_status"] if row else "ok",
            "simulated": simulated,
            "registered": dev.enabled,
        })
    return out


def _emergency_summary(db_path: str) -> dict:
    conn = get_conn(db_path)
    try:
        counts = {r["state"]: r["n"] for r in conn.execute(
            "SELECT state, COUNT(*) AS n FROM emergency_events "
            "GROUP BY state")}
        recent = conn.execute(
            "SELECT event_id, ts, severity, message, trigger_name, state, "
            "local_alarm_fired, sync_status, ack_ts, ack_by "
            "FROM emergency_events ORDER BY ts DESC LIMIT 8").fetchall()
    finally:
        conn.close()
    return {
        "counts": {"active": counts.get("active", 0),
                   "acknowledged": counts.get("acknowledged", 0),
                   "resolved": counts.get("resolved", 0)},
        "recent": [dict(r) for r in recent],
    }


def _local_status(db_path: str) -> dict:
    from backend import outbox, push_service
    return {
        "local_db_ok": True,
        "outbox": outbox.counts(db_path),
        "oldest_pending_ts": outbox.oldest_pending_ts(db_path),
        "push_subscriptions": push_service.count_subscriptions(db_path),
        "push_deliveries": push_service.delivery_summary(db_path),
    }


def _cloud_status(db_path: str) -> dict:
    from backend import supabase_sync
    return supabase_sync.cloud_status(db_path)


def _load_mat_zone_calibration():
    """Optional per-zone calibration for the four-zone mat.

    Read from SAFEBATH_MAT_ZONE_CALIBRATION (inline JSON) or
    SAFEBATH_MAT_ZONE_CALIBRATION_FILE (path). Returns None when unconfigured,
    which makes the dashboard show raw ADC + an explicit "Uncalibrated" badge
    instead of inventing percentages. Expected shape:

      {
        "polarity": "increase",          # "increase" | "decrease" (global)
        "zones": [
          {"baseline": 120, "max": 3000},          # optional per-zone polarity
          {"baseline": 120, "max": 3000},
          {"baseline": 120, "max": 3000},
          {"baseline": 120, "max": 3000}
        ]
      }

    Nothing here is a measured value; operators must supply real baselines
    from a bring-up session (see README). Malformed config degrades to None.
    """
    raw = os.environ.get("SAFEBATH_MAT_ZONE_CALIBRATION")
    path = os.environ.get("SAFEBATH_MAT_ZONE_CALIBRATION_FILE")
    if not raw and path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                raw = fh.read()
        except OSError:
            return None
    if not raw:
        return None
    try:
        cfg = json.loads(raw)
        zones = cfg.get("zones")
        if not isinstance(zones, dict) and not isinstance(zones, list):
            return None
        if isinstance(zones, list) and len(zones) != MAT_ZONE_COUNT:
            return None
        return cfg
    except (ValueError, TypeError):
        return None


def _mat_state(conn: sqlite3.Connection) -> dict:
    """Which mat shape is active + the latest four-zone frame.

    Mode is decided by the most recently *inserted* mat event (id, not ts —
    device clocks may be absent), so switching a mat between grid and four-zone
    firmware flips the dashboard without deleting history. Legacy 72-cell frames
    stay available under mat_frame regardless of mode.
    """
    last_zone = conn.execute(
        "SELECT id, ts, payload FROM events WHERE topic=? "
        "ORDER BY id DESC LIMIT 1", (MAT_ZONE_TOPIC,)).fetchone()
    last_frame = conn.execute(
        "SELECT id, ts, payload FROM events WHERE topic='sensor/mat/frame' "
        "ORDER BY id DESC LIMIT 1").fetchone()

    if last_zone is not None and (last_frame is None
                                  or last_zone["id"] > last_frame["id"]):
        mode = "four_zone"
    elif last_frame is not None:
        mode = "grid"
    else:
        # The physical mat is the four-zone design: that is the default shape
        # when no mat event exists at all (matches README + mat_zone_order).
        mode = "four_zone"

    mat_zones = None
    recent = []
    if last_zone is not None:
        try:
            parsed = json.loads(
                last_zone["payload"].replace("[mock-seed]", "").strip())
            zones = [float(z) for z in parsed["zones"]]
        except (ValueError, TypeError, KeyError):
            zones = None
        if zones is not None and len(zones) == MAT_ZONE_COUNT:
            mat_zones = {
                "sensor_type": "pressure_4zone",
                "zones": zones,
                "count": MAT_ZONE_COUNT,
                "order": list(MAT_ZONE_ORDER),
                "contract_version": MAT_ZONE_CONTRACT_VERSION,
                "ts": last_zone["ts"],
                "calibration": _load_mat_zone_calibration(),
            }
        for r in conn.execute(
                "SELECT payload FROM events WHERE topic=? "
                "ORDER BY id DESC LIMIT 24", (MAT_ZONE_TOPIC,)):
            try:
                z = [float(x) for x in json.loads(
                    r["payload"].replace("[mock-seed]", "").strip())["zones"]]
            except (ValueError, TypeError, KeyError):
                continue
            if len(z) == MAT_ZONE_COUNT:
                recent.append(z)

    return {"mode": mode, "mat_zones": mat_zones,
            "mat_zone_recent": list(reversed(recent))}


def _danger_level(topic: str, payload: str) -> str:
    try:
        data = json.loads(payload) if payload.strip().startswith("{") else {}
    except json.JSONDecodeError:
        data = {}
    if topic == "sensor/radar/status" and data.get("possible_collapse"):
        return "danger"
    if topic == "sensor/tof_thermal/status" and \
            (data.get("tof_collapse") or data.get("unattended_hot_fill")):
        return "danger"
    if topic == "sensor/audio/event":
        # 6-class audio label set (backend/schemas.AudioLabel)
        if data.get("label") in ("impact_thud", "cry_or_scream"):
            return "warn"
        return "safe"
    if topic == "sensor/toilet/reading":
        return "warn"
    return "safe"


def _seed_mock_if_empty(conn: sqlite3.Connection) -> None:
    n = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    if n:
        return
    now = time.time()
    seed = [
        # Four-zone mat seed (the active geometry). The legacy 12x6 grid is not
        # seeded anymore; a legacy frame only appears if old firmware reports it.
        ("sensor/mat/zones",
         '{"sensor_type": "pressure_4zone", "zones": [120, 120, 120, 120]}'),
        ("sensor/mat/pir", '{"entry": true}'),
        ("sensor/radar/status",
         '{"presence": true, "motion": true, "stillness_ms": 0, '
         '"possible_collapse": false}'),
        ("sensor/toilet/reading",
         '{"gas_ppm": 58.2, "voc_ppm": 24.1, "turbidity": 2.3, '
         '"hour_of_day": 9}'),
        ("sensor/audio/event", '{"label": "silence", "energy": 0.01}'),
        ("sensor/tof_thermal/status",
         '{"avg_thermal_c": 26.4, "flow_pulses": 0, "tof_collapse": false, '
         '"unattended_hot_fill": false}'),
        ("voice/reply", '{"reply": "fine"}'),
    ]
    for i, (topic, payload) in enumerate(seed):
        conn.execute("INSERT INTO events(ts,topic,payload) VALUES (?,?,?)",
                     (now - len(seed) + i, topic, "[mock-seed] " + payload))
    conn.execute("INSERT INTO risk_state(ts,score,band,state,active_flags) "
                 "VALUES (?,?,?,?,?)", (now, 0.0, "normal", "resolved", ""))
    conn.commit()


@app.get("/api/latest")
def latest():
    init_telemetry(DB_PATH)
    conn = get_conn(DB_PATH)
    _seed_mock_if_empty(conn)

    risk_row = conn.execute(
        "SELECT score, band, state FROM risk_state ORDER BY ts DESC LIMIT 1"
    ).fetchone()

    modules = {}
    for mod_id, topic in MODULE_TOPICS:
        if mod_id == "mat":
            # the mat may report either shape; the newest event wins
            row = conn.execute(
                "SELECT payload FROM events WHERE topic IN "
                "('sensor/mat/zones','sensor/mat/frame')"
                " ORDER BY id DESC LIMIT 1").fetchone()
        else:
            row = conn.execute(
                "SELECT payload FROM events WHERE topic=? ORDER BY id DESC LIMIT 1",
                (topic,)).fetchone()
        modules[mod_id] = {"value": row["payload"] if row else None,
                           "ts": None}

    events = conn.execute(
        "SELECT ts, topic, payload FROM events ORDER BY id DESC LIMIT 10"
    ).fetchall()

    mat_values = []
    recent_frames = []
    for r in conn.execute(
            "SELECT payload FROM events WHERE topic='sensor/mat/frame' "
            "ORDER BY id DESC LIMIT 24"):
        try:
            vals = [int(x) for x in
                    r["payload"].replace("[mock-seed]", "").strip().split(",")]
            if len(vals) == MAT_CELL_COUNT:
                recent_frames.append(vals)
        except ValueError:
            continue
    if recent_frames:
        mat_values = recent_frames[0]
    # legacy 72-int CSV exactly as published on sensor/mat/frame, unmodified —
    # only present when legacy grid firmware is still reporting
    modules["mat"]["raw_frame"] = (
        ",".join(str(v) for v in mat_values) if mat_values else None)

    history = [{"ts": h["ts"], "score": h["score"]} for h in conn.execute(
        "SELECT ts, score FROM risk_state ORDER BY id DESC LIMIT 60")]

    mat_state = _mat_state(conn)

    return {
        "risk_score": risk_row["score"] if risk_row else 0,
        "band": risk_row["band"] if risk_row else "normal",
        "alert_state": risk_row["state"] if risk_row else "resolved",
        "modules": modules,
        "events": [{"text": f"{t}: {p}", "level": _danger_level(t, p),
                    "ts": ts} for ts, t, p in events],
        "mat_frame": {"values": mat_values, "rows": MAT_ROWS, "cols": MAT_COLS},
        "mat_frames_recent": list(reversed(recent_frames)),
        "mat_mode": mat_state["mode"],
        "mat_zones": mat_state["mat_zones"],
        "mat_zone_order": list(MAT_ZONE_ORDER),
        "mat_zone_contract_version": MAT_ZONE_CONTRACT_VERSION,
        "mat_zone_recent": mat_state["mat_zone_recent"],
        "risk_history": list(reversed(history)),
        # ---- additive fields (new client feature additions) ----------------
        "source": "real" if not _any_simulated(conn) else "simulated",
        "devices": _device_statuses(DB_PATH),
        "emergencies": _emergency_summary(DB_PATH),
        "local": _local_status(DB_PATH),
        "cloud": _cloud_status(DB_PATH),
    }


def _any_simulated(conn: sqlite3.Connection) -> bool:
    """Does today's feed look like the mock publisher (sim-* ids or the
    mock-seed marker that api_server injects during bring-up)?"""
    if SIMULATED_DEVICES:
        return True
    row = conn.execute(
        "SELECT COUNT(*) FROM events WHERE payload LIKE '[mock-seed]%'"
    ).fetchone()
    return bool(row[0])


@app.get("/api/system/status")
def system_status():
    """Offline-first dashboard health: local DB, outbox, push, device statuses,
    and (best-effort) cloud reachability — nothing here blocks on the cloud."""
    init_telemetry(DB_PATH)
    return {
        "generated_ts": time.time(),
        "devices": _device_statuses(DB_PATH),
        "emergencies": _emergency_summary(DB_PATH),
        "local": _local_status(DB_PATH),
        "cloud": _cloud_status(DB_PATH),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0",
                port=int(os.environ.get("API_PORT", "8000")))
