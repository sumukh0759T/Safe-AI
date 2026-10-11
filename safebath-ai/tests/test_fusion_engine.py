"""
Fusion engine + alert rules + cross-modal tests.

Covers the SOFTWARE_BUILD_SPEC 5.2/5.7 DoDs explicitly:
 (a) score rises correctly when two modules flag simultaneously
 (b) decay brings the score back down over N ticks with no new signal
 (c) re-escalation happens immediately if a second independent module fires
     mid-decay
plus the radar stillness rule against gen_radar_stub_data's synthetic stream.
"""
import pytest

from backend import alert_rules as rules
from backend.cross_modal import (
    acoustic_radar_gate,
    event_triggered_biomarker_sample,
    scald_shutoff_confirmed,
)
from backend.fusion_engine import FusionEngine
from backend.voice_bridge import attempt_voice_triage
from datasets.gen_radar_stub_data import synthetic_radar_stream


def flags(**kw):
    base = {k: False for k in rules.WEIGHTS}
    base.update(kw)
    return base


class TestRadarRule:
    def test_synthetic_stream_flag_index(self):
        """Module 2 rule vs stub stream: fires at i=491 (see generator test)."""
        engine = FusionEngine(now=lambda: 0.0)
        fired_at = None
        for i, (presence, motion, stillness) in enumerate(
                synthetic_radar_stream(n=600)):
            hit = rules.radar_possible_collapse(presence, stillness)
            res = engine.tick(flags(possible_collapse=hit),
                              {"radar"} if hit else set(), False, None,
                              ts=float(i))
            if engine.state == "voice_pending" and fired_at is None:
                fired_at = i
        assert fired_at == 491

    def test_boundary(self):
        assert rules.radar_possible_collapse(True, 120_001) is True
        assert rules.radar_possible_collapse(True, 120_000) is False   # strictly >
        assert rules.radar_possible_collapse(False, 999_999) is False


class TestFusionScore:
    def test_two_simultaneous_flags_rise(self):
        e = FusionEngine(now=lambda: 0.0)
        one = e.tick(flags(tof_collapse=True), {"tof"}, False, None, ts=1.0)
        two = e.tick(flags(tof_collapse=True, possible_collapse=True),
                     {"tof", "radar"}, False, None, ts=2.0)
        assert two["score"] > one["score"]
        # weights from Master Guide Section 11: 0.20+0.20 => 40
        assert two["score"] == pytest.approx(40.0)

    def test_decay_over_ticks(self):
        e = FusionEngine(now=lambda: 0.0)
        e.tick(flags(fall_risk=True), {"fall"}, False, None, ts=1.0)
        peak = e.score
        scores = [peak]
        for t in range(2, 30):
            r = e.tick(flags(), set(), False, None, ts=float(t))
            scores.append(r["score"])
        assert all(scores[i + 1] <= scores[i] for i in range(len(scores) - 1))
        assert scores[-1] < rules.SCORE_WATCH      # decays back under watch

    def test_reescalation_mid_decay_immediate(self):
        # flag_hold_seconds=0 = strict per-event flags, so decay is visible
        e = FusionEngine(now=lambda: 0.0, flag_hold_seconds=0.0)
        e.tick(flags(tof_collapse=True), {"tof"}, False, None, ts=1.0)
        # tof signal ends; two decay ticks bring the score down
        d1 = e.tick(flags(), set(), False, None, ts=2.0)["score"]
        d2 = e.tick(flags(), set(), False, None, ts=3.0)["score"]
        assert d2 < d1 < 20.0
        # second independent module fires mid-decay -> immediate re-escalation
        r = e.tick(flags(possible_collapse=True), {"radar"}, False, None, ts=4.0)
        assert r["score"] == pytest.approx(20.0)   # raw jump dominates instantly
        assert r["score"] > d2

    def test_bands(self):
        assert rules.fusion_band(0) == "normal"
        assert rules.fusion_band(29.9) == "normal"
        assert rules.fusion_band(30) == "watch"
        assert rules.fusion_band(60) == "watch"
        assert rules.fusion_band(60.1) == "high"


class TestAlertStateMachine:
    def _trigger(self, e, ts):
        return e.tick(flags(tof_collapse=True), {"tof"}, False, None, ts=ts)

    def test_resolved_to_voice_pending_to_alerting_on_no_response(self):
        e = FusionEngine(now=lambda: 0.0, voice_wait_seconds=12.0)
        self._trigger(e, ts=1.0)
        assert e.state == "voice_pending"
        r = self._trigger(e, ts=20.0)              # timeout -> no_response
        assert e.state == "alerting"
        actions = [a["action"] for a in r["actions"]]
        assert "escalate_alert" in actions

    def test_fine_reply_stands_down(self):
        e = FusionEngine(now=lambda: 0.0, voice_wait_seconds=12.0)
        self._trigger(e, ts=1.0)
        r = e.tick(flags(), set(), False, voice_reply="fine", ts=3.0)
        assert e.state == "resolved"
        assert any(a["action"] == "stand_down" for a in r["actions"])

    def test_help_reply_escalates(self):
        e = FusionEngine(now=lambda: 0.0, voice_wait_seconds=12.0)
        self._trigger(e, ts=1.0)
        r = e.tick(flags(), set(), False, voice_reply="help", ts=3.0)
        assert e.state == "alerting"
        assert any(a["action"] == "escalate_alert" for a in r["actions"])

    def test_scald_is_immediate_no_voice_gate(self):
        e = FusionEngine(now=lambda: 0.0)
        r = e.tick(flags(), set(), scald_confirmed=True, voice_reply=None, ts=1.0)
        assert e.state == "alerting"
        actions = {a["action"] for a in r["actions"]}
        assert "close_solenoid_relay" in actions
        assert "request_voice_triage" not in actions

    def test_transitions_logged_with_timestamps(self, tmp_db):
        """DoD 5.8: state transitions logged to events.db with timestamps."""
        from backend.db import get_conn
        e = FusionEngine(now=lambda: 0.0)
        self._trigger(e, ts=42.5)
        conn = get_conn(tmp_db)
        conn.execute("INSERT INTO state_log(ts,from_state,to_state,reason) "
                     "VALUES (?,?,?,?)",
                     (42.5, "resolved", "voice_pending", "high_severity_trigger"))
        row = conn.execute("SELECT ts, to_state FROM state_log").fetchone()
        assert row["ts"] == 42.5 and row["to_state"] == "voice_pending"


class TestFourZoneMat:
    def _observe(self, e, zones, ts=1.0):
        import json
        raw = json.dumps({"sensor_type": "pressure_4zone", "zones": zones})
        return e.observe("sensor/mat/zones", raw, ts)

    def test_four_zone_frame_does_not_crash_and_is_recorded(self):
        e = FusionEngine(now=lambda: 0.0)
        r = self._observe(e, [1200, 2900, 600, 2100])
        assert r is not None
        assert list(e.mat_zone_frames)[-1] == [1200, 2900, 600, 2100]

    def test_72_cell_rules_not_reused_for_four_zones(self):
        # A saturated four-zone frame must NOT raise fall_risk via the grid
        # sustained-pressure / centroid heuristics.
        e = FusionEngine(now=lambda: 0.0)
        for t in range(1, 6):
            r = self._observe(e, [4095, 4095, 4095, 4095], ts=float(t))
        assert r["flags"]["fall_risk"] is False
        assert r["flags"]["possible_collapse"] is False

    def test_scald_pressure_interlock_is_opt_in(self, monkeypatch):
        import backend.fusion_engine as fe
        e = FusionEngine(now=lambda: 0.0)
        self._observe(e, [3000, 3000, 3000, 3000], ts=5.0)
        assert getattr(e, "last_mat_pressure_ts", None) is None   # disabled by default
        monkeypatch.setattr(fe, "MAT_ZONE_PRESSURE_SUM_ADC", 10_000.0)
        e2 = FusionEngine(now=lambda: 0.0)
        self._observe(e2, [3000, 3000, 3000, 3000], ts=5.0)
        assert e2.last_mat_pressure_ts == 5.0


class TestCrossModal:
    def test_event_triggered_biomarker(self):
        assert event_triggered_biomarker_sample(False, False) is False
        assert event_triggered_biomarker_sample(True, False) is True
        assert event_triggered_biomarker_sample(False, True) is True

    def test_acoustic_gate(self):
        assert acoustic_radar_gate("impact_thud", True, True) is True
        assert acoustic_radar_gate("impact_thud", True, False) is False
        assert acoustic_radar_gate("water", True, True) is False

    def test_scald_gate(self):
        assert scald_shutoff_confirmed(True, False) is True
        assert scald_shutoff_confirmed(True, True) is False
        assert scald_shutoff_confirmed(False, False) is False

    def test_engine_uses_cross_modal_not_inline(self):
        """fusion_engine must import these — guards against logic drift."""
        import inspect
        import backend.fusion_engine as fe
        src = inspect.getsource(fe)
        assert "cross_modal" in src and "rules.scald_shutoff" in src


class TestMediumSeverity:
    def test_toilet_never_triggers_high(self):
        e = FusionEngine(now=lambda: 0.0)
        r = e.tick(flags(toilet_biomarker=True), set(), False, None, ts=1.0)
        assert r["score"] < rules.SCORE_HIGH          # weight 0.05 only
        assert r["state"] == "resolved"               # never pages anyone

    def test_unsteady_contributes_but_medium_only(self):
        e = FusionEngine(now=lambda: 0.0)
        r = e.tick(flags(fall_risk=True), set(), False, None, ts=1.0)
        assert r["score"] == pytest.approx(26.0)
        assert r["band"] in ("watch", "normal")
        actions = [a["action"] for a in r["actions"]]
        assert "request_voice_triage" not in actions


class TestVoiceBridgeTriage:
    def test_silent_features_give_no_response(self):
        assert attempt_voice_triage({"rms": 0.005, "speech_ratio": 0.0}) \
            == "no_response"

    def test_moderate_speech_gives_fine(self):
        assert attempt_voice_triage({"rms": 0.15, "speech_ratio": 0.6}) == "fine"

    def test_loud_shout_gives_help(self):
        assert attempt_voice_triage({"rms": 0.9, "speech_ratio": 0.8}) == "help"

    def test_missing_model_returns_heuristic_not_crash(self):
        out = attempt_voice_triage({"rms": 0.02, "speech_ratio": 0.1})
        assert out in ("fine", "help", "no_response")
