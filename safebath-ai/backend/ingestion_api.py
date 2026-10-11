"""
SafeBath v1 device telemetry protocol — ingestion API.

One versioned JSON contract for all five ESP32 boards. Every message is a
single JSON object sent to:

    POST /api/v1/telemetry      message_type: "telemetry"
    POST /api/v1/heartbeat      message_type: "heartbeat"
    POST /api/v1/event          message_type: "event"
    POST /api/v1/diagnostic     message_type: "diagnostic"
    GET  /api/v1/health
    GET  /api/v1/devices

Envelope (all fields required, extra fields rejected):

    {
      "contract_version": 1,
      "device_id": "mat-01",                 // registered board id
      "module": "mat",                       // mat | radar | toilet | audio | tof
      "firmware_version": "1.2.0",
      "boot_id": "A1B2C3D4E5F60718",         // hex string; new value per boot
      "sequence": 17,                        // per-boot monotonic counter
      "device_uptime_ms": 604800123,
      "timestamp": 1699561234.5,             // unix seconds; 0 = use RPi clock
      "message_type": "telemetry",           // telemetry | heartbeat | event | diagnostic
      "sensor_status": "ok",                 // ok | degraded | fault
      "payload": { ... }                     // module/message_type specific
    }

Payload shapes reuse the existing schemas.py contract (12x6 mat frame = 72 ints,
radar presence/motion/stillness_ms/possible_collapse, toilet gas/voc/turbidity/
hour_of_day + optional temp_c, audio label/energy, tof avg_thermal_c/flow_pulses/
tof_collapse/unattended_hot_fill). The mat module additionally accepts a
four-zone frame via an explicit discriminator, routed to its own legacy topic:

    payload {"sensor_type": "pressure_4zone", "zones": [4 readings]}
        -> sensor/mat/zones   (validated by Pressure4ZonePayload)

Events carry mat {"entry": bool}, audio {"reply": "fine"|"help"|"no_response"}
and tof {"prompt": "are_you_ok"}.

Behavior:
  * malformed JSON / envelope / payload  -> 400, recorded in invalid_events
  * unknown device, wrong module, bad token -> 403, recorded in invalid_events
  * duplicate / out-of-order (boot_id, sequence) -> 409, recorded in invalid_events
  * accepted telemetry/event -> written to `events` with the EXACT legacy topic
    + wire format (mat frame stays a 72-int CSV), so fusion_engine and the
    frontend /api/latest contract keep working unchanged. Heartbeats and
    diagnostics go to their own tables, not `events`.
"""
import json
import hmac
import os
import time
import uuid
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import Field, ValidationError

from backend.db import get_conn
from backend.device_manager import device_manager
from backend.schemas import (
    AudioEventPayload,
    MAT_ZONE_SENSOR_TYPE,
    MAT_ZONE_TOPIC,
    MatFramePayload,
    MatPirPayload,
    Pressure4ZonePayload,
    RadarStatusPayload,
    StrictModel,
    TofThermalStatusPayload,
    ToiletReadingPayload,
    VoiceReplyPayload,
    VoiceTriggerPayload,
)
from backend.telemetry_store import (
    get_db_path,
    get_tracker,
    health_counts,
    init_telemetry,
    insert_diagnostic,
    insert_event,
    insert_heartbeat,
    insert_invalid,
    latest_heartbeat,
    list_devices,
    update_device_state,
)

CONTRACT_VERSION = 1

# Voice Bridge timing (Board 5 posts a prompt event; Board 4 polls & claims it)
VOICE_REQUEST_TTL = 30.0        # how long a request stays claimable/relevant
VOICE_REQUEST_MAX_AGE = 120.0   # ignore stale requests from long ago
VOICE_CLAIM_LEASE = 8.0         # Board 4's listen window (firmware matches)

def create_voice_request(db_path: str, ts: float, prompt: str) -> str:
    """Board 5 posted a voice prompt event — record a claimable request for
    Board 4 (audio) to pick up over HTTP (replaces the old MQTT self-prompt)."""
    conn = get_conn(db_path)
    try:
        event_id = f"vr-{uuid.uuid4().hex[:16]}"
        conn.execute(
            "INSERT OR IGNORE INTO voice_requests"
            "(event_id, ts, prompt, expires_at) VALUES (?,?,?,?)",
            (event_id, ts, prompt or "are_you_ok", ts + VOICE_REQUEST_TTL))
        conn.commit()
        return event_id
    finally:
        conn.close()


def claim_voice_request(db_path: str, device_id: str,
                        now: Optional[float] = None,
                        lease: float = VOICE_CLAIM_LEASE,
                        role: str = "listen") -> Optional[Dict]:
    """Claim a voice prompt for one of two independent roles.

    role="listen" (Board 4, audio): lease the oldest open request so the board
    can listen ~8 s and POST a reply. A lease that is never answered expires and
    can be re-claimed, so a board reboot mid-listen does not wedge the bridge.

    role="play" (Board 5, DFPlayer speaker): mark the oldest not-yet-played
    request so the prompt is spoken aloud. Playback is tracked separately from
    listening (played_by vs claimed_by) so both boards can be served by the same
    request over plain HTTP — the fan-out an MQTT topic used to give us.
    """
    now = now if now is not None else time.time()
    conn = get_conn(db_path)
    try:
        if role == "play":
            row = conn.execute(
                "SELECT id, event_id, ts, prompt FROM voice_requests "
                "WHERE played_by IS NULL AND ts > ? "
                "ORDER BY ts ASC LIMIT 1",
                (now - VOICE_REQUEST_MAX_AGE,)).fetchone()
            if not row:
                return None
            conn.execute(
                "UPDATE voice_requests SET played_by=?, played_at=? WHERE id=?",
                (device_id, now, row["id"]))
            conn.commit()
            return {"event_id": row["event_id"], "prompt": row["prompt"],
                    "lease_seconds": lease, "role": "play"}

        row = conn.execute(
            "SELECT id, event_id, ts, prompt FROM voice_requests "
            "WHERE reply IS NULL AND ts > ? "
            "  AND (claimed_by IS NULL OR expires_at <= ?) "
            "ORDER BY ts ASC LIMIT 1",
            (now - VOICE_REQUEST_MAX_AGE, now)).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE voice_requests SET claimed_by=?, claimed_at=?, "
            "expires_at=? WHERE id=?",
            (device_id, now, now + lease, row["id"]))
        conn.commit()
        return {"event_id": row["event_id"], "prompt": row["prompt"],
                "lease_seconds": lease, "role": "listen"}
    finally:
        conn.close()


def record_voice_reply(db_path: str, reply: str,
                       now: Optional[float] = None) -> None:
    """Board 4 posted a reply event — attach it to the newest open request."""
    now = now if now is not None else time.time()
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT id FROM voice_requests WHERE reply IS NULL "
            "ORDER BY ts DESC LIMIT 1").fetchone()
        if row:
            conn.execute(
                "UPDATE voice_requests SET reply=?, reply_ts=? WHERE id=?",
                (reply, now, row["id"]))
            conn.commit()
    finally:
        conn.close()


DB_PATH = os.environ.get("SQLITE_PATH", get_db_path())

ModuleName = Literal["mat", "radar", "toilet", "audio", "tof"]
MessageTypeName = Literal["telemetry", "heartbeat", "event", "diagnostic"]
SensorStatus = Literal["ok", "degraded", "fault"]


# --------------------------------------------------------------------------- #
# Envelope contract
# --------------------------------------------------------------------------- #
class TelemetryEnvelope(StrictModel):
    contract_version: int = Field(CONTRACT_VERSION, ge=1, le=1)
    device_id: str = Field(min_length=1, max_length=48,
                           pattern=r"^[A-Za-z0-9_.-]+$")
    module: ModuleName
    firmware_version: str = Field(min_length=1, max_length=32)
    boot_id: str = Field(pattern=r"^[0-9a-fA-F]{8,32}$")
    sequence: int = Field(ge=0)
    device_uptime_ms: int = Field(ge=0)
    timestamp: float = Field(ge=0.0)
    message_type: MessageTypeName
    sensor_status: SensorStatus
    payload: Dict[str, Any]


# legacy topic per module for telemetry feeds
TELEMETRY_TOPIC = {
    "mat": "sensor/mat/frame",
    "radar": "sensor/radar/status",
    "toilet": "sensor/toilet/reading",
    "audio": "sensor/audio/event",
    "tof": "sensor/tof_thermal/status",
}

# payload schema per module for telemetry / event message types
TELEMETRY_PAYLOAD = {
    "mat": MatFramePayload,          # default/legacy; see resolve_telemetry()
    "radar": RadarStatusPayload,
    "toilet": ToiletReadingPayload,
    "audio": AudioEventPayload,
    "tof": TofThermalStatusPayload,
}


def resolve_telemetry(module: str, payload: Any):
    """Pick the (payload_model, legacy_topic) for a telemetry message.

    The mat module has two valid shapes and we must NOT force four-zone
    telemetry through MatFramePayload's 72-value check (or vice versa):

      {"values": [72 ints]}                       -> sensor/mat/frame (grid)
      {"sensor_type": "pressure_4zone", ...}      -> sensor/mat/zones (2x2)
      {"zones": [4 readings]}                     -> sensor/mat/zones (2x2)

    The explicit `sensor_type` discriminator is preferred; the `zones` key is
    accepted as a convenience. Everything else resolves to the legacy topic.
    """
    if module == "mat" and isinstance(payload, dict):
        if (payload.get("sensor_type") == MAT_ZONE_SENSOR_TYPE
                or "zones" in payload):
            return Pressure4ZonePayload, MAT_ZONE_TOPIC
        return MatFramePayload, "sensor/mat/frame"
    return TELEMETRY_PAYLOAD.get(module), TELEMETRY_TOPIC.get(module)


EVENT_TOPIC = {
    "mat": "sensor/mat/pir",
    "audio": "voice/reply",
    "tof": "voice/trigger",
}

EVENT_PAYLOAD = {
    "mat": MatPirPayload,
    "audio": VoiceReplyPayload,
    "tof": VoiceTriggerPayload,
}


def wire_format(topic: str, model: Any) -> str:
    """Serialize a validated payload into the exact legacy wire format the
    events table + fusion/api_server already understand."""
    d = model.model_dump(exclude_none=True)
    if topic == "sensor/mat/frame":
        return ",".join(str(v) for v in d["values"])
    if topic == MAT_ZONE_TOPIC:
        return json.dumps({"sensor_type": d["sensor_type"],
                           "zones": d["zones"]})
    if topic == "voice/reply":
        return json.dumps({"reply": d["reply"]})
    if topic == "voice/trigger":
        return json.dumps({"prompt": d["prompt"]})
    return json.dumps(d)


def _first_validation_error(exc: ValidationError) -> str:
    err = exc.errors()[0] if exc.errors() else {}
    loc = ".".join(str(x) for x in err.get("loc", ()))
    return f"{loc}: {err.get('msg', '')}" if loc else str(err.get("msg", ""))


def _reject(status: int, reason: str, detail: str = "") -> JSONResponse:
    body = {"received": False, "contract_version": CONTRACT_VERSION,
            "error": reason}
    if detail:
        body["detail"] = detail
    return JSONResponse(status_code=status, content=body)


def _accept(message_type: str, event_id: int) -> JSONResponse:
    return JSONResponse(status_code=200, content={
        "received": True, "contract_version": CONTRACT_VERSION,
        "message_type": message_type, "event_id": event_id,
        "duplicate": False,
    })


def _record_reject(db_path: str, ts: float, topic: str, raw: str,
                   error: str) -> None:
    try:
        insert_invalid(db_path, ts, topic, raw, error)
    except Exception:
        pass  # audit write must never break the HTTP response path


async def _ingest(request: Request, expected: str) -> JSONResponse:
    db_path = DB_PATH
    init_telemetry(db_path)
    ts = time.time()
    topic_prefix = f"api/v1/{expected}"

    raw = (await request.body()).decode("utf-8", errors="replace").strip()
    try:
        body = json.loads(raw)
    except ValueError:
        _record_reject(db_path, ts, topic_prefix, raw, "invalid_json")
        return _reject(400, "invalid_json")
    if not isinstance(body, dict):
        _record_reject(db_path, ts, topic_prefix, raw, "not_json_object")
        return _reject(400, "not_json_object")

    # --- envelope / contract validation ------------------------------- #
    try:
        env = TelemetryEnvelope.model_validate(body)
    except ValidationError as e:
        _record_reject(db_path, ts, topic_prefix, raw,
                       f"invalid_message: {_first_validation_error(e)}")
        return _reject(400, "invalid_message", _first_validation_error(e))

    if env.message_type != expected:
        _record_reject(db_path, ts, f"{topic_prefix}/{env.message_type}", raw,
                       "message_type_mismatch")
        return _reject(400, "message_type_mismatch")

    # --- device authorization ---------------------------------------- #
    dev = device_manager.get(env.device_id)
    if dev is None:
        _record_reject(db_path, ts, topic_prefix, raw,
                       f"unknown_device: {env.device_id}")
        return _reject(403, "unknown_device")
    if not dev.enabled:
        _record_reject(db_path, ts, topic_prefix, raw,
                       f"device_disabled: {env.device_id}")
        return _reject(403, "device_disabled")
    if dev.module != env.module:
        _record_reject(db_path, ts, topic_prefix, raw,
                       f"module_mismatch: {env.device_id} is "
                       f"{dev.module!r}, sent {env.module!r}")
        return _reject(403, "module_mismatch")
    if dev.token:
        sent = request.headers.get("X-Node-Token") or \
               request.headers.get("x-node-token")
        if sent != dev.token:
            _record_reject(db_path, ts, topic_prefix, raw,
                           f"invalid_token: {env.device_id}")
            return _reject(403, "invalid_token")

    # --- payload validation per (message_type, module) --------------- #
    topic = None
    model = None
    if expected == "telemetry":
        model, topic = resolve_telemetry(env.module, env.payload)
        if model is None:
            _record_reject(db_path, ts, topic_prefix, raw,
                           f"unsupported_telemetry: {env.module}")
            return _reject(400, "unsupported_telemetry_for_module")
    elif expected == "event":
        model = EVENT_PAYLOAD.get(env.module)
        if model is None:
            _record_reject(db_path, ts, topic_prefix, raw,
                           f"unsupported_event: {env.module}")
            return _reject(400, "unsupported_event_for_module")
        topic = EVENT_TOPIC[env.module]

    if model is not None:
        try:
            payload_model = model.model_validate(env.payload)
        except ValidationError as e:
            _record_reject(db_path, ts, topic_prefix, raw,
                           f"invalid_payload: {_first_validation_error(e)}")
            return _reject(400, "invalid_payload", _first_validation_error(e))

    # --- duplicate / out-of-order sequence gate ---------------------- #
    tracker = get_tracker()
    tracker.ensure_seeded(db_path)
    ok, reason = tracker.check(env.device_id, env.boot_id, env.sequence)
    if not ok:
        _record_reject(db_path, ts, topic or topic_prefix, raw, reason)
        return _reject(409, reason)

    # --- persistence ------------------------------------------------- #
    event_ts = env.timestamp if env.timestamp > 0 else ts

    if expected in ("telemetry", "event"):
        wire = wire_format(topic, payload_model)
        event_id = insert_event(db_path, event_ts, topic, wire)
        # Voice Bridge over HTTP: a ToF prompt event opens a request Board 4
        # can claim; an audio reply event closes the newest open request.
        try:
            if expected == "event" and env.module == "tof":
                create_voice_request(db_path, event_ts, payload_model.prompt)
            elif expected == "event" and env.module == "audio":
                record_voice_reply(db_path, payload_model.reply, event_ts)
        except Exception as e:
            print(f"[ingest] voice bridge bookkeeping failed: {e}")
    elif expected == "heartbeat":
        event_id = insert_heartbeat(
            db_path, env.device_id, event_ts, env.boot_id, env.sequence,
            env.sensor_status, json.dumps(env.payload))
    else:  # diagnostic
        event_id = insert_diagnostic(
            db_path, env.device_id, event_ts, env.boot_id, env.sequence,
            json.dumps(env.payload))

    tracker.record(env.device_id, env.boot_id, env.sequence)
    update_device_state(db_path, env.device_id, dev.module, dev.name,
                        env.firmware_version, env.boot_id, env.sequence,
                        env.device_uptime_ms, env.sensor_status, event_ts)
    return _accept(env.message_type, event_id)


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
api_v1_router = APIRouter()


@api_v1_router.post("/api/v1/telemetry")
async def ingest_telemetry(request: Request) -> JSONResponse:
    return await _ingest(request, "telemetry")


@api_v1_router.post("/api/v1/heartbeat")
async def ingest_heartbeat(request: Request) -> JSONResponse:
    return await _ingest(request, "heartbeat")


@api_v1_router.post("/api/v1/event")
async def ingest_event(request: Request) -> JSONResponse:
    return await _ingest(request, "event")


@api_v1_router.post("/api/v1/diagnostic")
async def ingest_diagnostic(request: Request) -> JSONResponse:
    return await _ingest(request, "diagnostic")


@api_v1_router.get("/api/v1/health")
async def health() -> dict:
    db_path = DB_PATH
    init_telemetry(db_path)
    counts = health_counts(db_path)
    return {
        "status": "ok",
        "contract_version": CONTRACT_VERSION,
        "db": db_path,
        **counts,
        "registered_devices": len(device_manager.known_ids()),
        "last_heartbeat_ts": latest_heartbeat(db_path),
    }


@api_v1_router.get("/api/v1/devices")
async def devices() -> dict:
    db_path = DB_PATH
    init_telemetry(db_path)
    return {"contract_version": CONTRACT_VERSION,
            "devices": list_devices(db_path, device_manager.devices)}


@api_v1_router.get("/api/v1/config")
async def config() -> dict:
    """Runtime config/primer for the boards: contract version, server time
    (for NTP-less timestamping) and the polling intervals the firmware should
    use. Safe to return with no secrets in it."""
    return {
        "contract_version": CONTRACT_VERSION,
        "server_time": time.time(),
        "heartbeat_interval_s": 30,
        "telemetry_interval_s": 1,
        "voice_poll_interval_s": 1,
        "voice_lease_s": 8,
        "endpoints": {
            "telemetry": "/api/v1/telemetry",
            "heartbeat": "/api/v1/heartbeat",
            "event": "/api/v1/event",
            "diagnostic": "/api/v1/diagnostic",
            "config": "/api/v1/config",
            "voice_pending": "/api/v1/voice/pending",
        },
    }


@api_v1_router.get("/api/v1/voice/pending")
async def voice_pending(request: Request, device_id: str = "audio-01",
                        role: str = "listen") -> dict:
    """Voice Bridge poll (was MQTT fan-out).

    role=listen (Board 4, mic): lease the prompt to listen ~8 s and POST a reply.
    role=play   (Board 5, speaker): claim the prompt to play aloud once.

    Each board polls for its own role, so both are served by one request over
    plain HTTP. Returns has_request=False when there is nothing to do so the
    board can sleep and poll again."""
    if role not in ("listen", "play"):
        role = "listen"
    device = device_manager.get(device_id)
    if device is None or not device.enabled:
        return JSONResponse(status_code=403,
                            content={"error": "unknown_or_disabled_device"})
    expected_module = "tof" if role == "play" else "audio"
    if device.module != expected_module:
        return JSONResponse(status_code=403,
                            content={"error": "device_role_mismatch"})
    if device.token:
        supplied = request.headers.get("X-Node-Token", "")
        if not hmac.compare_digest(supplied, str(device.token)):
            return JSONResponse(status_code=403,
                                content={"error": "invalid_token"})
    db_path = DB_PATH
    init_telemetry(db_path)
    claimed = claim_voice_request(db_path, device_id, role=role)
    if not claimed:
        return {"has_request": False}
    return {"has_request": True, **claimed}
