"""API server tests — /api/latest must serve the exact shape the dashboard
Live mode expects, even on an empty (mock-seeded) DB."""
import pytest

fastapi = pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient


@pytest.fixture()
def client(tmp_db, monkeypatch):
    import backend.api_server as api
    import backend.caretaker_api as caretaker
    monkeypatch.setattr(api, "DB_PATH", tmp_db)
    monkeypatch.setattr(caretaker, "DB_PATH", tmp_db)
    return TestClient(api.app)


def test_emergency_actions_require_token_and_resolve(client, tmp_db, monkeypatch):
    from backend import emergency
    import backend.caretaker_api as caretaker
    monkeypatch.setattr(caretaker, "CARETAKER_TOKEN", "test-caretaker-token")
    row, _ = emergency.create_if_new(tmp_db, {
        "ts": 1000.0, "score": 70, "band": "high", "state": "alerting",
        "flags": {"tof_collapse": True},
        "actions": [{"action": "escalate_alert", "reason": "tof_collapse"}],
    })
    path = f"/caretaker/emergencies/{row['event_id']}"
    assert client.post(path + "/ack").status_code == 401
    headers = {"Authorization": "Bearer test-caretaker-token"}
    assert client.post(path + "/ack", headers=headers).json()["state"] == "acknowledged"
    assert client.post(path + "/resolve", headers=headers).json()["state"] == "resolved"
    listed = client.get("/caretaker/emergencies").json()["emergencies"][0]
    assert listed["state"] == "resolved"
    assert listed["resolved_by"] == "caretaker"


def test_latest_shape(client):
    r = client.get("/api/latest")
    assert r.status_code == 200
    data = r.json()
    for key in ("risk_score", "band", "alert_state", "modules", "events",
                "mat_frame", "mat_frames_recent", "risk_history"):
        assert key in data
    assert set(data["modules"]) == {"mat", "radar", "toilet", "audio", "tof"}
    # A fresh DB is seeded with the ACTIVE four-zone (2x2) mat geometry; the
    # legacy 72-cell grid stays empty until old firmware reports a grid frame.
    assert data["mat_mode"] == "four_zone"
    assert data["mat_zones"]["count"] == 4
    assert len(data["mat_zones"]["zones"]) == 4
    assert data["mat_zone_order"] == [
        "zone_1_top_left", "zone_2_top_right",
        "zone_3_bottom_left", "zone_4_bottom_right"]
    assert data["mat_frame"]["values"] == []


def test_mock_seed_populates(client):
    data = client.get("/api/latest").json()
    assert any(m["value"] is not None for m in data["modules"].values())
    assert len(data["events"]) >= 1


def test_mat_frame_roundtrip(client, tmp_db):
    """A real 12x6 CSV frame published through the bridge must surface intact."""
    from backend.db import get_conn
    payload = ",".join(str((i * 37) % 4096) for i in range(72))
    conn = get_conn(tmp_db)
    conn.execute("INSERT INTO events(ts,topic,payload) VALUES "
                 "(?, 'sensor/mat/frame', ?)", (123.0, payload))
    conn.commit()
    data = client.get("/api/latest").json()
    assert data["mat_frame"]["values"][1] == (37) % 4096
    assert data["mat_frame"]["rows"] == 12 and data["mat_frame"]["cols"] == 6


def test_event_levels(client, tmp_db):
    from backend.db import get_conn
    conn = get_conn(tmp_db)
    conn.executemany(
        "INSERT INTO events(ts,topic,payload) VALUES (?,?,?)",
        [(1.0, "sensor/audio/event", '{"label": "impact_thud", "energy": 0.5}'),
         (2.0, "sensor/radar/status",
          '{"presence": true, "motion": false, "stillness_ms": 130000, '
          '"possible_collapse": true}'),
         (3.0, "sensor/mat/pir", '{"entry": true}')])
    conn.commit()
    data = client.get("/api/latest").json()
    levels = {e["text"].split(":")[0]: e["level"] for e in data["events"]}
    assert levels["sensor/audio/event"] == "warn"
    assert levels["sensor/radar/status"] == "danger"
    assert levels["sensor/mat/pir"] == "safe"
