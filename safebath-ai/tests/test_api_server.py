"""API server tests — /api/latest must serve the exact shape the dashboard
Live mode expects, even on an empty (mock-seeded) DB."""
import pytest

fastapi = pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient


@pytest.fixture()
def client(tmp_db, monkeypatch):
    import backend.api_server as api
    monkeypatch.setattr(api, "DB_PATH", tmp_db)
    return TestClient(api.app)


def test_latest_shape(client):
    r = client.get("/api/latest")
    assert r.status_code == 200
    data = r.json()
    for key in ("risk_score", "band", "alert_state", "modules", "events",
                "mat_frame", "mat_frames_recent", "risk_history"):
        assert key in data
    assert set(data["modules"]) == {"mat", "radar", "toilet", "audio", "tof"}
    assert len(data["mat_frame"]["values"]) == 72


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
