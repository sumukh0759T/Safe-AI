"""
Four-zone pressure-mat telemetry tests.

The physical mat moved from a 12x6 grid (72 cells) to four zones. These tests
prove four-zone payloads travel the SAME ingestion -> storage -> /api/latest
path as every other module, that malformed zone frames are rejected loudly, and
that legacy 72-cell telemetry keeps working untouched.
"""
import json

import pytest

pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from backend.schemas import MAT_CELL_COUNT, MAT_ZONE_TOPIC  # noqa: E402


ZONES = [1200, 2900, 600, 2100]


def mat_frame_values(n=None, base=120):
    if n is None:
        n = MAT_CELL_COUNT
    return [base + (i * 37) % 2000 for i in range(n)]


def envelope(device_id="mat-01", module="mat", message_type="telemetry",
             payload=None, boot_id="A1B2C3D4E5F60718", sequence=1, **kw):
    body = {
        "contract_version": 1,
        "device_id": device_id,
        "module": module,
        "firmware_version": "1.3.0",
        "boot_id": boot_id,
        "sequence": sequence,
        "device_uptime_ms": 1_000_000,
        "timestamp": 1_700_000_000.0,
        "message_type": message_type,
        "sensor_status": "ok",
        "payload": payload if payload is not None else {"values": mat_frame_values()},
    }
    body.update(kw)
    return body


def zone_payload(zones=None, **extra):
    payload = {"sensor_type": "pressure_4zone",
               "zones": list(ZONES) if zones is None else zones}
    payload.update(extra)
    return payload


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
    monkeypatch.delenv("SAFEBATH_MAT_ZONE_CALIBRATION", raising=False)
    monkeypatch.delenv("SAFEBATH_MAT_ZONE_CALIBRATION_FILE", raising=False)
    get_tracker().reset()
    return TestClient(api.app)


def _events(db, topic=None):
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


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #
def test_valid_four_zone_accepted_and_stored(client, db):
    r = client.post("/api/v1/telemetry", json=envelope(payload=zone_payload()))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["received"] is True and body["duplicate"] is False
    assert body["event_id"] >= 1

    rows = _events(db, MAT_ZONE_TOPIC)
    assert len(rows) == 1
    stored = json.loads(rows[0]["payload"])
    assert stored["sensor_type"] == "pressure_4zone"
    assert stored["zones"] == [float(z) for z in ZONES]
    # timestamp + topic preserved; legacy grid table untouched
    assert rows[0]["ts"] == pytest.approx(1_700_000_000.0)
    assert _events(db, "sensor/mat/frame") == []


def test_device_identity_and_timestamp_preserved(client, db):
    client.post("/api/v1/telemetry", json=envelope(
        payload=zone_payload(), sequence=9, boot_id="DEADBEEFDEADBEEF"))
    from backend.db import get_conn
    conn = get_conn(db)
    try:
        row = conn.execute(
            "SELECT device_id, module, boot_id, last_seq FROM telemetry_devices "
            "WHERE device_id='mat-01'").fetchone()
    finally:
        conn.close()
    assert row["device_id"] == "mat-01" and row["module"] == "mat"
    assert row["boot_id"] == "DEADBEEFDEADBEEF" and row["last_seq"] == 9


def test_four_zone_does_not_fail_a_72_cell_length_check(client, db):
    # The whole point of the separate payload: 4 readings must NOT be judged
    # against the 72-cell grid schema.
    r = client.post("/api/v1/telemetry", json=envelope(payload=zone_payload()))
    assert r.status_code == 200
    assert _events(db, MAT_ZONE_TOPIC)


def test_zones_key_without_sensor_type_accepted(client, db):
    r = client.post("/api/v1/telemetry", json=envelope(
        payload={"zones": list(ZONES)}))
    assert r.status_code == 200
    assert _events(db, MAT_ZONE_TOPIC)


def test_legacy_72_cell_telemetry_still_supported(client, db):
    values = mat_frame_values()
    r = client.post("/api/v1/telemetry", json=envelope(payload={"values": values}))
    assert r.status_code == 200
    rows = _events(db, "sensor/mat/frame")
    assert len(rows) == 1
    assert rows[0]["payload"] == ",".join(str(v) for v in values)
    latest = client.get("/api/latest").json()
    assert latest["mat_mode"] == "grid"
    assert latest["mat_zones"] is None
    assert latest["mat_frame"]["values"] == values


def test_duplicate_sequence_still_rejected(client, db):
    assert client.post("/api/v1/telemetry", json=envelope(
        payload=zone_payload(), sequence=4)).status_code == 200
    dup = client.post("/api/v1/telemetry", json=envelope(
        payload=zone_payload(), sequence=4))
    assert dup.status_code == 409
    assert dup.json()["error"] == "duplicate_sequence"
    assert len(_events(db, MAT_ZONE_TOPIC)) == 1


# --------------------------------------------------------------------------- #
# Rejections
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("zones", [
    [1200, 2900, 600],                       # missing zone
    [1200, 2900, 600, 2100, 900],            # extra zone
    [],                                       # empty
])
def test_wrong_zone_count_rejected(client, db, zones):
    r = client.post("/api/v1/telemetry", json=envelope(payload=zone_payload(zones)))
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_payload"
    assert not _events(db, MAT_ZONE_TOPIC)


def test_negative_zone_rejected(client, db):
    r = client.post("/api/v1/telemetry",
                    json=envelope(payload=zone_payload([1200, -5, 600, 2100])))
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_payload"


def test_non_numeric_zone_rejected(client, db):
    r = client.post("/api/v1/telemetry",
                    json=envelope(payload=zone_payload(["a", 2900, 600, 2100])))
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_payload"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_zone_rejected(client, db, bad):
    body = envelope(payload=zone_payload([bad, 2900, 600, 2100]))
    # json.dumps emits bare NaN/Infinity which the server's json.loads accepts,
    # then the schema validator must reject as non-finite.
    r = client.post("/api/v1/telemetry", content=json.dumps(body),
                    headers={"content-type": "application/json"})
    assert r.status_code == 400, r.text
    assert r.json()["error"] == "invalid_payload"


def test_extra_zone_field_rejected(client, db):
    r = client.post("/api/v1/telemetry", json=envelope(
        payload=zone_payload(sneaky=True)))
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_payload"


def test_mixed_values_and_zones_rejected(client, db):
    r = client.post("/api/v1/telemetry", json=envelope(payload={
        "sensor_type": "pressure_4zone", "zones": list(ZONES),
        "values": mat_frame_values(),
    }))
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_payload"


# --------------------------------------------------------------------------- #
# /api/latest contract
# --------------------------------------------------------------------------- #
def test_latest_reports_four_zone_mode(client, db):
    client.post("/api/v1/telemetry", json=envelope(payload=zone_payload()))
    latest = client.get("/api/latest").json()
    assert latest["mat_mode"] == "four_zone"
    assert latest["mat_zones"]["count"] == 4
    assert latest["mat_zones"]["zones"] == [float(z) for z in ZONES]
    assert latest["mat_zones"]["sensor_type"] == "pressure_4zone"
    assert latest["mat_zones"]["calibration"] is None
    assert latest["mat_zone_recent"][-1] == [float(z) for z in ZONES]


def test_latest_carries_calibration_when_configured(client, db, monkeypatch):
    cal = {"polarity": "decrease",
           "zones": [{"baseline": 3000, "max": 200}] * 4}
    monkeypatch.setenv("SAFEBATH_MAT_ZONE_CALIBRATION", json.dumps(cal))
    client.post("/api/v1/telemetry", json=envelope(payload=zone_payload()))
    latest = client.get("/api/latest").json()
    assert latest["mat_zones"]["calibration"]["polarity"] == "decrease"
    assert len(latest["mat_zones"]["calibration"]["zones"]) == 4


def test_malformed_calibration_degrades_to_none(client, db, monkeypatch):
    monkeypatch.setenv("SAFEBATH_MAT_ZONE_CALIBRATION", "{not json")
    client.post("/api/v1/telemetry", json=envelope(payload=zone_payload()))
    assert client.get("/api/latest").json()["mat_zones"]["calibration"] is None


def test_four_zone_wins_over_older_grid_event(client, db):
    # Legacy grid event first, then a four-zone event -> mode switches.
    client.post("/api/v1/telemetry", json=envelope(
        payload={"values": mat_frame_values()}, sequence=1))
    client.post("/api/v1/telemetry", json=envelope(
        payload=zone_payload(), sequence=2))
    assert client.get("/api/latest").json()["mat_mode"] == "four_zone"


def test_devices_endpoint_still_lists_mat(client):
    data = client.get("/api/v1/devices").json()
    assert any(d["device_id"] == "mat-01" for d in data["devices"])


# --------------------------------------------------------------------------- #
# Zone order / contract version are explicit and stable
# --------------------------------------------------------------------------- #
def test_latest_exposes_canonical_zone_order_and_version(client, db):
    from backend.schemas import MAT_ZONE_CONTRACT_VERSION, MAT_ZONE_ORDER
    client.post("/api/v1/telemetry", json=envelope(payload=zone_payload()))
    latest = client.get("/api/latest").json()
    assert latest["mat_zone_order"] == list(MAT_ZONE_ORDER)
    assert latest["mat_zone_contract_version"] == MAT_ZONE_CONTRACT_VERSION
    assert latest["mat_zones"]["order"] == list(MAT_ZONE_ORDER)
    assert latest["mat_zones"]["contract_version"] == MAT_ZONE_CONTRACT_VERSION
    # readings are positional — index i is exactly MAT_ZONE_ORDER[i]
    assert latest["mat_zones"]["zones"] == [float(z) for z in ZONES]


# --------------------------------------------------------------------------- #
# Four-zone data can never be fed to the 72-cell ConvLSTM path
# --------------------------------------------------------------------------- #
def test_zone_frames_never_reach_the_72_cell_ml_path():
    from backend.fusion_engine import FusionEngine
    seen = []
    engine = FusionEngine(now=lambda: 0.0,
                          fall_classifier=lambda frames: seen.append(frames) or 0)
    engine.observe(MAT_ZONE_TOPIC,
                   json.dumps({"sensor_type": "pressure_4zone",
                               "zones": list(ZONES)}), 1.0)
    assert len(engine.mat_zone_frames) == 1
    assert len(engine.mat_frames) == 0      # zones never enter the grid buffer
    assert seen == []                       # classifier never sees zone data


def test_grid_frames_still_feed_the_ml_path_after_20_frames():
    from backend.fusion_engine import FusionEngine
    seen = []
    engine = FusionEngine(now=lambda: 0.0,
                          fall_classifier=lambda frames: seen.append(frames) or 0)
    csv = ",".join(str(120 + i) for i in range(MAT_CELL_COUNT))
    for _ in range(20):
        engine.observe("sensor/mat/frame", csv, 1.0)
    assert len(engine.mat_frames) == 20
    assert len(seen) == 1                    # positive control: grid still works
