import pytest


def _client_for_voice_commands(tmp_db, monkeypatch):
    pytest.importorskip("fastapi")
    TestClient = pytest.importorskip("fastapi.testclient").TestClient
    import backend.api_server as api
    import backend.ingestion_api as ingestion

    monkeypatch.setattr(api, "DB_PATH", tmp_db)
    monkeypatch.setattr(ingestion, "DB_PATH", tmp_db)
    monkeypatch.setenv("SAFEBATH_VOICE_TEST_TOKEN", "dashboard-test-token")
    return TestClient(api.app), ingestion


def test_remote_prompt_queue_and_ack_lifecycle(tmp_db, monkeypatch):
    client, ingestion = _client_for_voice_commands(tmp_db, monkeypatch)
    dashboard_headers = {"Authorization": "Bearer dashboard-test-token"}

    queued = client.post("/api/v1/voice/commands", headers=dashboard_headers,
                         json={"prompt_id": 1, "device_id": "tof-01"})
    assert queued.status_code == 202
    command = queued.json()
    assert command["prompt"] == "are_you_ok"
    assert command["device_id"] == "tof-01"
    assert command["status"] == "queued"

    pending = client.get("/api/v1/voice/pending?device_id=tof-01&role=play")
    assert pending.status_code == 200
    assert pending.json()["event_id"] == command["event_id"]
    assert pending.json()["prompt"] == "are_you_ok"
    # Polling again does not replay the command.
    assert client.get("/api/v1/voice/pending?device_id=tof-01&role=play").json() == {
        "has_request": False
    }

    status_url = f"/api/v1/voice/commands/{command['event_id']}"
    status = client.get(status_url, headers=dashboard_headers).json()
    assert status["status"] == "claimed"
    assert status["device_id"] == "tof-01"

    ack_url = "/api/v1/voice/ack"
    device_ack = {"event_id": command["event_id"], "device_id": "tof-01"}
    for state in ("received", "started", "completed"):
        ack = client.post(ack_url, json={**device_ack, "status": state})
        assert ack.status_code == 200
        assert ack.json()["status"] == state
    assert client.get(status_url, headers=dashboard_headers).json()["status"] == "completed"


def test_remote_prompt_commands_require_dashboard_auth_and_allowlisted_id(
        tmp_db, monkeypatch):
    client, _ = _client_for_voice_commands(tmp_db, monkeypatch)
    url = "/api/v1/voice/commands"

    assert client.post(url, json={"prompt_id": 1}).status_code == 401
    headers = {"Authorization": "Bearer dashboard-test-token"}
    assert client.post(url, headers=headers,
                       json={"prompt_id": 5, "device_id": "tof-01"}).status_code == 422
    assert client.post(url, headers=headers,
                       json={"prompt_id": "1", "device_id": "tof-01"}).status_code == 422
    assert client.post(url, headers=headers,
                       json={"prompt_id": 1, "device_id": "audio-01"}).status_code == 403


def test_playback_ack_is_limited_to_claiming_tof_device(tmp_db, monkeypatch):
    client, _ = _client_for_voice_commands(tmp_db, monkeypatch)
    headers = {"Authorization": "Bearer dashboard-test-token"}
    command = client.post("/api/v1/voice/commands", headers=headers,
                          json={"prompt_id": 2}).json()
    body = {"event_id": command["event_id"], "device_id": "tof-01",
            "status": "received"}

    assert client.post("/api/v1/voice/ack", json={**body, "device_id": "audio-01"}).status_code == 403
    assert client.post("/api/v1/voice/ack", json=body).status_code == 404

    client.get("/api/v1/voice/pending?device_id=tof-01&role=play")
    assert client.post("/api/v1/voice/ack", json=body).status_code == 200
