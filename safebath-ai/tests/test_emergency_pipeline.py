"""
Offline-first emergency pipeline tests.

Covers: emergency creation + persistence, dedup of retried sensor packets,
local alarm firing (command injected, never blocking), caregiver push
fan-out idempotency, caretaker acknowledgement (local + outbox ack), and the
Voice-Bridge-over-HTTP claim flow — all without network or hardware.
"""
import json
import time

import pytest

from backend import emergency, outbox, push_service

WEIGHT_FLAGS = ["fall_risk", "possible_collapse", "tof_collapse",
                "acoustic_distress", "thermal_freeze", "toilet_biomarker"]


def flags(**kw):
    d = {k: False for k in WEIGHT_FLAGS}
    d.update(kw)
    return d


def escalation_result(reason="tof_collapse", ts=1000.0, score=40.0, **fl):
    return {
        "ts": ts, "score": score, "band": "high", "state": "alerting",
        "flags": flags(**(fl or {"tof_collapse": True})),
        "actions": [{"action": "escalate_alert", "reason": reason, "ts": ts}],
    }


# --------------------------------------------------------------------------- #
# Emergency creation / dedup
# --------------------------------------------------------------------------- #
def test_emergency_created_persisted(tmp_db):
    emg, created = emergency.create_if_new(tmp_db, escalation_result())
    assert created is True
    assert emg["state"] == "active"
    assert emg["trigger_name"] == "tof_collapse"
    assert emg["severity"] == "high"
    assert emg["local_alarm_fired"] == 0
    assert emg["sync_status"] == "pending"
    assert emg["device_ids"] and "tof-01" in emg["device_ids"]
    assert len(emergency.list_events(tmp_db)) == 1


def test_emergency_dedup_same_trigger_within_window(tmp_db):
    emg1, created1 = emergency.create_if_new(tmp_db, escalation_result())
    emg2, created2 = emergency.create_if_new(tmp_db, escalation_result())
    assert created1 is True and created2 is False
    assert emg1["event_id"] == emg2["event_id"]
    assert len(emergency.list_events(tmp_db)) == 1


def test_emergency_dedup_expires_outside_window(tmp_db):
    emergency.create_if_new(tmp_db, escalation_result(ts=1000.0),
                            now=1000.0, dedup_seconds=60.0)
    # 5 minutes later the same trigger is a genuinely new emergency
    _, created = emergency.create_if_new(tmp_db, escalation_result(ts=1300.0),
                                         now=1300.0, dedup_seconds=60.0)
    assert created is True
    assert len(emergency.list_events(tmp_db)) == 2


def test_repeat_escalation_only_when_configured(tmp_db):
    emergency.create_if_new(tmp_db, escalation_result(ts=1000.0), now=1000.0,
                            dedup_seconds=600.0, repeat_seconds=0.0)
    _, created = emergency.create_if_new(tmp_db, escalation_result(ts=1400.0),
                                         now=1400.0, dedup_seconds=600.0,
                                         repeat_seconds=0.0)
    assert created is False                    # repeat suppressed by default
    _, created2 = emergency.create_if_new(tmp_db, escalation_result(ts=1400.0),
                                          now=1400.0, dedup_seconds=600.0,
                                          repeat_seconds=100.0)
    assert created2 is True                    # 400s > 100s repeat threshold


# --------------------------------------------------------------------------- #
# dispatch_alert integration (what fusion_engine calls each tick)
# --------------------------------------------------------------------------- #
def test_dispatch_alert_creates_emergency_alert_and_outbox(tmp_db):
    from backend import caretaker_api
    eid = caretaker_api.dispatch_alert(escalation_result(), db_path=tmp_db)
    assert eid and eid.startswith("emg-")

    from backend.db import get_conn
    conn = get_conn(tmp_db)
    try:
        alerts = conn.execute("SELECT id, event_id, severity FROM alerts"
                              ).fetchall()
        emg = conn.execute("SELECT * FROM emergency_events").fetchall()
    finally:
        conn.close()
    assert len(alerts) == 1 and alerts[0]["event_id"] == eid
    assert len(emg) == 1 and emg[0]["state"] == "active"

    counts = outbox.counts(tmp_db)
    assert counts["queued"] == 1               # emergency queued for sync


def test_dispatch_alert_retry_does_not_realarm_or_renotify(tmp_db, monkeypatch):
    from backend import caretaker_api

    fired = []
    monkeypatch.setattr(emergency, "fire_local_alarm",
                        lambda db, row, now=None: fired.append(row["event_id"]))

    eid1 = caretaker_api.dispatch_alert(escalation_result(), db_path=tmp_db)
    eid2 = caretaker_api.dispatch_alert(escalation_result(), db_path=tmp_db)
    assert eid1 == eid2
    assert fired == [eid1]                      # alarm fired exactly once

    from backend.db import get_conn
    conn = get_conn(tmp_db)
    try:
        n_alerts = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    finally:
        conn.close()
    assert n_alerts == 1                        # no duplicate alert row


def test_local_alarm_command_is_invoked(tmp_db, monkeypatch):
    monkeypatch.setenv("SAFEBATH_LOCAL_ALARM", "safe-alarm-cmd")
    calls = []

    class FakePopen:
        def __init__(self, argv, *a, **k):
            calls.append(argv)

    monkeypatch.setattr(emergency.subprocess, "Popen", FakePopen)
    emg, _ = emergency.create_if_new(tmp_db, escalation_result())
    emergency.fire_local_alarm(tmp_db, emg)
    assert calls and calls[0][0] == "safe-alarm-cmd"
    assert calls[0][1] == emg["event_id"]
    # local_alarm_fired is recorded so the dashboard knows it happened offline
    assert emergency.list_events(tmp_db)[0]["local_alarm_fired"] == 1


# --------------------------------------------------------------------------- #
# Push notification fan-out
# --------------------------------------------------------------------------- #
def test_notifications_queued_once_per_subscription(tmp_db):
    push_service.subscribe(tmp_db, "https://push.example/a", "p1", "a1")
    push_service.subscribe(tmp_db, "https://push.example/b", "p2", "a2")
    emg, _ = emergency.create_if_new(tmp_db, escalation_result())

    assert emergency.queue_notifications(tmp_db, emg) == 2
    emergency.queue_notifications(tmp_db, emg)          # idempotent
    summary = push_service.delivery_summary(tmp_db)
    assert summary["queued"] == 2
    assert push_service.count_subscriptions(tmp_db) == 2


# --------------------------------------------------------------------------- #
# Acknowledgement
# --------------------------------------------------------------------------- #
def test_acknowledge_updates_state_and_queues_cloud_ack(tmp_db):
    emg, _ = emergency.create_if_new(tmp_db, escalation_result())
    eid = emg["event_id"]
    assert emergency.acknowledge(tmp_db, eid, by="nurse") is True

    row = emergency.list_events(tmp_db)[0]
    assert row["state"] == "acknowledged"
    assert row["ack_by"] == "nurse"
    assert row["ack_count"] == 1

    acks = outbox.pending_by_kind(tmp_db, "ack")
    assert len(acks) == 1 and acks[0]["event_id"] == f"ack-{eid}"
    # repeated taps don't duplicate the cloud ack
    emergency.acknowledge(tmp_db, eid, by="nurse")
    assert len(outbox.pending_by_kind(tmp_db, "ack")) == 1


def test_caretaker_ack_endpoints(tmp_db, monkeypatch):
    pytest.importorskip("fastapi")
    TestClient = pytest.importorskip("fastapi.testclient").TestClient
    import backend.api_server as api
    import backend.caretaker_api as ct
    monkeypatch.setattr(api, "DB_PATH", tmp_db)
    monkeypatch.setattr(ct, "DB_PATH", tmp_db)

    from backend import caretaker_api
    eid = caretaker_api.dispatch_alert(escalation_result(), db_path=tmp_db)
    client = TestClient(api.app)

    listed = client.get("/caretaker/emergencies").json()["emergencies"]
    assert len(listed) == 1 and listed[0]["event_id"] == eid
    assert listed[0]["state"] == "active"

    r = client.post(f"/caretaker/emergencies/{eid}/ack?by=carer")
    assert r.status_code == 200 and r.json()["state"] == "acknowledged"
    assert client.get("/caretaker/emergencies").json(
    )["emergencies"][0]["state"] == "acknowledged"


def test_caretaker_push_subscribe_and_vapid_key(tmp_db, monkeypatch):
    pytest.importorskip("fastapi")
    TestClient = pytest.importorskip("fastapi.testclient").TestClient
    import backend.api_server as api
    import backend.caretaker_api as ct
    monkeypatch.setattr(api, "DB_PATH", tmp_db)
    monkeypatch.setattr(ct, "DB_PATH", tmp_db)
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "pub")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "priv")
    client = TestClient(api.app)

    assert client.get("/caretaker/push/vapid-public-key").json()["public_key"] \
        == "pub"
    body = {"endpoint": "https://push.example/x",
            "keys": {"p256dh": "k1", "auth": "k2"}}
    assert client.post("/caretaker/push/subscribe", json=body).json()["ok"]
    assert push_service.count_subscriptions(tmp_db) == 1
    assert client.post("/caretaker/push/unsubscribe",
                       json={"endpoint": "https://push.example/x"}).json()["ok"]
    assert push_service.count_subscriptions(tmp_db) == 0


# --------------------------------------------------------------------------- #
# Voice Bridge over HTTP (Board5 prompt -> Board4 claim -> reply)
# --------------------------------------------------------------------------- #
def test_voice_claim_flow(tmp_db):
    from backend import ingestion_api as ing
    ing.create_voice_request(tmp_db, time.time(), "are_you_ok")

    claimed = ing.claim_voice_request(tmp_db, "audio-01", lease=8.0)
    assert claimed and claimed["prompt"] == "are_you_ok"
    # a second board cannot claim the same leased request
    assert ing.claim_voice_request(tmp_db, "audio-02") is None

    ing.record_voice_reply(tmp_db, "fine", now=time.time())
    # after a reply the request is closed (even once the lease would expire)
    assert ing.claim_voice_request(tmp_db, "audio-01",
                                   now=time.time() + 999) is None


def test_voice_play_and_listen_are_independent(tmp_db):
    """Board 5 (speaker) plays, Board 4 (mic) listens — same request, two leases."""
    from backend import ingestion_api as ing
    ing.create_voice_request(tmp_db, time.time(), "are_you_ok")

    played = ing.claim_voice_request(tmp_db, "tof-01", role="play")
    assert played and played["role"] == "play" and played["prompt"] == "are_you_ok"
    # a prompt is spoken at most once
    assert ing.claim_voice_request(tmp_db, "tof-01", role="play") is None

    listened = ing.claim_voice_request(tmp_db, "audio-01", role="listen")
    assert listened and listened["role"] == "listen"

    # closing the request with a reply does not retroactively un-play it,
    # and playback never reopens a closed request
    ing.record_voice_reply(tmp_db, "help", now=time.time())
    assert ing.claim_voice_request(tmp_db, "tof-01", role="play") is None
    assert ing.claim_voice_request(tmp_db, "audio-02", role="listen") is None


def _envelope(device_id, module, message_type, payload, seq=1):
    return {
        "contract_version": 1, "device_id": device_id, "module": module,
        "firmware_version": "1.2.0", "boot_id": "A1B2C3D4E5F60718",
        "sequence": seq, "device_uptime_ms": 1000,
        "timestamp": time.time(), "message_type": message_type,
        "sensor_status": "ok", "payload": payload,
    }


def test_voice_endpoints_roundtrip(tmp_db, monkeypatch):
    pytest.importorskip("fastapi")
    TestClient = pytest.importorskip("fastapi.testclient").TestClient
    import backend.api_server as api
    import backend.ingestion_api as ing
    from backend.telemetry_store import get_tracker
    monkeypatch.setattr(api, "DB_PATH", tmp_db)
    monkeypatch.setattr(ing, "DB_PATH", tmp_db)
    get_tracker().reset()
    client = TestClient(api.app)

    assert client.get("/api/v1/voice/pending").json()["has_request"] is False

    # Board 5 posts the prompt event -> opens a claimable request
    assert client.post("/api/v1/event", json={
        "contract_version": 1, "device_id": "tof-01", "module": "tof",
        "firmware_version": "1.2.0", "boot_id": "A1B2C3D4E5F60718",
        "sequence": 1, "device_uptime_ms": 1000, "timestamp": time.time(),
        "message_type": "event", "sensor_status": "ok",
        "payload": {"prompt": "are_you_ok"},
    }).status_code == 200

    pending = client.get("/api/v1/voice/pending?device_id=audio-01").json()
    assert pending["has_request"] is True
    assert pending["prompt"] == "are_you_ok"

    # a second claim finds nothing (the request is leased)
    assert client.get("/api/v1/voice/pending?device_id=audio-02"
                      ).status_code == 403

    # Board 5 (speaker) claims the same request to play, independently
    play = client.get("/api/v1/voice/pending?device_id=tof-01&role=play").json()
    assert play["has_request"] is True and play["role"] == "play"
    assert client.get("/api/v1/voice/pending?device_id=tof-01&role=play"
                      ).json()["has_request"] is False   # played once

    # Board 4 answers -> request closed
    assert client.post("/api/v1/event", json={
        "contract_version": 1, "device_id": "audio-01", "module": "audio",
        "firmware_version": "1.2.0", "boot_id": "BBBBBBBBBBBBBBBB",
        "sequence": 1, "device_uptime_ms": 1000, "timestamp": time.time(),
        "message_type": "event", "sensor_status": "ok",
        "payload": {"reply": "fine"},
    }).status_code == 200

    from backend.db import get_conn
    conn = get_conn(tmp_db)
    try:
        req = conn.execute("SELECT reply FROM voice_requests "
                           "ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    assert req["reply"] == "fine"


def test_voice_pending_checks_configured_device_token(tmp_db, monkeypatch):
    pytest.importorskip("fastapi")
    TestClient = pytest.importorskip("fastapi.testclient").TestClient
    import backend.api_server as api
    import backend.ingestion_api as ing
    from backend.device_manager import Device, device_manager

    monkeypatch.setattr(api, "DB_PATH", tmp_db)
    monkeypatch.setattr(ing, "DB_PATH", tmp_db)
    monkeypatch.setitem(
        device_manager.devices, "tof-01",
        Device("tof-01", "tof", "Voice Output", token="test-node-token"))
    client = TestClient(api.app)
    url = "/api/v1/voice/pending?device_id=tof-01&role=play"

    assert client.get(url).status_code == 403
    assert client.get(url, headers={"X-Node-Token": "wrong"}).status_code == 403
    assert client.get("/api/v1/voice/pending?device_id=audio-01&role=play",
                      headers={"X-Node-Token": "test-node-token"}).status_code == 403
    accepted = client.get(url, headers={"X-Node-Token": "test-node-token"})
    assert accepted.status_code == 200
    assert accepted.json() == {"has_request": False}


def test_config_endpoint_is_secret_free(tmp_db, monkeypatch):
    pytest.importorskip("fastapi")
    TestClient = pytest.importorskip("fastapi.testclient").TestClient
    import backend.api_server as api
    import backend.ingestion_api as ing
    monkeypatch.setattr(api, "DB_PATH", tmp_db)
    monkeypatch.setattr(ing, "DB_PATH", tmp_db)
    data = TestClient(api.app).get("/api/v1/config").json()
    assert data["contract_version"] == 1
    assert "server_time" in data and data["voice_lease_s"] == 8
    assert "endpoints" in data
    # never leak credentials/tokens through the config primer
    assert "token" not in json.dumps(data).lower()
