"""
Autonomous end-to-end verification (Network & Autonomous Build Guide Section 3):
mock_publisher traffic -> mqtt_bridge validation/insert -> fusion_engine +
alert_rules reactions -> api_server response shape. No broker, no hardware.
"""
import pytest

from backend.mock_publisher import (
    COLLAPSE_WINDOW_TICKS,
    DirectPublisher,
    MockPublisher,
)


class StreamToDb(DirectPublisher):
    """Mock publisher wired straight into the bridge's validate+insert path."""

    def __init__(self, db_path, **kw):
        kw.pop("db_path", None)
        super().__init__(db_path=db_path, **kw)

    def run_silent(self, ticks):
        for _ in range(ticks):
            self.emit()          # emit() only, no sleeping


def load_events(tmp_db):
    from backend.db import get_conn
    conn = get_conn(tmp_db)
    return conn.execute("SELECT id, ts, topic, payload FROM events "
                        "ORDER BY id").fetchall()


def drive_engine(tmp_db, voice_wait=12.0, seconds_per_event=1.0 / 6.0):
    """Replay every stored event through a fresh FusionEngine.

    Simulated clock: ~6 events per publisher tick, so 1 tick ~= 0.17 s —
    timeouts are exercised by stream length, not wall time."""
    from backend.fusion_engine import FusionEngine
    e = FusionEngine(now=lambda: 0.0, voice_wait_seconds=voice_wait)
    results = []
    for i, r in enumerate(load_events(tmp_db)):
        res = e.observe(r["topic"], r["payload"], i * seconds_per_event)
        if res is not None:
            results.append(res)
    return e, results


# --------------------------------------------------------------------------- #
def test_default_scenario_all_payloads_valid(tmp_db):
    pub = StreamToDb(tmp_db, spike_every=10, seed=3)
    pub.run_silent(ticks=25)
    n_invalid = count_invalid(tmp_db)
    assert n_invalid == 0


def count_invalid(tmp_db):
    from backend.db import get_conn
    conn = get_conn(tmp_db)
    return conn.execute("SELECT COUNT(*) c FROM invalid_events").fetchone()["c"]


class TestDangerSpikeAlertChain:
    def test_spike_triggers_voice_gate_then_timeout_escalation(self, tmp_db):
        # spikes at ticks 8/16/24/32; after the last spike, ~10 silent ticks
        # (~9 simulated seconds) exceed the 6 s voice-wait -> auto-escalation
        pub = StreamToDb(tmp_db, scenario="default", spike_every=8, seed=5)
        pub.run_silent(ticks=34)

        engine, results = drive_engine(tmp_db, voice_wait=6.0)

        # radar alone scores its weight (20); radar+tof together (flag hold
        # window) reach the combined 40+, per DoD 5.7 case (a)
        solo = [r["score"] for r in results
                if r["flags"].get("possible_collapse")
                and not r["flags"].get("tof_collapse")]
        assert solo and 20.0 in [pytest.approx(s) for s in solo]
        both = [r for r in results if r["flags"].get("possible_collapse")
                and r["flags"].get("tof_collapse")]
        assert both and min(r["score"] for r in both) == pytest.approx(40.0)

        transitions = {(r["ts"], a["from"], a["to"]) for r in results
                       for a in r["actions"] if a["action"] == "state_change"}
        # High-severity triggers -> Voice Bridge gate requested...
        assert any(to == "voice_pending" for _, _, to in transitions)
        # ...and since no reply arrives within 12 s of simulated time,
        # the pipeline escalates autonomously (guide Section 1 Step 5 chain)
        assert any(a["action"] == "escalate_alert"
                   for r in results for a in r["actions"])
        assert any(to == "alerting" for _, _, to in transitions)

    def test_cross_modal_gate_blocks_unconfirmed_thud(self, tmp_db):
        """Deterministic gate check: an impact_thud is only escalated when the
        radar independently confirms presence AND motion at that timestamp."""
        from backend.db import get_conn
        from backend.fusion_engine import FusionEngine
        from backend.mqtt_bridge import on_message

        conn = get_conn(tmp_db)
        on_message(conn, "sensor/radar/status",
                   '{"presence": true, "motion": false, "stillness_ms": 1000,'
                   ' "possible_collapse": false}')
        on_message(conn, "sensor/audio/event",
                   '{"label": "impact_thud", "energy": 0.9}')   # unconfirmed
        on_message(conn, "sensor/radar/status",
                   '{"presence": true, "motion": true, "stillness_ms": 0,'
                   ' "possible_collapse": false}')
        on_message(conn, "sensor/audio/event",
                   '{"label": "impact_thud", "energy": 0.9}')   # confirmed

        e = FusionEngine(now=lambda: 0.0)
        results = [r for r in
                   (e.observe(r["topic"], r["payload"], float(i))
                    for i, r in enumerate(load_events(tmp_db))) if r]

        thud_results = [r for r in results if r["flags"].get("acoustic_distress")]
        # exactly one acoustic escalation — the radar-CONFIRMED thud only
        assert len(thud_results) == 1
        confirmed = thud_results[0]
        assert e.state == "voice_pending"
        assert any(a["action"] == "request_voice_triage"
                   for a in confirmed["actions"])


class TestCollapseScenario:
    def test_full_chain_to_alerting_and_recovery(self, tmp_db):
        pub = StreamToDb(tmp_db, scenario="collapse", auto_reply="help",
                         seed=9)
        pub.run_silent(ticks=COLLAPSE_WINDOW_TICKS + 2)

        engine, results = drive_engine(tmp_db)
        actions = [a["action"] for r in results for a in r["actions"]]
        assert "escalate_alert" in actions
        transitions = {a["to"] for r in results for a in r["actions"]
                       if a["action"] == "state_change"}
        assert "alerting" in transitions

    def test_fine_reply_stands_down(self, tmp_db):
        pub = StreamToDb(tmp_db, scenario="collapse", auto_reply="fine",
                         seed=9)
        pub.run_silent(ticks=COLLAPSE_WINDOW_TICKS + 2)

        engine, results = drive_engine(tmp_db)
        actions = [a["action"] for r in results for a in r["actions"]]
        assert "stand_down" in actions
        assert "escalate_alert" not in actions


class TestScaldScenario:
    def test_immediate_relay_no_voice_gate(self, tmp_db):
        # nobody on the mat -> pressure proxy stays under threshold
        pub = StreamToDb(tmp_db, scenario="scald", spike_every=6, seed=11)
        pub.run_silent(ticks=12)

        engine, results = drive_engine(tmp_db)
        actions = [a["action"] for r in results for a in r["actions"]]
        assert "close_solenoid_relay" in actions
        assert "escalate_alert" in actions
        transitions = [(a["from"], a["to"]) for r in results
                       for a in r["actions"] if a["action"] == "state_change"]
        # scald goes straight to alerting — it never waits at the voice gate
        assert ("resolved", "voice_pending") not in transitions


def test_chaos_mode_rejected_loudly(tmp_db):
    pub = StreamToDb(tmp_db, chaos=True, spike_every=50, seed=13)
    pub.run_silent(ticks=20)           # invalid audio at ticks 0 & 17
    assert count_invalid(tmp_db) >= 1
    # valid traffic still flows alongside the rejected payloads
    assert len(load_events(tmp_db)) > 80


class TestApiServesSimulatedData:
    def test_populated_response_with_danger_levels(self, tmp_db, monkeypatch):
        fastapi = pytest.importorskip("fastapi")
        TestClient = pytest.importorskip("fastapi.testclient").TestClient

        pub = StreamToDb(tmp_db, scenario="default", spike_every=8, seed=5)
        pub.run_silent(ticks=18)

        # fusion pass, persisting risk_state exactly like main() does
        from backend.fusion_engine import FusionEngine
        from backend.db import get_conn
        e = FusionEngine(now=lambda: 0.0)
        conn = get_conn(tmp_db)
        last = None
        for i, r in enumerate(load_events(tmp_db)):
            res = e.observe(r["topic"], r["payload"], float(i))
            if res:
                conn.execute("INSERT INTO risk_state(ts,score,band,state,"
                             "active_flags) VALUES (?,?,?,?,?)",
                             (res["ts"], res["score"], res["band"],
                              res["state"],
                              ",".join(k for k, v in res["flags"].items() if v)))
        conn.commit()

        import backend.api_server as api
        monkeypatch.setattr(api, "DB_PATH", tmp_db)
        data = TestClient(api.app).get("/api/latest").json()

        assert data["risk_score"] > 0                 # fusion wrote risk_state
        modules = data["modules"]
        assert all(m["value"] is not None for m in modules.values())
        levels = [e["level"] for e in data["events"]]
        assert "danger" in levels or "warn" in levels
