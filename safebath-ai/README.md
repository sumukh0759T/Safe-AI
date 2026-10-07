# SafeBath AI

Elderly-care bathroom safety system: 5 sensor boards -> MQTT -> fusion engine ->
risk dashboard, with on-edge TinyML models and a Voice Bridge check-in.

## Repo layout

```
safebath-ai/
  data/          raw/ synthetic/ real/        (generated + fetched datasets)
  datasets/      fetch scripts + procedural generators
  training/      one script per model + tflite conversion + evaluation
  firmware/      five ESP32 .ino nodes (one per board) + shared model_data/
  backend/       schemas, mqtt bridge, fusion engine, alert rules, REST API
  frontend/      SafeBathApp.jsx (Mock/Live dashboard)
  models/        trained artifacts (.h5/.tflite/.pkl) - gitignored
  tests/         pytest suite
```

## Hardware data contract (stable interface)

Software never touches GPIO. Everything flows over MQTT with the payload shapes
in `backend/schemas.py`, mirroring `SafeBath_AI_Final_Master_Guide.md` Section 3:

| Board | Topic | Payload |
|---|---|---|
| 1 Mat | `sensor/mat/frame` | CSV string, **64 ints** (16 rows x 4 cols, row-major), 12-bit ADC 0-4095 @10Hz |
| 1 Mat | `sensor/mat/pir` | `{"entry": true}` |
| 2 Radar | `sensor/radar/status` | `{"presence": bool, "motion": bool, "stillness_ms": int, "possible_collapse": bool}` @2Hz |
| 3 Toilet | `sensor/toilet/reading` | `{"gas_ppm": float, "voc_ppm": float, "turbidity": float, "hour_of_day": int}` every 2s (`temp_c` optional — pending DS18B20 decision) |
| 4 Audio | `sensor/audio/event` | `{"label": "silence"|"water"|"impact_thud"|"speech_or_cry", "energy": float}` ~5Hz |
| 5 ToF/Thermal | `sensor/tof_thermal/status` | `{"avg_thermal_c": float, "flow_pulses": int, "tof_collapse": bool, "unattended_hot_fill": bool}` ~3Hz |
| 5 Voice | `voice/trigger` sub / `voice/reply` pub | `{"prompt": "are_you_ok"}` / `{"reply": "fine"|"help"|"no_response"}` |

## Original plan vs. hardware-adapted plan (traceability table)

Kept verbatim from SOFTWARE_BUILD_SPEC.md Section 4 so nobody re-derives this:

| # | Module | Original planned model (ideal hardware) | **Actual model to implement now** (confirmed hardware) | Why it changed |
|---|---|---|---|---|
| 1 | Fall Risk | Spatiotemporal CNN on a 32×32 Velostat grid → Frailty Index | **ConvLSTM2D on an 8×8 grid** (now **16×4**, same 64 cells, per final wiring), 3-class (normal/unsteady/fall) | Confirmed mux/shift-register setup reads a coarser grid; ConvLSTM still captures the sway trajectory that matters |
| 2 | Cardiac/Presence | LSTM on mmWave micro-Doppler → HR/RR anomaly | **Rule-based: presence + stillness-duration threshold** | C4001 exposes presence/motion booleans, not raw Doppler/vitals data |
| 3 | Toilet Biomarkers | Random Forest / SVM fusion on gas + optical + thermal + frequency | **Random Forest on tabular features** (gas_ppm, voc_ppm, turbidity) | Final wiring has no toilet-side thermal sensor; DS18B20 dropped |
| 4 | Acoustic Distress | MobileNetV2 on Mel-spectrograms → later YAMNet embeddings | **RMS/ZCR/spectral-centroid features → tiny dense net or thresholds** (Master Guide §5.1) | MAX4466 is analog — YAMNet/I2S approach no longer applies; features computed identically in firmware and training |
| 5 | Volumetric Fall | PointNet-lite on ToF point clouds | **Rule-based threshold** (all 3 VL53L1X < 300mm sustained) | Single-zone sensors have no spatial structure for PointNet |
| 6 | Thermal Scald/Freeze | CNN+LSTM on MLX90640 (32×24) | **CNN+LSTM on AMG8833 (8×8)** | Budget swap; same architecture family, smaller tensor |
| 7 | Risk Fusion Engine | Weighted ensemble → 0–100 score | Unchanged (+ severity decay & re-escalation) | Software-only |
| 8 | Alert Resolution | State machine | Unchanged, gated by Voice Bridge reply | Software-only |
| 9 | Routine Anomaly | Isolation Forest on visit timestamps | Unchanged (real runtime data only) | Software-only |
| 10 | Voice Bridge | — | micro_speech-style KWS: fine/help/no_response | Flagship differentiator |

## Quickstart

```bash
cd safebath-ai
python -m venv .venv && .venv\Scripts\activate     # Windows
pip install -r requirements.txt

python datasets/gen_mat_data.py                    # synthetic data -> data/synthetic/
python datasets/gen_toilet_data.py
python datasets/gen_thermal_data.py
python datasets/gen_audio_data.py

pytest tests/                                      # everything green before any training

python training/train_toilet_rf.py                 # sklearn-only, trains immediately
python training/train_fall_convlstm.py             # needs tensorflow installed
uvicorn backend.api_server:app --reload --port 8000
```

## Run without any hardware (simulated pipeline)

`backend/mock_publisher.py` emits schema-correct MQTT traffic for all 5 boards,
with deliberate danger spikes, so the whole chain runs before a single board is
flashed:

```bash
# no broker installed? direct mode writes through the bridge's validation path:
python backend/mock_publisher.py --mode direct --scenario collapse --auto-reply help --ticks 14
python backend/fusion_engine.py --once --from-start     # watch state transitions
uvicorn backend.api_server:app --port 8000              # then open the dashboard

# have mosquitto? use real broker mode instead (--mode broker), plus:
mosquitto_sub -t "sensor/#" -v                          # watch live traffic
```

Scenarios: `default` (periodic spikes), `collapse` (sustained multi-board event),
`scald` (immediate relay path); `--chaos` injects malformed payloads to prove
the bridge rejects them loudly. `--auto-reply fine|help` exercises stand-down /
escalation deterministically.

## Run the dashboard frontend

```bash
cd frontend
npm install
npm run build          # esbuild -> dist/bundle.js (~150 KB, works offline)
npm run serve          # http://localhost:8080  (or: python -m http.server 8080)
```

Toggle **LIVE** in the nav bar; with `api_server` running on :8000 the cards
populate with real (or simulated) data within ~3 s.

## Hardware bring-up

Follow `NEXT_STEPS_FOR_YOU.md` — per-board Serial-first verification
(`DEBUG_SERIAL_ONLY` flag in every `.ino`), then WiFi+MQTT, backend, dashboard,
and finally the Phase F all-boards checklist from
`SafeBath_AI_Network_And_Autonomous_Build_Guide.md` Section 2.5.

Frontend: point any React app at `frontend/SafeBathApp.jsx` (see header comment of
that file). Toggle Mock/Live in its nav bar; Live polls `http://localhost:8000/api/latest`.

## Immediate next steps (from Master Guide §12)

1. **DS18B20**: treated as *removed* — schemas keep `temp_c` optional so adding it back is a one-line change.
2. **Velostat heatmap + center-of-pressure trail**: built into `SafeBathApp.jsx` Board-1 card (plus numeric overlay toggle for calibration).
3. **ADC calibration**: placeholder constants live in `firmware/mat_node/mat_node.ino`
   (`CALIB_OFFSET` / `CALIB_GAIN`) and are applied in `datasets/gen_mat_data.py`
   normalization. Replace both once real ADC ranges arrive from Phase A bring-up.
