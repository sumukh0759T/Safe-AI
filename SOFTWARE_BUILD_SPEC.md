# SafeBath AI — Software Build Specification
### For use with `opencode` — dataset strategy, AI/ML model spec, hardware data contracts, complete software task list

---

## 0. How to Use This Document

This file is written as an **agent task spec**, not a narrative explainer — each section ends in concrete files to create and a definition of done. Point `opencode` at this file and work top-to-bottom; sections are ordered so nothing depends on a file that hasn't been created yet.

```
opencode run "Read SOFTWARE_BUILD_SPEC.md in full. Execute Phase 0 (repo scaffold), then Phase 1 (dataset layer), confirming each file matches its 'Definition of done' before moving to the next task."
```

Recommended: run one phase per `opencode` session so output stays reviewable. Hardware wiring is **out of scope for this document** — Joy Projects owns final pin assignments; software only depends on the **MQTT topic/payload contract in Section 2**, which stays stable regardless of which physical GPIO a sensor lands on.

**Repo root:** `safebath-ai/`

---

## 1. Target Repo Structure (create this first — Phase 0)

```
safebath-ai/
  data/
    raw/                          # fetched public datasets land here (gitignored)
    synthetic/                    # generated .npy / .csv training data
    real/                         # opportunistically-recorded real sessions
  datasets/
    fetch_audio_esc50.py
    fetch_audio_ravdess.py
    fetch_fall_reference_sets.py
    gen_mat_data.py
    gen_toilet_data.py
    gen_thermal_data.py
    gen_radar_stub_data.py
    prepare_audio_embeddings.py
  training/
    train_fall_convlstm.py
    train_toilet_rf.py
    train_audio_transfer.py
    train_thermal_cnn_lstm.py
    train_voice_kws.py
    train_routine_isoforest.py
    convert_to_tflite.py
    evaluate_all.py
  firmware/
    mat_node/mat_node.ino
    radar_node/radar_node.ino
    toilet_node/toilet_node.ino
    audio_node/audio_node.ino
    tof_thermal_voice_node/tof_thermal_voice_node.ino
    shared/model_data/            # generated .h files land here
  backend/
    mqtt_bridge.py
    fusion_engine.py
    voice_bridge.py
    api_server.py                 # REST API the React dashboard polls
    cross_modal.py                # Section 12.3-style cross-modal rules
    schemas.py                    # pydantic models mirroring Section 2 contract
  frontend/
    SafeBathApp.jsx                # already built — see Section 8
  models/                          # trained artifacts (.h5, .tflite, .pkl) — gitignored
  tests/
    test_fusion_engine.py
    test_schemas.py
    test_synthetic_generators.py
  requirements.txt
  .env.example
  README.md
```

**Definition of done:** every folder above exists (empty `.gitkeep` where needed), `requirements.txt` and `.env.example` are populated per Section 9.

---

## 2. Hardware Data Contract (what software can rely on)

Software never touches GPIO. It only ever sees MQTT topics with this exact JSON shape — **this is the interface boundary between hardware and software**, and it does not change even if Joy Projects changes physical pins.

| Board | MQTT Topic | Payload | Publish rate |
|---|---|---|---|
| 1 — Mat | `sensor/mat/frame` | CSV string, 64 comma-separated ints (8×8 pressure grid, 12-bit ADC range 0–4095) | 10 Hz |
| 1 — Mat | `sensor/mat/pir` | `{"entry": true}` | on event |
| 2 — Radar | `sensor/radar/status` | `{"presence": bool, "motion": bool, "stillness_ms": int, "possible_collapse": bool}` | 2 Hz |
| 3 — Toilet | `sensor/toilet/reading` | `{"gas_ppm": float, "voc_ppm": float, "turbidity": float, "temp_c": float, "hour_of_day": int}` | every 2 s |
| 4 — Audio | `sensor/audio/event` | plain string: `"silence" \| "water" \| "impact_thud" \| "speech_or_cry"` (never raw audio) | on classified window (~5 Hz) |
| 5 — ToF/Thermal | `sensor/tof_thermal/status` | `{"avg_thermal_c": float, "flow_pulses": int, "tof_collapse": bool, "unattended_hot_fill": bool}` | ~3 Hz |
| 5 — Voice Bridge | `voice/trigger` (subscribe) / `voice/reply` (publish) | `{"prompt": "are_you_ok"}` in, `{"reply": "fine"\|"help"\|"no_response"}` out | on-demand |

**Definition of done:** `backend/schemas.py` defines a pydantic (or dataclass) model per row above, used by every consumer (`mqtt_bridge.py`, `fusion_engine.py`, `api_server.py`) so a malformed payload fails loudly instead of silently corrupting the fusion score.

---

## 3. Dataset Strategy — Fetching + Generation

Two tracks per module, used together: **(a)** pull a relevant public dataset where one genuinely exists and helps, **(b)** procedurally generate synthetic data that matches your actual sensor's signal shape. Track (b) exists for *every* module since it needs zero hardware; track (a) only exists where a real public dataset is actually applicable — don't force-fit one where it isn't (see the "N/A" rows below and why).

### 3.1 Per-Module Dataset Plan

| Module | Public dataset to fetch? | Why / why not | Synthetic generator |
|---|---|---|---|
| 1 — Fall risk (mat) | **No direct fit.** SisFall/UMAFall/MobiFall are wearable-accelerometer datasets — different sensor modality (IMU, not pressure grid), so they don't transfer to a Velostat pressure-frame model. Use them only as *qualitative reference* for what a collapse trajectory looks like in time, not as training input. | `gen_mat_data.py` |
| 2 — Radar presence/motion | N/A — rule-based, no model to train | N/A | Rule thresholds only (no data file needed) |
| 3 — Toilet biomarkers | **No public dataset exists** for gas/turbidity/temp fused urine-screening at this sensor combination — this is expected, it's a novel sensing setup (see the older plan's Section 12.1 literature review). Synthetic-only is the correct approach here, not a shortcut. | `gen_toilet_data.py` |
| 4 — Acoustic distress | **Yes — fetch ESC-50** (environmental sound classification, 2000 labeled 5-second clips, 50 classes incl. water, footsteps, glass-breaking-adjacent impact sounds) for the "water" and ambient classes, and **RAVDESS** (emotional speech/vocalization) for the "speech_or_cry" class. Combine both with synthetic impact-thud clips (no public "person falling" audio dataset exists) | `gen_audio_data.py` (synthetic thud/silence) + `prepare_audio_embeddings.py` (real clips → YAMNet embeddings) |
| 5 — ToF collapse | N/A — rule-based, single-zone sensor, no model to train | N/A | Rule thresholds only |
| 6 — Thermal scald/freeze | **No usable public dataset** at 8×8 AMG8833 resolution — published thermal fall datasets use higher-res arrays (32×24 MLX90640) or are not publicly released. Synthetic-only, generated directly at 8×8 to match your actual sensor. | `gen_thermal_data.py` |
| 9 — Routine anomaly | N/A — unsupervised, trains directly on your own logged visit timestamps once the system has been running for a few days | N/A | Not synthetic — see Section 5.6 |
| 10 — Voice Bridge KWS | **Yes — fetch Google Speech Commands dataset** (`"yes"`, `"no"`, `"help"` are close enough to a 3-way intent set; fine-tune/relabel toward `fine` / `help` / `unknown`) | `training/train_voice_kws.py` consumes this directly |

**Honesty note for whoever presents this:** rows marked "no public dataset exists" aren't a gap in your search — they're genuinely novel sensing combinations, which is worth stating as a strength (see the original plan's Section 12.3 "genuinely new ideas"), not hidden as a weakness.

### 3.2 Fetch Scripts

#### `datasets/fetch_audio_esc50.py`
```python
"""
Fetches ESC-50 (environmental sound classification, CC-BY licensed).
Source: https://github.com/karoldvl/ESC-50  (mirrored at karolpiczak/ESC-50)
Verify the URL resolves before relying on it in an offline/venue environment —
download once ahead of time and cache under data/raw/esc50/.
"""
import os, zipfile, urllib.request

URL = "https://github.com/karolpiczak/ESC-50/archive/refs/heads/master.zip"
OUT_DIR = "data/raw/esc50"
ZIP_PATH = "data/raw/esc50_master.zip"

RELEVANT_CLASSES = ["water_drops", "pouring_water", "toilet_flush", "footsteps", "door_wood_knock", "glass_breaking"]

def fetch():
    os.makedirs(OUT_DIR, exist_ok=True)
    if not os.path.exists(ZIP_PATH):
        print("Downloading ESC-50...")
        urllib.request.urlretrieve(URL, ZIP_PATH)
    with zipfile.ZipFile(ZIP_PATH) as z:
        z.extractall(OUT_DIR)
    print(f"Extracted to {OUT_DIR}. Filter meta/esc50.csv for: {RELEVANT_CLASSES}")

if __name__ == "__main__":
    fetch()
```

#### `datasets/fetch_audio_ravdess.py`
```python
"""
Fetches RAVDESS (emotional speech/song, CC-BY-NC-SA), used only for the
'speech_or_cry' class prototypes — not for the primary distress classifier,
which trains on YAMNet embeddings per Section 5.4.
Source (Zenodo): https://zenodo.org/record/1188976
Verify this resolves at build time; Zenodo record IDs are stable but re-check
before offline demo day and cache locally.
"""
import os, zipfile, urllib.request

URL = "https://zenodo.org/record/1188976/files/Audio_Speech_Actors_01-24.zip"
OUT_DIR = "data/raw/ravdess"
ZIP_PATH = "data/raw/ravdess.zip"

def fetch():
    os.makedirs(OUT_DIR, exist_ok=True)
    if not os.path.exists(ZIP_PATH):
        print("Downloading RAVDESS speech subset...")
        urllib.request.urlretrieve(URL, ZIP_PATH)
    with zipfile.ZipFile(ZIP_PATH) as z:
        z.extractall(OUT_DIR)
    print(f"Extracted to {OUT_DIR}. Use 'fearful' and 'sad' emotion codes as speech_or_cry proxies.")

if __name__ == "__main__":
    fetch()
```

#### `datasets/fetch_fall_reference_sets.py`
```python
"""
Fetches SisFall (reference only — see Section 3.1, NOT used as direct training
input for the pressure-mat model, different sensor modality). Useful only for
sanity-checking what collapse-trajectory timing/shape looks like.
Source: http://sistemic.udea.edu.co/en/investigacion/proyectos/english-falls/
This host has historically been slow/unstable — treat as optional, skip if
unavailable, the mat model does not depend on this file existing.
"""
import os, urllib.request

URL = "http://sistemic.udea.edu.co/wp-content/uploads/2017/04/SisFall_dataset.zip"
OUT_PATH = "data/raw/sisfall.zip"

def fetch():
    os.makedirs("data/raw", exist_ok=True)
    try:
        urllib.request.urlretrieve(URL, OUT_PATH)
        print("SisFall reference set downloaded (optional, reference-only).")
    except Exception as e:
        print(f"Skipping SisFall fetch (optional): {e}")

if __name__ == "__main__":
    fetch()
```

#### `datasets/prepare_audio_embeddings.py`
```python
"""
Converts fetched real audio (ESC-50 + RAVDESS subsets) into YAMNet embeddings,
combined with the synthetic clips from gen_audio_data.py, into one training set.
"""
import numpy as np
import tensorflow as tf
import tensorflow_hub as hub
import librosa
import glob, os

yamnet = hub.load("https://tfhub.dev/google/yamnet/1")

def embed(waveform_16k):
    scores, embeddings, spectrogram = yamnet(waveform_16k)
    return tf.reduce_mean(embeddings, axis=0).numpy()

def load_and_resample(path, sr=16000):
    y, _ = librosa.load(path, sr=sr, mono=True, duration=1.0)
    if len(y) < sr:
        y = np.pad(y, (0, sr - len(y)))
    return y

LABEL_MAP = {
    "water_drops": 1, "pouring_water": 1, "toilet_flush": 1,        # -> "water"
    "footsteps": 0, "door_wood_knock": 2, "glass_breaking": 2,      # -> silence/thud proxies
}

def build_from_esc50(esc50_dir):
    import csv
    X, y = [], []
    meta_path = os.path.join(esc50_dir, "ESC-50-master", "meta", "esc50.csv")
    audio_dir = os.path.join(esc50_dir, "ESC-50-master", "audio")
    with open(meta_path) as f:
        for row in csv.DictReader(f):
            cls = row["category"]
            if cls in LABEL_MAP:
                wav = load_and_resample(os.path.join(audio_dir, row["filename"]))
                X.append(embed(wav))
                y.append(LABEL_MAP[cls])
    return np.array(X), np.array(y)

def build_from_ravdess(ravdess_dir, label=3):  # 3 = speech_or_cry
    X, y = [], []
    for path in glob.glob(os.path.join(ravdess_dir, "**", "*.wav"), recursive=True):
        emotion_code = path.split("-")[2] if "-" in os.path.basename(path) else None
        if emotion_code in ("06", "04"):  # fearful, sad
            wav = load_and_resample(path)
            X.append(embed(wav))
            y.append(label)
    return np.array(X), np.array(y)

if __name__ == "__main__":
    Xe, ye = build_from_esc50("data/raw/esc50")
    Xr, yr = build_from_ravdess("data/raw/ravdess")
    X = np.concatenate([Xe, Xr]) if len(Xe) and len(Xr) else (Xe if len(Xe) else Xr)
    y = np.concatenate([ye, yr]) if len(ye) and len(yr) else (ye if len(ye) else yr)
    np.save("data/real/audio_real_embeddings_X.npy", X)
    np.save("data/real/audio_real_embeddings_y.npy", y)
    print(f"Saved {len(X)} real-audio embeddings.")
```

### 3.3 Synthetic Generators

Reuse these unchanged (already validated against the module's actual sensor shape — 8×8 grids, single-scalar toilet readings, 1-second audio windows):

`datasets/gen_mat_data.py`, `datasets/gen_toilet_data.py`, `datasets/gen_thermal_data.py` — physics/rule-informed procedural generation (domain randomization): each writes labeled `.npy`/`.csv` files to `data/synthetic/`. Full generator code is in `SafeBath_AI_Full_Stack_Build_Guide.md` Section 9.7 — copy those three files verbatim into `datasets/`, updating only the output path to `data/synthetic/`.

`datasets/gen_audio_data.py` — same source, but its output is now **combined** with `prepare_audio_embeddings.py`'s real-data output before training (Section 5.4), not used alone.

`datasets/gen_radar_stub_data.py` — new, small helper so the radar rule (Section 5.2) can be unit-tested before the C4001 physically arrives:
```python
import numpy as np

def synthetic_radar_stream(n=500, collapse_at=250):
    """Yields (presence, motion, stillness_ms) tuples simulating a stillness event."""
    for i in range(n):
        presence = True
        if i < collapse_at:
            motion = np.random.rand() > 0.3
            stillness = 0 if motion else np.random.randint(0, 5000)
        else:
            motion = False
            stillness = (i - collapse_at) * 500
        yield presence, motion, stillness

if __name__ == "__main__":
    for p, m, s in synthetic_radar_stream():
        flag = p and s > 120000
        if flag:
            print(f"possible_collapse flagged at stillness_ms={s}")
            break
```

**Definition of done for Section 3:** running `python datasets/gen_mat_data.py`, `gen_toilet_data.py`, `gen_thermal_data.py`, `gen_audio_data.py` produces files under `data/synthetic/`; running the two fetch scripts (ESC-50, RAVDESS) followed by `prepare_audio_embeddings.py` produces `data/real/audio_real_embeddings_{X,y}.npy`. If a fetch script fails (offline, host down), training must still proceed on synthetic-only data — no hard dependency on network access.

---

## 4. AI/ML Algorithms — Original Plan vs. Hardware-Adapted Plan

The **original architecture plan** (from the earlier full research/ideation phase) assumed higher-fidelity sensors than what's confirmed in hand today. Both are documented here so the reasoning is traceable — this is a deliberate, justified downgrade path per sensor capability, not a shortcut.

| # | Module | Original planned model (ideal hardware) | **Actual model to implement now** (confirmed hardware) | Why it changed |
|---|---|---|---|---|
| 1 | Fall Risk | Spatiotemporal CNN on a 32×32 Velostat grid → Frailty Index | **ConvLSTM2D on an 8×8 grid**, 3-class (normal/unsteady/fall) | Confirmed mux/shift-register setup reads a coarser grid; ConvLSTM still captures the sway trajectory that matters |
| 2 | Cardiac/Presence | LSTM on mmWave micro-Doppler → HR/RR anomaly (planned sensor: HLK-LD1115H) | **Rule-based: presence + stillness-duration threshold** (confirmed sensor: DFRobot C4001) | C4001 exposes presence/motion booleans, not raw Doppler/vitals data — no HR/RR signal exists to model |
| 3 | Toilet Biomarkers | Random Forest / SVM fusion on gas + optical + thermal + frequency | **Random Forest on 4 tabular features** (gas_ppm, voc_ppm, turbidity, temp_c) — same model family, smaller feature set | Confirmed hardware doesn't include a toilet-side thermal camera (AMG8833 is Board-5-only, see hardware doc rationale) |
| 4 | Acoustic Distress | MobileNetV2 on Mel-spectrograms, trained from scratch, TFLite-Micro | **YAMNet embeddings + small dense classifier head** (transfer learning) | Same outcome (on-device 4-class audio classifier), far less data needed — standard fast-training approach for small audio datasets |
| 5 | Volumetric Fall | PointNet-lite on 8×8 multizone ToF point clouds (planned sensor: VL53L5CX) | **Rule-based trilateration threshold** (confirmed sensor: VL53L1X, single-zone ×3) | Single-zone sensors have no spatial structure for PointNet to learn from — this is a legitimate deterministic fallback, not a placeholder |
| 6 | Thermal Scald/Freeze | CNN + LSTM on MLX90640 (32×24) | **CNN + LSTM on AMG8833 (8×8)** — same architecture, smaller input tensor | Budget swap (Section 2.1 of the original plan) — 8×8 is enough resolution for blob presence/motion/freeze, doesn't change the model family |
| 7 | Risk Fusion Engine | Weighted ensemble → 0–100 score | **Unchanged** — weighted ensemble with severity decay | Software-only, no hardware dependency |
| 8 | Alert Resolution | State machine on posture stream (reused Module 5) | **Unchanged**, now reads the rule-based ToF collapse flag instead of a PointNet posture class | Same state machine logic, different upstream signal type (bool instead of class label) |
| 9 | Routine Anomaly | Isolation Forest on visit timestamps (reused Module 1) | **Unchanged** | Software-only |
| 10 | Voice Bridge (NEW since original plan) | — | **micro_speech-style keyword spotting**, 3-way classify (fine/help/no_response) | Added after original plan — flagship differentiator, see Full-Stack Build Guide Section 15 |

**Definition of done for Section 4:** `README.md` includes this table verbatim so any future contributor (or a hackathon judge asking "why isn't this a CNN") can see the reasoning without re-deriving it.

---

## 5. Model-by-Model Implementation Tasks

### 5.1 Module 1 — Fall Risk (`training/train_fall_convlstm.py`)
- Input: `data/synthetic/mat_synth_X.npy` (N, 20, 8, 8), labels 0/1/2
- Architecture: `ConvLSTM2D(8, (3,3)) → Flatten → Dense(16, relu) → Dense(3, softmax)`
- Output: `models/fall_risk_model.h5`
- **Definition of done:** validation accuracy printed and ≥ 0.85 on synthetic hold-out; model converts cleanly through `convert_to_tflite.py` with tensor arena < 100KB (ESP32 constraint per the original plan's Section 2.2).

### 5.2 Module 2 — Presence/Motion Rule (`backend/fusion_engine.py`, no training)
- Logic: `possible_collapse = presence AND stillness_ms > 120000`
- Unit-test against `datasets/gen_radar_stub_data.py`'s synthetic stream before wiring to real MQTT data.
- **Definition of done:** `tests/test_fusion_engine.py` includes a case that feeds the synthetic stillness stream and asserts the flag fires at the correct index.

### 5.3 Module 3 — Toilet Biomarkers (`training/train_toilet_rf.py`)
- Input: `data/synthetic/toilet_synth_data.csv`
- `RandomForestClassifier(n_estimators=100, max_depth=6)`
- Output: `models/toilet_rf_model.pkl`
- **Definition of done:** test accuracy ≥ 0.80 on synthetic hold-out; confusion matrix printed (normal / uti_risk / dehydration_risk).

### 5.4 Module 4 — Acoustic Distress (`training/train_audio_transfer.py`)
- Input: `data/real/audio_real_embeddings_{X,y}.npy` **concatenated with** synthetic thud/silence embeddings from `datasets/gen_audio_data.py` run through the same YAMNet embed step
- Architecture: `Dense(64, relu) → Dense(4, softmax)` on top of frozen YAMNet embeddings
- Output: `models/audio_head_model.h5`
- **Definition of done:** per-class recall reported for all 4 classes (silence/water/impact_thud/speech_or_cry) — flag if any class recall < 0.7, since class imbalance between real and synthetic sources is the most likely failure mode here.

### 5.5 Module 5 — ToF Rule (in firmware, mirrored in `backend/cross_modal.py` for dashboard simulation)
- Logic: `collapse = all three sensor readings < 300mm, sustained`
- **Definition of done:** same threshold value exists in exactly one place conceptually — firmware owns the live decision, `cross_modal.py` only mirrors it for the Mock-mode dashboard, and a comment in both files points to this section so they don't drift.

### 5.6 Module 6 — Thermal Scald/Freeze (`training/train_thermal_cnn_lstm.py`)
- Input: `data/synthetic/thermal_synth_X.npy` (N, 15, 8, 8), labels 0/1
- Architecture: `TimeDistributed(Conv2D(8,(3,3))) → TimeDistributed(Flatten) → LSTM(16) → Dense(2, softmax)`
- Output: `models/thermal_freeze_model.h5`
- **Definition of done:** validation accuracy ≥ 0.85.

### 5.7 Module 7 — Risk Fusion Engine (`backend/fusion_engine.py`)
- Weighted sum across all module outputs (weights already defined in the Full-Stack Build Guide Section 9.9) + exponential severity decay (`decayed = max(raw, decayed * 0.9)`).
- **Definition of done:** `tests/test_fusion_engine.py` covers (a) score rises correctly when two modules flag simultaneously, (b) decay brings the score back down over N ticks with no new signal, (c) re-escalation happens immediately if a second independent module fires mid-decay (original plan's Section 12.3 "Alert Severity Decay with Re-Escalation" — implement this explicitly, don't skip it, it's one of the genuinely novel mechanisms).

### 5.8 Module 8 — Alert Resolution (`backend/fusion_engine.py`, state machine)
- States: `resolved → alerting → resolved`, gated by the Voice Bridge reply (Section 5.9) before ever reaching `alerting`.
- **Definition of done:** state transitions logged with timestamps to `data/events.db` so a post-hoc timeline can be reconstructed for the demo.

### 5.9 Module 9 — Routine Anomaly (`training/train_routine_isoforest.py`)
- Input: **not synthetic** — reads real `sensor/mat/pir` entry-event timestamps accumulated in `data/events.db` over the system's actual runtime.
- `IsolationForest(contamination=0.1)` on features: hour-of-day, gap-since-last-visit, visit-duration.
- **Definition of done:** script gracefully no-ops with a clear log message if fewer than ~50 real events exist yet — this module is explicitly the one that needs live runtime, not bring-up-day data, don't fake this one with synthetic data since it would defeat its purpose (personal baseline).

### 5.10 Module 10 — Voice Bridge (`training/train_voice_kws.py`, `backend/voice_bridge.py`)
- Input: Google Speech Commands subset (`yes`/`no`/`help`... relabel toward `fine`/`help`/`unknown`), fetched separately (add `datasets/fetch_speech_commands.py` following the same pattern as Section 3.2's fetch scripts, source: `https://storage.googleapis.com/download.tensorflow.org/data/speech_commands_v0.02.tar.gz` — official TensorFlow-hosted mirror).
- Architecture: micro_speech-style (small conv + dense on MFCC features), target < 20KB `.tflite`.
- **Definition of done:** `attempt_voice_triage()` in `voice_bridge.py` calls the real model instead of the placeholder string from the earlier build guide; unit test feeds 3 sample clips (one per class) and asserts correct classification.

---

## 6. Backend Tasks

### 6.1 `backend/schemas.py`
Pydantic models for every payload in Section 2's table. Every other backend file imports from here — no inline dict-shape assumptions anywhere else in the codebase.

### 6.2 `backend/mqtt_bridge.py`
Subscribes to `sensor/#`, validates each payload against `schemas.py`, writes to SQLite (`data/events.db`). Reuse the version in the Full-Stack Build Guide Section 9.9, add schema validation before insert (currently missing there — add it now).

### 6.3 `backend/fusion_engine.py`
Implements Sections 5.2, 5.7, 5.8 above. Runs as its own long-lived process (`python backend/fusion_engine.py`), reading from `data/events.db`, writing the current risk score + state back to a `risk_state` table that `api_server.py` reads from.

### 6.4 `backend/cross_modal.py`
Implements the original plan's Section 12.3 cross-modal ideas explicitly as testable functions, not just prose:
```python
def event_triggered_biomarker_sample(mat_unsteady: bool, tof_collapse: bool) -> bool:
    """Trigger an immediate high-priority toilet reading when Module 1 or 5 flags."""
    return mat_unsteady or tof_collapse

def acoustic_radar_gate(audio_class: str, radar_presence: bool, radar_motion: bool) -> bool:
    """Only escalate an 'impact_thud' if radar independently confirms a body-sized
    target was present and moving at that timestamp — attacks the 'running water
    masks distress sounds' false-positive mode."""
    return audio_class == "impact_thud" and radar_presence and radar_motion

def scald_shutoff_confirmed(unattended_hot_fill: bool, mat_pressure_present: bool) -> bool:
    """Cross-check Module 1 before Module 6 triggers the solenoid — reduces nuisance
    shutoffs when someone is actually standing at the tub/sink."""
    return unattended_hot_fill and not mat_pressure_present
```
**Definition of done:** each function has a corresponding test in `tests/test_fusion_engine.py`; `fusion_engine.py` imports and uses all three rather than re-implementing the logic inline.

### 6.5 `backend/api_server.py` (NEW — connects the React dashboard to real data)
```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import sqlite3, json

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

def get_conn():
    return sqlite3.connect("data/events.db", check_same_thread=False)

@app.get("/api/latest")
def latest():
    conn = get_conn()
    risk_row = conn.execute("SELECT score FROM risk_state ORDER BY ts DESC LIMIT 1").fetchone()
    modules = {}
    for mod_id, topic in [
        ("mat", "sensor/mat/frame"), ("radar", "sensor/radar/status"),
        ("toilet", "sensor/toilet/reading"), ("audio", "sensor/audio/event"),
        ("tof", "sensor/tof_thermal/status"),
    ]:
        row = conn.execute("SELECT payload FROM events WHERE topic=? ORDER BY ts DESC LIMIT 1", (topic,)).fetchone()
        modules[mod_id] = {"value": row[0] if row else None}
    events = conn.execute("SELECT ts, topic, payload FROM events ORDER BY ts DESC LIMIT 10").fetchall()
    return {
        "risk_score": risk_row[0] if risk_row else 0,
        "modules": modules,
        "events": [{"text": f"{t}: {p}", "level": "safe"} for _, t, p in events],
    }
```
Run with: `uvicorn backend.api_server:app --reload --port 8000`
**Definition of done:** hitting `http://localhost:8000/api/latest` returns JSON matching exactly the shape `SafeBathApp.jsx`'s Live mode already expects (see Section 8) — no frontend changes needed, this is the missing link mentioned at the end of the last build session.

---

## 7. Firmware Tasks (edge — only after hardware confirms final pins)

Firmware files already exist in the Full-Stack Build Guide (Section 9.2–9.6) and match the Section 2 MQTT contract exactly. When Joy Projects finalizes pin assignments:
1. Copy the five `.ino` files from the Full-Stack Build Guide into `firmware/*/`.
2. Update only the `#define GPIOxx` lines — nothing else changes, since firmware's job is to always emit the Section 2 contract regardless of which physical pin feeds it.
3. Copy each trained model's `model_data.h` (from `convert_to_tflite.py`) into `firmware/shared/model_data/` and `#include` it in the matching `.ino`.

**Definition of done:** a firmware file compiles (Arduino CLI or IDE) without touching anything in `backend/` or `training/` — this boundary should never leak.

---

## 8. Frontend Integration

`frontend/SafeBathApp.jsx` (already built) needs no changes to consume `api_server.py` — set the Live-mode URL in its top bar to `http://localhost:8000/api/latest`. Confirm the following before considering Section 6.5 done:
- Toggle to "Live" in the nav bar
- `liveStatus` indicator turns from red (`WifiOff`) to green (`Wifi`) within ~3 s of `api_server.py` running
- Module cards populate with real values instead of the mock generator's output

---

## 9. Environment Setup

### `requirements.txt`
```
tensorflow>=2.15
tensorflow-hub
scikit-learn
numpy
pandas
librosa
paho-mqtt
fastapi
uvicorn
pydantic
requests
joblib
pytest
```

### `.env.example`
```
MQTT_BROKER_HOST=localhost
MQTT_BROKER_PORT=1883
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
SQLITE_PATH=data/events.db
API_PORT=8000
```

**Definition of done:** `pip install -r requirements.txt --break-system-packages` (or inside a venv) completes cleanly; `pytest tests/` runs with zero collection errors even before any model is trained (tests for untrained modules should skip, not fail, using `pytest.mark.skipif`).

---

## 10. Suggested `opencode` Execution Order

1. **Phase 0** — Section 1 (scaffold), Section 9 (env files)
2. **Phase 1** — Section 3 (dataset fetch + generation scripts), run them, confirm files land under `data/`
3. **Phase 2** — Section 6.1 (`schemas.py`) before anything else in `backend/`, since everything else imports it
4. **Phase 3** — Section 5 model-by-model (5.1, 5.3, 5.4, 5.6, 5.10 need training; 5.2, 5.5 are logic-only)
5. **Phase 4** — Section 6.2–6.4 (`mqtt_bridge.py`, `fusion_engine.py`, `cross_modal.py`) + their tests
6. **Phase 5** — Section 6.5 (`api_server.py`) + Section 8 (confirm frontend Live mode)
7. **Phase 6** — Section 7 (firmware) — **only start this once Joy Projects has locked final pins**, everything upstream of it is already done and doesn't need to wait

**Global definition of done:** `pytest tests/` passes, `uvicorn backend.api_server:app` serves valid data (real or mock-seeded), and `SafeBathApp.jsx` in Live mode shows it end-to-end with zero manual data massaging.
