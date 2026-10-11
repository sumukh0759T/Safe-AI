"""
Simulated hardware publisher — the piece that unblocks fully autonomous
building (SafeBath_AI_Network_And_Autonomous_Build_Guide.md Section 3).

Publishes realistic, schema-correct MQTT traffic for all 5 boards, including
periodic deliberate 'danger' spikes, so the entire pipeline — broker,
mqtt_bridge, fusion_engine, alert_rules, api_server, frontend — can be built
and self-tested end-to-end with zero physical hardware connected.
Swap this off (or just don't run it) once real boards are on the network;
nothing downstream changes.

Modes
-----
  --mode broker   real MQTT publishes to MQTT_BROKER_HOST:PORT (default).
                  Requires a running broker (e.g. mosquitto).
  --mode direct   no broker needed: feeds every payload through the SAME
                  validation+insert path used by the live bridge
                  (backend.mqtt_bridge.on_message -> data/events.db).

Scenarios
---------
  default    calm traffic; a danger spike every --spike-every ticks
             (guide Section 3.1 behavior: radar+ToF collapse flags, thud w/o
             radar-motion confirmation — which also demonstrates the
             cross-modal acoustic gate BLOCKING the thud escalation)
  collapse   sustained multi-board collapse window (radar + tof + confirmed
             thud + sustained cry) long enough to cross the Voice Bridge
             timeout and reach alerting/escalation, then recovery
  scald      unattended hot fill with nobody on the mat -> immediate relay
             shutoff path (no Voice Bridge wait)

Extra knobs
-----------
  --auto-reply fine|help|off   publish a voice/reply shortly after each danger
                               onset to exercise the stand-down / escalate
                               branches deterministically
  --chaos                      occasionally emit schema-invalid payloads to
                               prove mqtt_bridge rejects them loudly instead
                               of corrupting the fusion math
"""
import argparse
import json
import random
import time

# allow direct execution (python backend/mock_publisher.py) from any cwd
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

AUDIO_LABELS = [
    "silence",
    "background_noise",
    "impact_thud",
    "cry_or_scream",
    "ordinary_speech",
    "water_sound",
]

COLLAPSE_WINDOW_TICKS = 20      # > voice_wait_seconds/interval so timeout fires


# --------------------------------------------------------------------------- #
# payload builders (shapes match backend/schemas.py exactly)
# --------------------------------------------------------------------------- #
def mat_frame(danger=False, nobody=False):
    """72 comma-separated ints, 12x6 row-major, 12-bit range.

    nobody=True models an empty bathroom (scald scenario): calm noise floor
    even while the tap runs — that absence-of-pressure is what lets
    cross_modal.scald_shutoff_confirmed pass."""
    from backend.schemas import MAT_CELL_COUNT
    if nobody:
        vals = [random.randint(0, 400) for _ in range(MAT_CELL_COUNT)]
    else:
        vals = [random.randint(2800, 3800) if danger else random.randint(0, 400)
                for _ in range(MAT_CELL_COUNT)]
    return ",".join(map(str, vals))


def mat_pir(entry=None):
    return json.dumps({"entry": bool(random.random() < 0.5
                                     if entry is None else entry)})


def radar_status(danger=False, scenario="default"):
    collapse = danger and scenario != "scald"   # scald is a water event only
    return json.dumps({"presence": True, "motion": not (danger and scenario != "scald"),
                       "stillness_ms": 130000 if collapse else
                       random.choice([0, 200, 5000]),
                       "possible_collapse": collapse})


def toilet_reading(danger=False):
    return json.dumps({
        "gas_ppm": 320 if danger else random.uniform(45, 80),
        "voc_ppm": 90 if danger else random.uniform(18, 35),
        "turbidity": 95 if danger else random.uniform(1.5, 4.0),
        "hour_of_day": time.localtime().tm_hour,
    })


def audio_event(danger=False, scenario="default", tick_in_window=0):
    if danger:
        if scenario == "collapse":
            # confirmed thud early in window, then sustained distress call
            label = ("impact_thud" if tick_in_window < 3
                     else "speech_or_cry")
            energy = 0.9 if label == "impact_thud" else 0.55
        else:
            # NOTE: guide's default spike pairs impact_thud with motion=False,
            # so backend.cross_modal.acoustic_radar_gate must (correctly)
            # block its escalation — the radar confirms presence but NOT motion.
            label, energy = "impact_thud", 0.9
    else:
        label = random.choices(AUDIO_LABELS, weights=[40, 30, 5, 5, 15, 5])[0]
        energy = round(random.uniform(0.01, 0.25), 3)
    return json.dumps({"label": label, "energy": energy})


def tof_thermal(danger=False, scenario="default"):
    hot_fill = scenario == "scald" and danger
    return json.dumps({
        "avg_thermal_c": 41.5 if hot_fill else
                         (24.6 + random.uniform(-0.4, 1.2)),
        "flow_pulses": 80 if hot_fill else random.choice([0, 0, 0, 2]),
        "tof_collapse": danger and scenario != "scald",
        "unattended_hot_fill": hot_fill,
    })


INVALID_AUDIO = '{"label": "explosion", "energy": -5}'   # schema-invalid


# --------------------------------------------------------------------------- #
class MockPublisher:
    def __init__(self, scenario="default", interval=1.0, spike_every=40,
                 auto_reply="off", chaos=False, seed=7):
        self.scenario = scenario
        self.interval = interval
        self.spike_every = max(spike_every, 6)
        # collapse/scald windows start early so short test/demo runs see them;
        # default keeps the guide's "every N ticks" single-spike behavior
        self.onset = self.spike_every if scenario == "default" \
            else min(self.spike_every, 6)
        self.auto_reply = auto_reply
        self.chaos = chaos
        self.rng = random.Random(seed)
        self.tick = 0
        self.danger_onset_tick = None

    def is_danger(self):
        t = self.tick
        if self.scenario == "collapse":
            return t >= self.onset and t < self.onset + COLLAPSE_WINDOW_TICKS
        if self.scenario == "scald":
            return t >= self.onset and t < self.onset + 8
        return t % self.spike_every == 0 and t > 0     # guide default: 1 tick

    # ---- transport hooks ---------------------------------------------------
    def publish(self, topic: str, payload: str):
        raise NotImplementedError

    # ---- main loop ----------------------------------------------------------
    def emit(self):
        danger = self.is_danger()
        if danger and self.danger_onset_tick is None:
            self.danger_onset_tick = self.tick
            print(f"[mock_publisher] tick {self.tick}: DANGER ONSET "
                  f"(scenario={self.scenario})")

        tw = self.tick - self.danger_onset_tick if self.danger_onset_tick is not None else 99
        self.publish("sensor/mat/frame",
                     mat_frame(danger, nobody=self.scenario == "scald"))
        if self.tick % 5 == 0:                       # PIR occasionally
            self.publish("sensor/mat/pir", mat_pir())
        self.publish("sensor/radar/status", radar_status(danger, self.scenario))
        self.publish("sensor/toilet/reading", toilet_reading(danger))
        if self.scenario == "scald":
            # empty bathroom is quiet — no random cries muddying the scald path
            audio = json.dumps({"label": "silence", "energy": 0.01})
        else:
            audio = (INVALID_AUDIO if (self.chaos and self.tick % 17 == 0)
                     else audio_event(danger, self.scenario, tw))
        self.publish("sensor/audio/event", audio)
        self.publish("sensor/tof_thermal/status",
                     tof_thermal(danger, self.scenario))

        # deterministic reply injection right after danger onset
        if self.auto_reply != "off" and self.danger_onset_tick is not None \
                and tw == 3:
            self.publish("voice/reply",
                         json.dumps({"reply": self.auto_reply}))

        if self.danger_onset_tick is not None and \
                not self.is_danger() and tw >= COLLAPSE_WINDOW_TICKS:
            print(f"[mock_publisher] tick {self.tick}: danger cleared")
            self.danger_onset_tick = None

        self.tick += 1

    def run(self, max_ticks=None):
        while max_ticks is None or self.tick < max_ticks:
            self.emit()
            time.sleep(self.interval)


class BrokerPublisher(MockPublisher):
    """Real MQTT mode — requires a broker (mosquitto / any MQTT 3.1.1)."""

    def __init__(self, host="localhost", port=1883, **kw):
        super().__init__(**kw)
        import paho.mqtt.client as mqtt
        try:                                   # paho-mqtt >= 2.0
            self.client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
                client_id="SafeBathMockPublisher")
        except (AttributeError, TypeError):    # paho-mqtt 1.x
            self.client = mqtt.Client(client_id="SafeBathMockPublisher")
        self.client.connect(host, port, keepalive=30)
        self.client.loop_start()

    def publish(self, topic, payload):
        self.client.publish(topic, payload, qos=0)


class DirectPublisher(MockPublisher):
    """No-broker mode — same validate+store path the live bridge uses."""

    def __init__(self, db_path="data/events.db", **kw):
        super().__init__(**kw)
        from backend.db import get_conn, init_db
        from backend.mqtt_bridge import on_message
        init_db(db_path)
        self.conn = get_conn(db_path)
        self.on_message = on_message

    def publish(self, topic, payload):
        accepted = self.on_message(self.conn, topic, payload)
        if not accepted:
            print(f"[mock_publisher] tick {self.tick}: bridge REJECTED "
                  f"{topic} (schema violation logged)")


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", choices=["broker", "direct"], default="broker")
    ap.add_argument("--scenario", choices=["default", "collapse", "scald"],
                    default="default")
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--spike-every", type=int, default=40)
    ap.add_argument("--ticks", type=int, default=None,
                    help="stop after N ticks (default: run forever)")
    ap.add_argument("--auto-reply", choices=["fine", "help", "off"],
                    default="off")
    ap.add_argument("--chaos", action="store_true",
                    help="inject occasional invalid payloads")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--db", default="data/events.db")
    args = ap.parse_args()

    cls = BrokerPublisher if args.mode == "broker" else DirectPublisher
    pub = cls(scenario=args.scenario, interval=args.interval,
              spike_every=args.spike_every, auto_reply=args.auto_reply,
              chaos=args.chaos,
              **({"host": args.host, "port": args.port}
                 if args.mode == "broker" else {"db_path": args.db}))
    print(f"[mock_publisher] mode={args.mode} scenario={args.scenario} "
          f"interval={args.interval}s spike_every={args.spike_every}")
    try:
        pub.run(max_ticks=args.ticks)
    except KeyboardInterrupt:
        pass
    finally:
        print(f"[mock_publisher] done after {pub.tick} ticks")


if __name__ == "__main__":
    main()
