"""
Risk Fusion Engine — Modules 2/7/8 combined.

Long-lived process: polls data/events.db, validates payloads against schemas,
maintains module flags, applies the weighted ensemble with exponential severity
decay + re-escalation (Master Guide Section 11 weights), runs the Voice-Bridge-
gated alert state machine, and writes risk_state/state_log back for api_server.

State machine (Module 8):
    resolved -> voice_pending -> resolved      (reply == "fine")
    resolved -> voice_pending -> alerting      (help / no_response / timeout)
    resolved -> alerting                       (immediate: scald shutoff only)
    alerting -> resolved                       (decayed band back to normal)
All transitions are logged to the state_log table with timestamps so a demo-day
post-hoc timeline can be reconstructed (SOFTWARE_BUILD_SPEC Section 5.8 DoD).
"""
import json
import os
import sys
import time
from collections import deque
from typing import Callable, Dict, List, Optional

# allow direct execution (python backend/fusion_engine.py) from any cwd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# models/*.h5 are trained+saved under tf_keras (Keras 2 — required for TFLite
# conversion; see training/convert_to_tflite.py). Keras 3 cannot deserialize
# their ConvLSTM layer ('time_major' kwarg), so pin the legacy stack before any
# tensorflow import happens in this process.
os.environ.setdefault("TF_USE_LEGACY_KERAS", "1")

from backend import alert_rules as rules
from backend import cross_modal
from backend.db import DEFAULT_DB, get_conn, init_db
from backend.schemas import MAT_COLS, MAT_ROWS, parse_for_topic

HIGH_SEVERITY_TRIGGERS = {"fall", "radar_collapse", "tof_collapse",
                          "thud_confirmed", "sustained_cry"}

# sum of all 72 cells above this == someone on the mat. Sized against the mock
# publisher's signal model (calm noise floor sums ~12k, full-body press ~200k)
# and scaled by cell count from the old 64-cell grid (60_000 * 72/64);
# recalibrate against real Velostat frames during Phase A bring-up.
MAT_PRESSURE_THRESHOLD = 67_500

# Four-zone mat (Pressure4ZonePayload / sensor/mat/zones). The rule-based grid
# heuristics below (MAT_PRESSURE_THRESHOLD, MAT_SUSTAIN_HIGH_SUM, CoP centroid
# jump) are defined on the 12x6 geometry and are therefore NOT reused for four
# zones — the sum/centroid of four zones means something different. The zone
# frames are still recorded (so nothing errors and history is kept) and may
# optionally feed the scald interlock via an explicit opt-in threshold:
#   SAFEBATH_MAT_ZONE_PRESSURE_SUM_ADC (>0) -> summed zone ADC counts as
#   "pressure present" for the unattended-hot-fill interlock. Default 0 =
#   disabled (no invented threshold).
try:
    MAT_ZONE_PRESSURE_SUM_ADC = float(
        os.environ.get("SAFEBATH_MAT_ZONE_PRESSURE_SUM_ADC", "0") or 0)
except ValueError:
    MAT_ZONE_PRESSURE_SUM_ADC = 0.0


class FusionEngine:
    def __init__(self,
                 decay: float = 0.9,
                 voice_wait_seconds: float = rules.VOICE_WAIT_SECONDS,
                 flag_hold_seconds: float = 5.0,
                 now: Callable[[], float] = time.time,
                 fall_classifier: Optional[Callable] = None,
                 toilet_model=None,
                 thermal_hazard_fn: Optional[Callable] = None):
        self.decay = decay
        self.voice_wait_seconds = voice_wait_seconds
        # Module signals arrive as separate MQTT events; a short persistence
        # window lets two modules that fired close together count as
        # "simultaneous" in the weighted ensemble (DoD 5.7 case a).
        self.flag_hold_seconds = flag_hold_seconds
        self.now = now
        self.fall_classifier = fall_classifier
        self.toilet_model = toilet_model
        self.thermal_hazard_fn = thermal_hazard_fn

        self.score = 0.0
        self.band = "normal"
        self.state = "resolved"
        self.state_changed_at = self.now()

        # --- rolling sensor state ---
        self.mat_frames: deque = deque(maxlen=20)
        self.mat_zone_frames: deque = deque(maxlen=20)
        self.fall_streak = 0
        self.unsteady_since: Optional[float] = None
        # interim rule-based mat danger (ConvLSTM upgrade later; same flag)
        self.mat_high_since: Optional[float] = None
        self.last_cop = None
        self.last_radar: Optional[Dict] = None
        self.cry_since: Optional[float] = None
        self.hot_fill_streak = 0
        self.uti_streak = 0
        self.dehyd_times: List[float] = []
        self.pending_reply_requested_at: Optional[float] = None
        self.normal_band_ticks_in_alerting = 0
        self._flag_holds: Dict[str, float] = {}

        self.active_flags: Dict[str, bool] = {}

    # ------------------------------------------------------------------ #
    def _log_transition(self, actions: List[Dict], from_state: str,
                        to_state: str, reason: str, ts: float) -> None:
        self.state = to_state
        self.state_changed_at = ts
        actions.append({"action": "state_change", "from": from_state,
                        "to": to_state, "reason": reason, "ts": ts})

    # ------------------------------------------------------------------ #
    def tick(self, flags: Dict[str, bool], high_triggers: set,
             scald_confirmed: bool, voice_reply: Optional[str],
             ts: Optional[float] = None) -> Dict:
        """One fusion step: score -> band -> alert state machine -> actions."""
        ts = self.now() if ts is None else ts
        actions: List[Dict] = []

        # -- Module 7: weighted ensemble + exponential severity decay ------
        # expire stale holds, extend holds for currently-flagged modules,
        # then score the union so near-simultaneous flags combine
        for k in [k for k, exp in self._flag_holds.items() if exp <= ts]:
            del self._flag_holds[k]
        effective = dict(flags)
        for k, exp in self._flag_holds.items():
            effective.setdefault(k, False)
            effective[k] = effective[k] or True
        for k, v in flags.items():
            if v:
                self._flag_holds[k] = ts + self.flag_hold_seconds

        raw = sum(rules.WEIGHTS[k] for k, v in effective.items() if v) * 100.0
        self.score = max(raw, self.score * self.decay)
        self.band = rules.fusion_band(self.score)
        self.active_flags = {k: bool(v) for k, v in effective.items()}

        # -- Module 8: alert resolution state machine -----------------------
        from_state = self.state

        if scald_confirmed:
            # water safety can't wait on a conversation (Master Guide Section 7)
            actions.append({"action": "close_solenoid_relay", "ts": ts})
            actions.append({"action": "escalate_alert", "channel": "telegram_push",
                            "reason": "unattended_hot_fill_confirmed", "ts": ts})
            if self.state != "alerting":
                self._log_transition(actions, from_state, "alerting",
                                     "scald_shutoff_confirmed", ts)
            self.pending_reply_requested_at = None
            self.normal_band_ticks_in_alerting = 0
            return self._result(ts, actions)

        wants_gate = self.band == "high" or bool(high_triggers)

        if self.state == "resolved":
            if wants_gate:
                actions.append({"action": "request_voice_triage", "prompt": "are_you_ok",
                                "ts": ts})
                self._log_transition(actions, from_state, "voice_pending",
                                     "high_severity_trigger", ts)
                self.pending_reply_requested_at = ts
        elif self.state == "voice_pending":
            waited = ts - (self.pending_reply_requested_at or ts)
            if voice_reply == "fine":
                actions.append({"action": "stand_down", "ts": ts})
                self._log_transition(actions, from_state, "resolved", "reply_fine", ts)
                self.score = min(self.score, rules.SCORE_HIGH - 1.0)
                self.band = rules.fusion_band(self.score)
            elif voice_reply in ("help", "no_response") or \
                    waited > self.voice_wait_seconds:
                reason = f"reply_{voice_reply}" if voice_reply else "no_response_timeout"
                actions.append({"action": "escalate_alert", "channel": "telegram_push",
                                "reason": reason, "ts": ts})
                self._log_transition(actions, from_state, "alerting", reason, ts)
        elif self.state == "alerting":
            self.normal_band_ticks_in_alerting = \
                self.normal_band_ticks_in_alerting + 1 if self.band == "normal" else 0
            if self.normal_band_ticks_in_alerting >= 3:
                actions.append({"action": "auto_resolve", "ts": ts})
                self._log_transition(actions, from_state, "resolved",
                                     "score_decayed_to_normal", ts)

        return self._result(ts, actions)

    def _result(self, ts: float, actions: List[Dict]) -> Dict:
        return {"ts": ts, "score": round(self.score, 2), "band": self.band,
                "state": self.state, "flags": dict(self.active_flags),
                "actions": actions}

    # ------------------------------------------------------------------ #
    def observe(self, topic: str, raw_payload: str, ts: float) -> Optional[Dict]:
        """Feed one validated MQTT event; returns a tick result or None."""
        payload = parse_for_topic(topic, raw_payload)
        data = payload.model_dump()
        flags: Dict[str, bool] = {k: False for k in rules.WEIGHTS}
        triggers: set = set()
        scald = False
        voice_reply: Optional[str] = None

        if topic == "sensor/mat/frame":
            grid = data["values"]
            self.mat_frames.append(grid)
            if sum(grid) > MAT_PRESSURE_THRESHOLD:
                self.last_mat_pressure_ts = ts

            # ---- interim RULE-BASED mat danger --------------------------------
            # Sustained total pressure + CoP jumps feed the SAME 'fall_risk'
            # flag the ConvLSTM drives; either signal can raise it. When real
            # training data lands and the model takes over, these rules stay
            # as a cheap sanity layer — fusion_engine's interface is unchanged.
            frame_sum = sum(grid)
            high = frame_sum > rules.MAT_SUSTAIN_HIGH_SUM
            if high:
                self.mat_high_since = self.mat_high_since or ts
            else:
                self.mat_high_since = None
            if self.mat_high_since and rules.mat_sustained_high_pressure(
                    frame_sum, ts - self.mat_high_since):
                flags["fall_risk"] = True
                triggers.add("mat_sustained_pressure")

            cop = rules.mat_centroid_cells(grid)
            if cop is not None and rules.mat_centroid_jump(self.last_cop, cop):
                flags["fall_risk"] = True
                triggers.add("mat_centroid_jump")
            self.last_cop = cop
            # -------------------------------------------------------------------

            label = None
            if self.fall_classifier is not None and len(self.mat_frames) >= 20:
                try:
                    label = int(self.fall_classifier(list(self.mat_frames)))
                except Exception:
                    label = None
            if label == 2:
                self.fall_streak += 1
            else:
                self.fall_streak = 0
            if rules.mat_fall_trigger(self.fall_streak):
                flags["fall_risk"] = True
                triggers.add("fall")
            if label == 1:
                self.unsteady_since = self.unsteady_since or ts
            else:
                self.unsteady_since = None
            if self.unsteady_since and \
                    rules.mat_unsteady_sustained(ts - self.unsteady_since):
                flags["fall_risk"] = True   # Medium path — contributes to score only

        elif topic == "sensor/mat/zones":
            # Four-zone mat: record the frame but deliberately do NOT reuse the
            # 72-cell rules (see MAT_ZONE_PRESSURE_SUM_ADC note above). This
            # keeps the grid fall/centroid heuristics from mis-scoring a shape
            # they were never calibrated for.
            zones = data["zones"]
            self.mat_zone_frames.append(zones)
            if MAT_ZONE_PRESSURE_SUM_ADC > 0 and \
                    sum(zones) > MAT_ZONE_PRESSURE_SUM_ADC:
                self.last_mat_pressure_ts = ts

        elif topic == "sensor/radar/status":
            self.last_radar = data
            if rules.radar_possible_collapse(data["presence"], data["stillness_ms"]) \
                    or data["possible_collapse"]:
                flags["possible_collapse"] = True
                triggers.add("radar_collapse")

        elif topic == "sensor/toilet/reading":
            cls_name = "normal"
            if self.toilet_model is not None:
                import numpy as np
                feats = [[data["gas_ppm"], data["voc_ppm"], data["turbidity"]]]
                cls_name = str(self.toilet_model.predict(
                    np.array(feats))[0])
            if cls_name == "uti_risk":
                self.uti_streak += 1
            else:
                self.uti_streak = 0
            if cls_name == "dehydration_risk":
                self.dehyd_times = [t for t in self.dehyd_times if ts - t <= 86400]
                self.dehyd_times.append(ts)
            if rules.toilet_uti_flag(self.uti_streak) or \
                    rules.toilet_dehydration_flag(len(self.dehyd_times)):
                flags["toilet_biomarker"] = True   # Medium — never pages anyone

        elif topic == "sensor/audio/event":
            label = data["label"]
            # 6-class audio contract (backend/schemas.AudioLabel): a sustained
            # distress call is cry_or_scream (the old single "speech_or_cry"
            # bucket was split; ordinary_speech is NOT distress).
            if label == "cry_or_scream":
                self.cry_since = self.cry_since or ts
            else:
                self.cry_since = None
            radar_p = bool(self.last_radar["presence"]) if self.last_radar else False
            radar_m = bool(self.last_radar["motion"]) if self.last_radar else False
            if rules.acoustic_thud_confirmed(label, radar_p, radar_m):
                flags["acoustic_distress"] = True
                triggers.add("thud_confirmed")
            elif self.cry_since and \
                    rules.acoustic_sustained_cry(ts - self.cry_since):
                flags["acoustic_distress"] = True
                triggers.add("sustained_cry")

        elif topic == "sensor/tof_thermal/status":
            # Module 5 rule — firmware owns the live decision; this mirrors it
            # so the backend reacts even if Board 5's own voice trigger missed.
            if rules.tof_collapse_trigger(data["tof_collapse"]):
                flags["tof_collapse"] = True
                triggers.add("tof_collapse")
            hazard = False
            if self.thermal_hazard_fn is not None:
                try:
                    hazard = bool(self.thermal_hazard_fn(data))
                except Exception:
                    hazard = False
            else:
                # bring-up fallback heuristic until models/thermal_freeze_model.h5 exists
                hazard = data["avg_thermal_c"] > 38.0
            self.hot_fill_streak = self.hot_fill_streak + 1 if hazard else 0
            if hazard or data["avg_thermal_c"] > 45.0:
                flags["thermal_freeze"] = True
            pressure_present = getattr(self, "last_mat_pressure_ts", None) is not None \
                and ts - self.last_mat_pressure_ts < 10.0
            if rules.scald_shutoff(data["unattended_hot_fill"], pressure_present):
                scald = True

        elif topic == "voice/reply":
            voice_reply = data["reply"]

        return self.tick(flags, triggers, scald, voice_reply, ts)


# ------------------------------------------------------------------------- #
def load_optional_models():
    """Best-effort loading of trained artifacts; everything degrades cleanly.

    The fall ConvLSTM expects the LEGACY 12x6 grid (72 cells). It is only ever
    invoked from the `sensor/mat/frame` branch, so four-zone frames (which land
    on `sensor/mat/zones`) can never reach it and no zone data is padded into a
    grid. Four-zone falls use the explicit rule-based path instead.
    """
    toilet_model = fall_clf = None

    try:
        import joblib, os
        p = os.path.join("models", "toilet_rf_model.pkl")
        if os.path.exists(p):
            toilet_model = joblib.load(p)
    except Exception as e:
        print(f"[fusion] toilet model unavailable: {e}")

    try:
        import os
        p = os.path.join("models", "fall_risk_model.h5")
        if os.path.exists(p):
            import tensorflow as tf  # heavy import only when artifact exists
            import numpy as np
            m = tf.keras.models.load_model(p)

            def fall_clf(frames_row_major, m=m, np=np):
                x = np.array(frames_row_major, dtype=np.float32) \
                      .reshape(1, 20, MAT_ROWS, MAT_COLS, 1) / 4095.0
                return int(np.argmax(m.predict(x, verbose=0)[0]))
    except Exception as e:
        print(f"[fusion] fall classifier unavailable: {e}")

    return toilet_model, fall_clf


def main(db_path: str = DEFAULT_DB, poll_seconds: float = 1.0,
         once: bool = False, from_start: bool = False) -> None:
    init_db(db_path)
    conn = get_conn(db_path)
    toilet_model, fall_clf = load_optional_models()
    engine = FusionEngine(toilet_model=toilet_model, fall_classifier=fall_clf)
    if from_start:
        last_id = 0
    else:
        last_id = conn.execute(
            "SELECT COALESCE(MAX(id),0) FROM events").fetchone()[0]
    print(f"[fusion] running against {db_path} from event id {last_id}"
          + (" (once)" if once else ""))
    prev_flags: Dict[str, bool] = {}
    last_heartbeat = 0.0
    while True:
        rows = conn.execute("SELECT id, ts, topic, payload FROM events "
                            "WHERE id > ? ORDER BY id", (last_id,)).fetchall()
        for r in rows:
            last_id = r["id"]
            try:
                result = engine.observe(r["topic"], r["payload"],
                                        r["ts"] or time.time())
            except Exception as e:
                print(f"[fusion] FAILED event {r['id']} ({r['topic']}): {e}")
                continue
            if result is None:
                continue
            # loud one-liner whenever a module flag newly switches on
            new_hits = sorted(k for k, v in result["flags"].items()
                              if v and not prev_flags.get(k))
            if new_hits:
                print(f"[fusion] DETECT {r['topic']} -> "
                      f"{'+'.join(new_hits)} | score={result['score']:.1f} "
                      f"band={result['band']} state={result['state']}")
            prev_flags = result["flags"]
            flags_csv = ",".join(k for k, v in result["flags"].items() if v)
            conn.execute("INSERT INTO risk_state(ts,score,band,state,active_flags)"
                         " VALUES (?,?,?,?,?)",
                         (result["ts"], result["score"], result["band"],
                          result["state"], flags_csv))
            for a in result["actions"]:
                if a["action"] == "state_change":
                    conn.execute("INSERT INTO state_log(ts,from_state,to_state,"
                                 "reason) VALUES (?,?,?,?)",
                                 (a["ts"], a["from"], a["to"], a["reason"]))
                    print(f"[fusion] {a['from']} -> {a['to']} ({a['reason']})")
                elif a["action"].startswith(("escalate", "close_", "request")):
                    print(f"[fusion] ACTION {a}")
            # dispatch caretaker alerts for escalations / high-severity flags
            try:
                from backend.caretaker_api import dispatch_alert
                dispatch_alert(result, conn, db_path)
            except Exception as e:
                print(f"[fusion] dispatch_alert failed for event {r['id']}: {e}")
        conn.commit()
        if once:
            print(f"[fusion] processed through event id {last_id}; "
                  f"score={engine.score:.1f} band={engine.band} "
                  f"state={engine.state}")
            break
        if rows and time.time() - last_heartbeat >= 10.0:
            print(f"[fusion] alive | score={engine.score:.1f} "
                  f"band={engine.band} state={engine.state} | at event {last_id}")
            last_heartbeat = time.time()
        time.sleep(poll_seconds)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--once", action="store_true",
                    help="process all pending events then exit")
    ap.add_argument("--from-start", action="store_true",
                    help="replay from the first event, not just new ones")
    args = ap.parse_args()
    main(db_path=args.db, once=args.once, from_start=args.from_start)
