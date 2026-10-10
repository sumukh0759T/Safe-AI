"""
Durable outbox + Supabase sync + push delivery tests.

The interesting requirement is offline-first behavior: when the cloud is down
everything stays queued with bounded backoff and survives a reboot; when it
comes back the same idempotent event_id is retried and marked synced; an auth
config error holds items instead of burning attempts. Everything is injected —
no network is touched.
"""
import time

import pytest

from backend import emergency, outbox, push_service, supabase_sync

WEIGHT_FLAGS = ["fall_risk", "possible_collapse", "tof_collapse",
                "acoustic_distress", "thermal_freeze", "toilet_biomarker"]


def escalation_result(reason="tof_collapse", ts=1000.0, score=40.0):
    return {
        "ts": ts, "score": score, "band": "high", "state": "alerting",
        "flags": {k: (k == "tof_collapse") for k in WEIGHT_FLAGS},
        "actions": [{"action": "escalate_alert", "reason": reason, "ts": ts}],
    }


class FakeClient:
    """Injectable client. First `fail_times` upserts fail, then succeed."""

    def __init__(self, fail_times=0, auth_error=False, ping_status=200):
        self.fail_times_left = fail_times
        self.auth_error = auth_error
        self.ping_status = ping_status
        self.upsert_calls = []
        self.ping_calls = 0

    def upsert(self, rows):
        self.upsert_calls.append(list(rows))
        if self.auth_error:
            raise PermissionError("Supabase auth failed (403)")
        if self.fail_times_left > 0:
            self.fail_times_left -= 1
            return [{"ok": False, "error": "connection refused"} for _ in rows]
        return [{"ok": True, "error": None} for _ in rows]

    def ping(self):
        self.ping_calls += 1
        if self.ping_status in (401, 403):
            raise PermissionError("auth")
        return self.ping_status


def _queue_emergency(tmp_db):
    from backend import emergency, outbox
    emg, _ = emergency.create_if_new(tmp_db, escalation_result())
    outbox.enqueue(tmp_db, "emergency", 0, event_id=emg["event_id"],
                   payload={"event_id": emg["event_id"]})
    return emg


@pytest.fixture(autouse=True)
def _no_real_supabase(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)


# --------------------------------------------------------------------------- #
# Outbox mechanics
# --------------------------------------------------------------------------- #
def test_outbox_enqueue_idempotent(tmp_db):
    eid = outbox.enqueue(tmp_db, "emergency", 1, event_id="emg-x",
                         payload={"a": 1})
    again = outbox.enqueue(tmp_db, "emergency", 1, event_id="emg-x",
                           payload={"a": 1})
    assert eid == again == "emg-x"
    assert outbox.counts(tmp_db)["queued"] == 1


def test_outbox_backoff_and_max_attempts(tmp_db):
    outbox.enqueue(tmp_db, "emergency", 1, event_id="emg-1")
    now = time.time()
    item = outbox.pending(tmp_db, now=now + 1)[0]
    last = now
    for _ in range(outbox.MAX_ATTEMPTS):
        outbox.mark_in_flight(tmp_db, item["id"], now=last)
        outbox.mark_failed(tmp_db, item["id"], "boom", now=last)
        last += 0.1
    # attempts exhausted -> permanently failed, never due again
    assert outbox.pending(tmp_db, now=last + 10_000_000) == []
    assert outbox.counts(tmp_db)["failed"] == 1


def test_outbox_inflight_lease_recovery_after_reboot(tmp_db):
    outbox.enqueue(tmp_db, "emergency", 1, event_id="emg-r")
    t0 = time.time()
    item = outbox.pending(tmp_db, now=t0 + 1)[0]
    outbox.mark_in_flight(tmp_db, item["id"], now=t0)
    # worker crashes here: row stays in_flight until the lease expires
    assert outbox.pending(tmp_db, now=t0 + 1) == []
    recovered = outbox.pending(
        tmp_db, now=t0 + outbox.IN_FLIGHT_LEASE_SECONDS + 1)[0]
    assert recovered["event_id"] == "emg-r"


# --------------------------------------------------------------------------- #
# sync_once against a (fake) cloud
# --------------------------------------------------------------------------- #
def test_sync_once_uploads_and_marks_synced(tmp_db):
    emg = _queue_emergency(tmp_db)
    client = FakeClient()
    report = supabase_sync.sync_once(tmp_db, client=client, now=time.time(),
                                     send_push=False)
    assert report["synced"] == 1 and report["failed"] == 0
    assert outbox.counts(tmp_db)["synced"] == 1
    assert client.upsert_calls[0][0]["event_id"] == emg["event_id"]


def test_sync_once_cloud_down_then_recovery(tmp_db):
    _queue_emergency(tmp_db)
    client = FakeClient(fail_times=1)
    t0 = time.time()
    r1 = supabase_sync.sync_once(tmp_db, client=client, now=t0, send_push=False)
    assert r1["failed"] == 1 and outbox.counts(tmp_db)["synced"] == 0

    # a later pass (backoff cleared) succeeds — queued item recovered
    t1 = t0 + outbox.BACKOFF_CAP + 1
    r2 = supabase_sync.sync_once(tmp_db, client=client, now=t1, send_push=False)
    assert r2["synced"] == 1 and outbox.counts(tmp_db)["synced"] == 1


def test_sync_once_auth_error_holds_items(tmp_db):
    _queue_emergency(tmp_db)
    client = FakeClient(auth_error=True)
    report = supabase_sync.sync_once(tmp_db, client=client, now=time.time(),
                                     send_push=False)
    assert report["synced"] == 0 and report["failed"] == 0
    assert any("auth" in e for e in report["errors"])
    # untouched: still queued, attempts not burned on a config error
    assert outbox.counts(tmp_db)["queued"] == 1
    assert outbox.counts(tmp_db)["failed"] == 0


def test_sync_once_no_env_reports_not_configured(tmp_db):
    _queue_emergency(tmp_db)
    report = supabase_sync.sync_once(tmp_db, client=None, now=time.time(),
                                     send_push=False)
    assert report["not_configured"] == 1
    assert outbox.counts(tmp_db)["synced"] == 0


# --------------------------------------------------------------------------- #
# Push delivery state machine
# --------------------------------------------------------------------------- #
def _queued_delivery(tmp_db, monkeypatch):
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "p")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "p")
    from backend import emergency, push_service
    push_service.subscribe(tmp_db, "https://push.example/a", "p", "a")
    emg, _ = emergency.create_if_new(tmp_db, escalation_result())
    emergency.queue_notifications(tmp_db, emg)
    return emg, push_service.queue_due_deliveries(tmp_db)[0]


def test_push_delivery_accepted(tmp_db, monkeypatch):
    from backend import push_service
    emg, row = _queued_delivery(tmp_db, monkeypatch)
    sent = []

    def fake_webpush(sub, payload, keys, claims):
        sent.append(payload)

    assert push_service.send_one(tmp_db, row, push_service.build_payload(emg),
                                 send_fn=fake_webpush) == "accepted"
    assert sent[0]["data"]["event_id"] == emg["event_id"]
    assert push_service.delivery_summary(tmp_db)["accepted"] == 1


def test_push_failure_retries_then_permanent(tmp_db, monkeypatch):
    from backend import push_service
    _, row = _queued_delivery(tmp_db, monkeypatch)

    def failing(sub, payload, keys, claims):
        raise RuntimeError("503 service unavailable")

    assert push_service.send_one(tmp_db, row, {}, send_fn=failing) == "queued"
    t = 1e18
    for _ in range(push_service.MAX_PUSH_ATTEMPTS + 1):
        rows = push_service.queue_due_deliveries(tmp_db, now=t)
        if not rows:
            break
        push_service.send_one(tmp_db, rows[0], {}, send_fn=failing, now=t)
        t += 10 ** 6
    assert push_service.delivery_summary(tmp_db)["failed"] == 1


def test_push_gone_subscription_disabled(tmp_db, monkeypatch):
    from backend import push_service
    _, row = _queued_delivery(tmp_db, monkeypatch)

    def gone(sub, payload, keys, claims):
        raise RuntimeError("410 Gone")

    assert push_service.send_one(tmp_db, row, {}, send_fn=gone) \
        == "subscription_disabled"
    assert push_service.count_subscriptions(tmp_db) == 0


def test_push_no_keys_stays_queued(tmp_db, monkeypatch):
    from backend import push_service
    _, row = _queued_delivery(tmp_db, monkeypatch)
    monkeypatch.delenv("VAPID_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("VAPID_PRIVATE_KEY", raising=False)
    assert push_service.send_one(tmp_db, row, {}) == "queued"
    assert push_service.delivery_summary(tmp_db)["queued"] == 1


# --------------------------------------------------------------------------- #
# Cloud reachability cache
# --------------------------------------------------------------------------- #
def test_cloud_status_probe_and_cache(tmp_db):
    from backend import supabase_sync as ss
    client = FakeClient()
    status = ss.probe_cloud(tmp_db, client=client)
    assert status["reachable"] is True and status["configured"] is True
    # a second call within the cache window does NOT probe again
    cached = ss.cloud_status(tmp_db, now=time.time() + 5)
    assert cached["reachable"] is True and client.ping_calls == 1


def test_cloud_status_not_configured(tmp_db):
    status = supabase_sync.cloud_status(tmp_db, now=0)
    assert status["configured"] is False and status["reachable"] is None


# --------------------------------------------------------------------------- #
# Device staleness + simulated labeling (dashboard truthfulness)
# --------------------------------------------------------------------------- #
def test_device_status_online_stale_offline(tmp_db, monkeypatch):
    import backend.api_server as api_server
    from backend.telemetry_store import init_telemetry, update_device_state
    init_telemetry(tmp_db)
    now = 100000.0
    monkeypatch.setattr(api_server.time, "time", lambda: now)

    update_device_state(tmp_db, "mat-01", "mat", "Pressure Mat", "1.0",
                        "B1", 1, 0, "ok", now - 5)             # online
    update_device_state(tmp_db, "radar-01", "radar", "Presence Radar", "1.0",
                        "B2", 1, 0, "ok", now - 300)           # stale
    update_device_state(tmp_db, "toilet-01", "toilet", "Toilet Biomarker",
                        "1.0", "B3", 1, 0, "ok", now - 5000)   # offline

    statuses = {d["device_id"]: d["status"]
                for d in api_server._device_statuses(tmp_db)}
    assert statuses["mat-01"] == "online"
    assert statuses["radar-01"] == "stale"
    assert statuses["toilet-01"] == "offline"
    assert statuses["audio-01"] == "offline"        # never seen


def test_simulated_labeling(tmp_db, monkeypatch):
    pytest.importorskip("fastapi")
    TestClient = pytest.importorskip("fastapi.testclient").TestClient
    import backend.api_server as api
    monkeypatch.setattr(api, "DB_PATH", tmp_db)
    client = TestClient(api.app)

    # an empty/bring-up DB is seeded with clearly-marked mock data
    assert client.get("/api/latest").json()["source"] == "simulated"

    from backend.db import get_conn
    conn = get_conn(tmp_db)
    try:
        conn.execute("DELETE FROM events")
        conn.execute("INSERT INTO events(ts,topic,payload) VALUES (?,?,?)",
                     (1.0, "sensor/radar/status",
                      '{"presence": true, "motion": false, "stillness_ms": 0,'
                      ' "possible_collapse": false}'))
        conn.commit()
    finally:
        conn.close()
    assert client.get("/api/latest").json()["source"] == "real"