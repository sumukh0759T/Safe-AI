# SafeBath AI

Elderly-care bathroom safety system: 5 ESP32 sensor boards POST JSON over HTTP
to an on-prem Raspberry Pi FastAPI edge, which validates, stores to local SQLite,
and runs the fusion/alert pipeline **offline-first** (works with no internet).
When connectivity exists it opportunistically syncs to Supabase for the cloud
dashboard and sends caretaker PWA push. On-edge TinyML models + a Voice Bridge
check-in round out the system.

## Repo layout

```
safebath-ai/
  data/          raw/ synthetic/ real/        (generated + fetched datasets)
  datasets/      fetch scripts + procedural generators
  training/      one script per model + tflite conversion + evaluation
  firmware/      five ESP32 .ino nodes (one per board) + common/ shared HTTP runtime
  backend/       schemas, HTTP ingestion, fusion engine, alert rules, REST API,
                 offline outbox + optional Supabase sync
  frontend/      SafeBathApp.jsx (Mock/Live dashboard)
  models/        trained artifacts (.h5/.tflite/.pkl) - gitignored
  tests/         pytest suite
```

## Hardware data contract (stable interface)

Software never touches GPIO. Each board POSTs JSON over HTTP to the edge API;
payload shapes live in `backend/schemas.py`, mirroring
`SafeBath_AI_Final_Master_Guide.md` Section 3. Every POST wraps the payload in a
common envelope (`contract_version, device_id, module, boot_id, seq, timestamp,
message_type`) defined in `firmware/common/SafeBathTelemetry.h`.

| Board | HTTP call (message_type `module`) | Payload |
|---|---|---|
| 1 Mat | `POST /api/v1/telemetry` `mat` | `{"values": [72 ints]}` (12x6 row-major, 12-bit 0-4095 @10Hz) |
| 1 Mat PIR | `POST /api/v1/event` `mat` | `{"entry": true}` |
| 2 Radar | `POST /api/v1/telemetry` `radar` | `{"presence": bool, "motion": bool, "stillness_ms": int, "possible_collapse": bool}` @2Hz |
| 3 Toilet | `POST /api/v1/telemetry` `toilet` | `{"gas_ppm": float, "voc_ppm": float, "turbidity": float, "hour_of_day": int}` every 2s (`temp_c` optional — pending DS18B20 decision) |
| 4 Audio | `POST /api/v1/telemetry` `audio` | `{"label": "silence"\|"water"\|"impact_thud"\|"speech_or_cry", "energy": float}` ~5Hz |
| 5 ToF/Thermal | `POST /api/v1/telemetry` `tof` | `{"avg_thermal_c": float, "flow_pulses": int, "tof_collapse": bool, "unattended_hot_fill": bool}` ~3Hz |
| 5 Voice trigger | `POST /api/v1/event` `tof` | `{"prompt": "are_you_ok"}` |
| 5 Voice play | `GET /api/v1/voice/pending?device_id=tof-01&role=play` | claims the prompt to speak it aloud (DFPlayer) |
| 4 Voice reply | `GET /api/v1/voice/pending?device_id=audio-01&role=listen` then `POST /api/v1/event` `audio` | `{"reply": "fine"\|"help"\|"no_response"}` |

Playback and listening are leased independently (`played_by` vs `claimed_by`),
so Board 5 can speak a prompt while Board 4 listens — the HTTP replacement for
the old `voice/trigger` fan-out.

Boards may also `POST /api/v1/heartbeat` (liveness) and `GET /api/v1/config`
(contract_version + `heartbeat_interval_s`, `voice_poll_interval_s`,
`voice_lease_s`, endpoint map).

## Original plan vs. hardware-adapted plan (traceability table)

Kept verbatim from SOFTWARE_BUILD_SPEC.md Section 4 so nobody re-derives this:

| # | Module | Original planned model (ideal hardware) | **Actual model to implement now** (confirmed hardware) | Why it changed |
|---|---|---|---|---|
| 1 | Fall Risk | Spatiotemporal CNN on a 32×32 Velostat grid → Frailty Index | **ConvLSTM2D on an 8×8 grid** (now **12×6**, 72 cells, per final wiring), 3-class (normal/unsteady/fall) | Confirmed mux/shift-register setup reads a coarser grid; ConvLSTM still captures the sway trajectory that matters |
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

`backend/mock_publisher.py` emits schema-correct payloads for all 5 boards, with
deliberate danger spikes, so the whole chain runs before a single board is
flashed. Use **direct mode** (no broker, no internet) — it feeds every payload
through the same validation + storage path the HTTP edge uses:

```bash
cd safebath-ai
python backend/mock_publisher.py --mode direct --scenario collapse --auto-reply help --ticks 14
python backend/fusion_engine.py --once --from-start     # watch state transitions
uvicorn backend.api_server:app --port 8000              # then open the dashboard
```

Scenarios: `default` (periodic spikes), `collapse` (sustained multi-board event),
`scald` (immediate relay path); `--chaos` injects malformed payloads to prove
the validator rejects them loudly. `--auto-reply fine|help` exercises stand-down
/ escalation deterministically.

> `--mode broker` (real mosquitto publish) is legacy — the active architecture
> carries no MQTT; `paho-mqtt` is intentionally not in `requirements.txt`.

## Run the dashboard frontend

```bash
cd frontend
npm install
npm run build          # esbuild -> dist/bundle.js (~150 KB, works offline)
npm run serve          # http://localhost:8080  (or: python -m http.server 8080)
```

Toggle **LIVE** in the nav bar; with `api_server` running on :8000 the cards
populate with real (or simulated) data within ~3 s. A **System** strip shows
whether data is LIVE or SIMULATED, cloud sync state, local outbox depth, device
freshness, and active emergencies. The API base is resolved from (highest
priority): `window.SAFEBATH_API_BASE` → `<meta name="safebath-api-base"
content="...">` → `?api=<base>` query param (persisted for the session) → for a
page served over http from a non-local host, `http://<host>:8000` (LAN) →
`http://localhost:8000`. When the API is unreachable the apps show which base
they're calling and a hint for that scenario (mixed-content on https pages,
file://, firewall/network). Hosted https pages (e.g. Hostinger) **must** set an
https API override — the browser blocks plain-http API calls from https pages.

**Laptop serving + Raspberry Pi API.** The dashboard is normally served from
the laptop (`npm run serve`/`python -m http.server 8080` in `frontend/`), so the
page host is `localhost` and the resolver deliberately falls back to
`http://localhost:8000` — which only works when the API also runs on this
machine. For a Pi on the LAN, create a per-machine override instead:

```bash
cd frontend
copy config.example.js config.local.js        # then edit it:
#   window.SAFEBATH_API_BASE = "http://192.168.43.66:8000";
```

`config.local.js` is git-ignored (see `config.example.js`), loaded by
`index.html`/`caretaker.html` before the bundle, and needs no rebuild — just
refresh. Alternatively pass `?api=http://<pi-ip>:8000` in the URL. Remember to
re-run `sudo ./update.sh` on the Pi after backend changes (and re-flash
`firmware/mat_node/mat_node.ino`), or `/api/latest` will keep reporting the old
frame shape and the mat parser (12×6 = 72 values) will ignore it.

## Caretaker PWA (push + offline acks)

`frontend/caretaker.html` is an installable PWA backed by `caretaker_api.py`
(mounted at `/caretaker`). It registers `frontend/sw.js`, shows active
emergencies, and can deliver Web Push (VAPID keys via env; delivery requires
`pywebpush`). Acknowledgements made while offline are queued and replayed on
reconnect.

## Hardware bring-up

Follow `NEXT_STEPS_FOR_YOU.md` — per-board Serial-first verification
(`DEBUG_SERIAL_ONLY` flag in every `.ino`), then WiFi+HTTP, backend, dashboard,
and finally the Phase F all-boards checklist from
`SafeBath_AI_Network_And_Autonomous_Build_Guide.md` Section 2.5.

Point every sketch at your network with one command (rewrites `ssid`, `password`
and `server_host` in all `firmware/*/*.ino`):

```powershell
powershell -ExecutionPolicy Bypass -File firmware\set_wifi.ps1
# or: -Ssid 'MyWiFi' -Password 'secret' -ServerHost 192.168.1.50
```

`server_host` is the RPi running the edge API (port 8000). Credentials are read
from the git-ignored `firmware/wifi_credentials.local` (never committed).

## Immediate next steps (from Master Guide §12)

1. **DS18B20**: treated as *removed* — schemas keep `temp_c` optional so adding it back is a one-line change.
2. **Velostat heatmap + center-of-pressure trail**: built into `SafeBathApp.jsx` Board-1 card (plus numeric overlay toggle for calibration).
3. **ADC calibration**: placeholder constants live in `firmware/mat_node/mat_node.ino`
   (`CALIB_OFFSET` / `CALIB_GAIN`) and are applied in `datasets/gen_mat_data.py`
   normalization. Replace both once real ADC ranges arrive from Phase A bring-up.
