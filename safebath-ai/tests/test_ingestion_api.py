"""
SafeBath v1 telemetry protocol tests.

Verifies the ingestion API against the versioned JSON contract: accept valid
telemetry/heartbeat/event/diagnostic, reject malformed input, unknown devices,
token mismatches and duplicate/out-of-order sequences, record every rejection
in invalid_events, and keep the existing /api/latest dashboard contract working.
"""
import pytest

pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient


def mat_frame_values(n=64, base=120):
    return [base + (i * 37) % 2000 for i in range(n)]


DEFAULT_PAYLOADS = {
    ("mat", "telemetry"): {"values": mat_frame_values()},
    ("radar", "telemetry"): {"presence": True, "motion": False,
                             "stillness_ms": 0, "possible_collapse": False},
    ("toilet", "telemetry"): {"gas_ppm": 58.2, "voc_ppm": 24.1,
                              "turbidity": 2.3, "hour_of_day": 9},
    ("audio", "telemetry"): {"label": "silence", "energy": 0.01},
    ("tof", "telemetry"): {"avg_thermal_c": 26.4, "flow_pulses": 0,
                           "tof_collapse": False, "unattended_hot_fill": False},
    ("mat", "event"): {"entry": True},
    ("audio", "event"): {"reply": "fine"},
    ("tof", "event"): {"prompt": "are_you_ok"},
    ("radar", "heartbeat"): {"rssi": -55, "heap_free": 140000},
    ("tof", "diagnostic"): {"errors": 0, "heap_free": 140000},
}


def envelope(device_id="mat-01", module="mat", message_type="telemetry",
             payload=None, boot_id="A1B2C3D4E5F60718", sequence=1, **kw):
    body = {
        "contract_version": 1,
        "device_id": device_id,
        "module": module,
        "firmware_version": "1.2.0",
        "boot_id": boot_id,
        "sequence": sequence,
        "device_uptime_ms": 1_000_000,
        "timestamp": 1_700_000_000.0,
        "message_type": message_type,
        "sensor_status": "ok",
        "payload": DEFAULT_PAYLOADS.get((module, message_type), {})
                  if payload is None else payload,
    }
    body.update(kw)
    return body


@pytest.fixture()
def db(tmp_path):
    return str(tmp_path / "events.db")


@pytest.fixture()
def client(db, monkeypatch):
    import backend.api_server as api
    import backend.ingestion_api as ing
    from backend.telemetry_store import get_tracker
    monkeypatch.setattr(ing, "DB_PATH", db)
    monkeypatch.setattr(api, "DB_PATH", db)
    get_tracker().reset()
    return TestClient(api.app)


def _events(client, db, topic=None):
    from backend.db import get_conn
    conn = get_conn(db)
    try:
        if topic:
            return conn.execute(
                "SELECT ts, topic, payload FROM events WHERE topic=? "
                "ORDER BY id", (topic,)).fetchall()
        return conn.execute(
            "SELECT ts, topic, payload FROM events ORDER BY id").fetchall()
    finally:
        conn.close()


def _invalid(client, db):
    from backend.db import get_conn
    conn = get_conn(db)
    try:
        return conn.execute(
            "SELECT topic, payload, error FROM invalid_events "
            "ORDER BY id").fetchall()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Happy paths
# --------------------------------------------------------------------------- #
def test_mat_telemetry_accepted_and_latest_unchanged(client, db):
    values = mat_frame_values()
    r = client.post("/api/v1/telemetry",
                    json=envelope(payload={"values": values}))
    assert r.status_code == 200
    body = r.json()
    assert body["received"] is True and body["duplicate"] is False
    assert body["event_id"] >= 1

    rows = _events(client, db, "sensor/mat/frame")
    assert len(rows) == 1
    assert rows[0]["payload"] == ",".join(str(v) for v in values)

    latest = client.get("/api/latest").json()
    assert latest["modules"]["mat"]["value"] == ",".join(str(v) for v in values)
    assert latest["mat_frame"]["values"] == values
    assert len(latest["mat_frames_recent"][-1]) == 64  # heatmap trail intact


def test_all_modules_telemetry_use_legacy_topics(client, db):
    cases = [
        ("radar-01", "radar", "sensor/radar/status"),
        ("toilet-01", "toilet", "sensor/toilet/reading"),
        ("audio-01", "audio", "sensor/audio/event"),
        ("tof-01", "tof", "sensor/tof_thermal/status"),
    ]
    for i, (device_id, module, topic) in enumerate(cases):
        r = client.post("/api/v1/telemetry",
                        json=envelope(device_id=device_id, module=module,
                                      sequence=1))
        assert r.status_code == 200, (module, r.text)

    rows = _events(client, db, "sensor/radar/status")
    assert rows[0]["payload"] == ('{"presence": true, "motion": false, '
                                  '"stillness_ms": 0, "possible_collapse": false}')


def test_heartbeat_not_written_to_events(client, db):
    r = client.post("/api/v1/heartbeat", json=envelope(
        device_id="radar-01", module="radar", message_type="heartbeat",
        payload={"rssi": -55, "heap_free": 140000}))
    assert r.status_code == 200 and r.json()["received"] is True

    from backend.db import get_conn
    conn = get_conn(db)
    try:
        hb = conn.execute("SELECT * FROM telemetry_heartbeats").fetchall()
        events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    finally:
        conn.close()
    assert len(hb) == 1 and hb[0]["device_id"] == "radar-01"
    assert hb[0]["sequence"] == 1
    assert events == 0


def test_event_mappings(client, db):
    # mat entry -> sensor/mat/pir
    assert client.post("/api/v1/event", json=envelope(
        message_type="event", payload={"entry": True})).status_code == 200
    # audio reply -> voice/reply
    assert client.post("/api/v1/event", json=envelope(
        device_id="audio-01", module="audio", message_type="event",
        payload={"reply": "fine"})).status_code == 200
    # tof trigger -> voice/trigger
    assert client.post("/api/v1/event", json=envelope(
        device_id="tof-01", module="tof", message_type="event",
        payload={"prompt": "are_you_ok"})).status_code == 200

    rows = _events(client, db)
    assert {r["topic"] for r in rows} == {"sensor/mat/pir", "voice/reply",
                                          "voice/trigger"}
    pid = next(r for r in rows if r["topic"] == "sensor/mat/pir")
    assert pid["payload"] == '{"entry": true}'


def test_diagnostic_stored_separately(client, db):
    r = client.post("/api/v1/diagnostic", json=envelope(
        device_id="tof-01", module="tof", message_type="diagnostic",
        payload={"errors": 0, "heap_free": 140000}))
    assert r.status_code == 200

    from backend.db import get_conn
    conn = get_conn(db)
    try:
        diag = conn.execute("SELECT * FROM telemetry_diagnostics").fetchall()
        events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    finally:
        conn.close()
    assert len(diag) == 1 and diag[0]["device_id"] == "tof-01"
    assert events == 0


# --------------------------------------------------------------------------- #
# Rejections
# --------------------------------------------------------------------------- #
def test_unknown_device_rejected(client, db):
    r = client.post("/api/v1/telemetry", json=envelope(device_id="rogue-99"))
    assert r.status_code == 403
    assert r.json()["error"] == "unknown_device"
    inv = _invalid(client, db)
    assert any("unknown_device" in row["error"] for row in inv)


def test_module_mismatch_rejected(client, db):
    r = client.post("/api/v1/telemetry",
                    json=envelope(device_id="mat-01", module="radar"))
    assert r.status_code == 403
    assert r.json()["error"] == "module_mismatch"
    assert any("module_mismatch" in row["error"] for row in _invalid(client, db))


def test_malformed_envelope_rejected(client, db):
    body = envelope()
    del body["sequence"]
    r = client.post("/api/v1/telemetry", json=body)
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_message"
    assert any("sequence" in row["error"] for row in _invalid(client, db))


def test_unsupported_contract_version_rejected(client, db):
    body = envelope(contract_version=2)
    r = client.post("/api/v1/telemetry", json=body)
    assert r.status_code == 400
    assert "contract_version" in r.json()["detail"]


def test_invalid_payload_rejected(client, db):
    # mat frame must carry exactly 64 values
    r = client.post("/api/v1/telemetry",
                    json=envelope(payload={"values": [1, 2, 3]}))
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_payload"

    # radar payload missing required field
    r2 = client.post("/api/v1/telemetry", json=envelope(
        device_id="radar-01", module="radar",
        payload={"presence": True}))
    assert r2.status_code == 400
    assert any("invalid_payload" in row["error"] for row in _invalid(client, db))


def test_extra_payload_key_rejected(client, db):
    body = envelope(payload={"values": mat_frame_values(), "sneaky": True})
    r = client.post("/api/v1/telemetry", json=body)
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_payload"


def test_message_type_mismatch_rejected(client, db):
    body = envelope(message_type="heartbeat")  # posted to /api/v1/telemetry
    r = client.post("/api/v1/telemetry", json=body)
    assert r.status_code == 400
    assert r.json()["error"] == "message_type_mismatch"
    assert any("message_type_mismatch" in row["error"]
               for row in _invalid(client, db))


def test_invalid_json_rejected(client, db):
    r = client.post("/api/v1/telemetry", content="{not json")
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_json"
    assert any("invalid_json" in row["error"] for row in _invalid(client, db))


def test_duplicate_sequence_rejected(client, db):
    ok = client.post("/api/v1/telemetry", json=envelope(sequence=7))
    assert ok.status_code == 200
    dup = client.post("/api/v1/telemetry", json=envelope(sequence=7))
    assert dup.status_code == 409
    assert dup.json()["error"] == "duplicate_sequence"
    assert len(_events(client, db, "sensor/mat/frame")) == 1
    inv = _invalid(client, db)
    assert any("duplicate_sequence" in row["error"] for row in inv)


def test_out_of_order_rejected(client, db):
    assert client.post("/api/v1/telemetry",
                       json=envelope(sequence=5)).status_code == 200
    r = client.post("/api/v1/telemetry", json=envelope(sequence=3))
    assert r.status_code == 409
    assert r.json()["error"] == "out_of_order_sequence"


def test_new_boot_id_resets_sequence(client, db):
    assert client.post("/api/v1/telemetry", json=envelope(
        boot_id="A1B2C3D4E5F60718", sequence=9)).status_code == 200
    ok = client.post("/api/v1/telemetry", json=envelope(
        boot_id="1111222233334444", sequence=2))
    assert ok.status_code == 200


def test_duplicate_detection_spans_message_types(client, db):
    # heartbeat sequence 3, then telemetry replay of sequence 3 -> duplicate
    assert client.post("/api/v1/heartbeat", json=envelope(
        device_id="tof-01", module="tof", message_type="heartbeat",
        sequence=3)).status_code == 200
    r = client.post("/api/v1/telemetry", json=envelope(
        device_id="tof-01", module="tof", sequence=3))
    assert r.status_code == 409


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
def test_token_required_when_configured(client, db, monkeypatch):
    import backend.ingestion_api as ing
    from backend.device_manager import Device
    dm = {"mat-01": Device("mat-01", "mat", "Pressure Mat", token="s3cret")}
    monkeypatch.setattr(ing.device_manager, "devices", dm)

    no_header = client.post("/api/v1/telemetry", json=envelope())
    assert no_header.status_code == 403
    assert no_header.json()["error"] == "invalid_token"

    wrong = client.post("/api/v1/telemetry", json=envelope(),
                        headers={"X-Node-Token": "nope"})
    assert wrong.status_code == 403

    right = client.post("/api/v1/telemetry", json=envelope(),
                        headers={"X-Node-Token": "s3cret"})
    assert right.status_code == 200


# --------------------------------------------------------------------------- #
# Operational endpoints
# --------------------------------------------------------------------------- #
def test_health_endpoint(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["contract_version"] == 1
    for key in ("events", "invalid_events", "active_devices",
                "registered_devices", "last_heartbeat_ts"):
        assert key in data


def test_devices_endpoint(client):
    data = client.get("/api/v1/devices").json()
    ids = [d["device_id"] for d in data["devices"]]
    assert set(ids) == {"mat-01", "radar-01", "toilet-01", "audio-01", "tof-01"}
    assert all(d["registered"] for d in data["devices"])
    assert all(d["online"] is False for d in data["devices"])


def test_devices_reflects_recent_activity(client):
    # timestamp 0 => RPi clock wins, so last_seen is ~now and the board is
    # "online" (real boards without an RTC behave exactly like this).
    assert client.post("/api/v1/heartbeat", json=envelope(
        device_id="mat-01", module="mat", message_type="heartbeat",
        payload={"heap_free": 100}, timestamp=0.0)).status_code == 200
    data = client.get("/api/v1/devices").json()
    mat = next(d for d in data["devices"] if d["device_id"] == "mat-01")
    assert mat["last_seq"] == 1
    assert mat["online"] is True
    assert mat["firmware_version"] == "1.2.0"