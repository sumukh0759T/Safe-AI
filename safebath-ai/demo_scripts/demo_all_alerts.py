"""
SafeBath AI -- Full-Module Alert Demo Script

Runs through all 6 alert scenarios sequentially so you can show judges
the complete pipeline working end-to-end:

  1. MAT FALL     -- sustained high pressure + CoP jump -> fall_risk
  2. RADAR COLLAPSE -- motionless + presence -> possible_collapse
  3. THERMAL SCALD  -- hot water + nobody on mat -> scald_shutoff
  4. AUDIO DISTRESS -- impact thud + radar confirm -> acoustic_distress
  5. TOILET BIOMARKER -- high gas/VOC -> toilet_biomarker (Medium)
  6. TOF COLLAPSE  -- ToF reports collapse posture -> tof_collapse

After each scenario the script waits for you to press Enter before
moving to the next, giving time to show the caretaker dashboard
receiving the alert.

Usage (with Mosquitto running):
    .venv\\Scripts\\python.exe demo_scripts\\demo_all_alerts.py

Or directly against the DB (no broker):
    .venv\\Scripts\\python.exe demo_scripts\\demo_all_alerts.py --direct

Dashboard: http://localhost:8080/caretaker.html
"""
import argparse
import json
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MQTT_HOST = "localhost"
MQTT_PORT = 1883

# -- MQTT client setup ------------------------------------------------
def make_mqtt_client():
    import paho.mqtt.client as mqtt
    try:
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
            client_id="SafeBathDemo",
        )
    except (AttributeError, TypeError):
        client = mqtt.Client(client_id="SafeBathDemo")
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    client.loop_start()
    return client

def make_direct_writer(db_path="data/events.db"):
    from backend.db import get_conn, init_db
    from backend.mqtt_bridge import on_message
    init_db(db_path)
    conn = get_conn(db_path)
    return conn, on_message

# -- Scenario builders ------------------------------------------------
def build_mat_fall_frames(count=25):
    """64 cells all high (sustained) + one frame with CoP shift (centroid jump)."""
    frames = []
    for i in range(count):
        if i == 15:
            # CoP jump: spike pressure on one side
            vals = [random.randint(200, 400)] * 48 + [random.randint(3200, 4000)] * 16
        else:
            vals = [random.randint(3000, 3900)] * 64
        frames.append(("sensor/mat/frame", ",".join(map(str, vals))))
    return frames

def build_radar_collapse(count=10):
    """Motionless + presence for multiple ticks."""
    frames = []
    for i in range(count):
        payload = json.dumps({
            "presence": True,
            "motion": False,
            "stillness_ms": 30000 + i * 3000,
            "possible_collapse": i >= 3,
        })
        frames.append(("sensor/radar/status", payload))
    return frames

def build_thermal_scald(count=8):
    """Hot water + nobody on mat -> scald path."""
    frames = []
    for i in range(count):
        # Empty mat (nobody)
        mat_vals = [random.randint(0, 200)] * 64
        frames.append(("sensor/mat/frame", ",".join(map(str, mat_vals))))
        # Hot thermal + flow
        thermal = json.dumps({
            "avg_thermal_c": 42.0 + random.uniform(-0.3, 0.3),
            "flow_pulses": 80,
            "tof_collapse": False,
            "unattended_hot_fill": True,
        })
        frames.append(("sensor/tof_thermal/status", thermal))
    return frames

def build_audio_distress(count=10):
    """Impact thud followed by sustained cry, with radar confirming presence."""
    frames = []
    for i in range(count):
        # Radar: present + motion
        radar = json.dumps({
            "presence": True,
            "motion": True,
            "stillness_ms": 0,
            "possible_collapse": False,
        })
        frames.append(("sensor/radar/status", radar))
        # Audio
        if i < 2:
            label, energy = "impact_thud", 0.9
        else:
            label, energy = "speech_or_cry", 0.6
        audio = json.dumps({"label": label, "energy": energy})
        frames.append(("sensor/audio/event", audio))
    return frames

def build_toilet_biomarker(count=5):
    """Consecutive high gas/VOC readings (UTI risk pattern)."""
    frames = []
    for i in range(count):
        reading = json.dumps({
            "gas_ppm": 310 + random.randint(0, 30),
            "voc_ppm": 85 + random.randint(0, 10),
            "turbidity": 92 + random.random() * 5,
            "hour_of_day": time.localtime().tm_hour,
        })
        frames.append(("sensor/toilet/reading", reading))
    return frames

def build_tof_collapse(count=10):
    """ToF reports collapse posture + radar confirms."""
    frames = []
    for i in range(count):
        radar = json.dumps({
            "presence": True,
            "motion": False,
            "stillness_ms": 25000 + i * 3000,
            "possible_collapse": i >= 2,
        })
        frames.append(("sensor/radar/status", radar))
        tof = json.dumps({
            "avg_thermal_c": 24.5,
            "flow_pulses": 0,
            "tof_collapse": True,
            "unattended_hot_fill": False,
        })
        frames.append(("sensor/tof_thermal/status", tof))
    return frames

# -- Scenarios --------------------------------------------------------
import random

SCENARIOS = [
    ("1. MAT FALL (fall_risk)",
     "Sustained high pressure on mat -> ConvLSTM detects fall + CoP jump",
     build_mat_fall_frames),

    ("2. RADAR COLLAPSE (possible_collapse)",
     "Motionless person detected by radar for 30+ seconds",
     build_radar_collapse),

    ("3. THERMAL SCALD (scald_shutoff)",
     "Hot water running + nobody on mat -> emergency shutoff",
     build_thermal_scald),

    ("4. AUDIO DISTRESS (acoustic_distress)",
     "Impact thud + sustained cry, radar confirms presence",
     build_audio_distress),

    ("5. TOILET BIOMARKER (toilet_biomarker)",
     "High gas + VOC levels -> UTI risk flagged (Medium severity)",
     build_toilet_biomarker),

    ("6. TOF COLLAPSE (tof_collapse)",
     "ToF sensor detects collapsed posture + radar confirms stillness",
     build_tof_collapse),
]

def run_scenario(name, desc, builder_fn, writer_fn, delay=0.15):
    """Run one scenario: publish all frames with small delay."""
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"  {desc}")
    print(f"{'='*60}")
    frames = builder_fn()
    print(f"  Publishing {len(frames)} messages...")
    for i, (topic, payload) in enumerate(frames):
        writer_fn(topic, payload)
        if i % 5 == 0:
            print(f"    [{i+1}/{len(frames)}] {topic}")
        time.sleep(delay)
    print(f"  [DONE] check caretaker dashboard for alert")

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--direct", action="store_true",
                        help="Write directly to DB (no MQTT broker needed)")
    parser.add_argument("--delay", type=float, default=0.15,
                        help="Delay between MQTT messages (seconds)")
    parser.add_argument("--scenarios", nargs="*", type=int,
                        help="Run only specific scenario numbers (1-6)")
    parser.add_argument("--auto", action="store_true",
                        help="Run all scenarios without pausing (for testing)")
    args = parser.parse_args()

    print("SafeBath AI -- Full-Module Alert Demo")
    print("Dashboard: http://localhost:8080/caretaker.html")
    print()

    if args.direct:
        print("Mode: DIRECT (no broker)")
        conn, on_msg = make_direct_writer()
        def writer(topic, payload):
            on_msg(conn, topic, payload)
    else:
        print("Mode: MQTT BROKER")
        print(f"Connecting to {MQTT_HOST}:{MQTT_PORT}...")
        client = make_mqtt_client()
        time.sleep(1)  # wait for connection
        def writer(topic, payload):
            client.publish(topic, payload, qos=0)

    scenarios = SCENARIOS
    if args.scenarios:
        scenarios = [SCENARIOS[i-1] for i in args.scenarios if 1 <= i <= len(SCENARIOS)]

    print(f"\nRunning {len(scenarios)} scenarios...")
    print("Press Enter after each to proceed to the next.\n")

    for idx, (name, desc, builder_fn) in enumerate(scenarios):
        run_scenario(name, desc, builder_fn, writer, delay=args.delay)
        if idx < len(scenarios) - 1 and not args.auto:
            try:
                input(f"\n  -> Press Enter to run next scenario (or Ctrl+C to stop)...")
            except KeyboardInterrupt:
                print("\n\nDemo stopped by user.")
                break

    print(f"\n{'='*60}")
    print("  ALL SCENARIOS COMPLETE")
    print("  Check caretaker dashboard for all 6 alert types")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()
