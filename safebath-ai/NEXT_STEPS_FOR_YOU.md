# NEXT_STEPS_FOR_YOU.md — physical actions only

Everything software-side is built and self-tested against the simulated
publisher (`backend/mock_publisher.py`). This file is the hands-on to-do list —
the actions opencode cannot do for you. Work top to bottom; each step ends with
a concrete "done looks like".

---

## 0. One-time network setup (Network Guide Section 2.2)

- [ ] Dedicated router / phone hotspot SSID + password chosen (not venue WiFi).
- [ ] Raspberry Pi (the edge server) joined to that network; note its IP:
      `hostname -I` → e.g. `192.168.1.50`. This value is `server_host`.
- [ ] Reserve that IP on the router (DHCP reservation) so it never changes.
- [ ] Sanity check from another device once the stack is up (step 3):
      `curl http://192.168.1.50:8000/api/v1/health` → `{"status":"ok"}`.

> No MQTT broker is needed. Boards POST straight to the Pi over HTTP.

## 1. Point all firmware at your network (Section 2.3)

In every `firmware/*/*.ino`, edit only these three lines:

```cpp
const char* ssid        = "YOUR_ROUTER_SSID";
const char* password    = "YOUR_ROUTER_PASSWORD";
const char* server_host = "192.168.1.50";   // RPi edge IP from step 0
```

Nothing else changes per deployment — that is the point of the HTTP contract.

Or do all five files in one command (repo ships `firmware/set_wifi.ps1`):

```powershell
# read credentials from firmware/wifi_credentials.local (git-ignored)...
powershell -ExecutionPolicy Bypass -File firmware\set_wifi.ps1
# ...or pass them directly:
powershell -ExecutionPolicy Bypass -File firmware\set_wifi.ps1 -Ssid MyWiFi `
    -Password 'your_router_password' -ServerHost 192.168.1.50
```

The `.ino` files in git contain placeholders only — real credentials live in
`firmware/wifi_credentials.local`, which `.gitignore` keeps out of the repo.

---

## 2. Per-board bring-up (Network Guide Section 1 workflow)

For EACH board below: Arduino IDE → select board/port → upload → walk steps 1–5.
`DEBUG_SERIAL_ONLY` starts as `false` in the repo files; flip to `true` first,
do Step 1, then flip back.

### Board 1 — Mat (`firmware/mat_node/mat_node.ino`)
- [ ] Step 1 (Serial): press each of the 64 intersections one at a time;
      `mat min= max= sum= peak@r,c pir=` must show ONLY that cell's region jump.
      If two cells move together → row/column short — fix Velostat wiring before
      anything else. Then flip DEBUG flag off.
- [ ] Step 2: `curl http://<rpi>:8000/api/latest` (or the edge log) shows 64-int
      values @10 Hz + PIR events when you wave at the sensor.
- [ ] Step 3: rows appear in `data/events.db`; `/api/latest` mat card updates.
- [ ] Step 4: dashboard Board-1 heatmap animates a footprint; CoP trail follows
      your steps.
- [ ] Step 5: press hard and hold → fall-risk path → Voice Bridge prompt.
- [ ] **Send me the real ADC ranges** (min/max idle vs pressed) so I can replace
      `CALIB_OFFSET/CALIB_GAIN` in this firmware AND `datasets/gen_mat_data.py`,
      then retrain the ConvLSTM.

### Board 2 — Radar (`radar_node.ino`)
- [ ] Serial: walk in/out → presence/motion flip; stand still 2 min →
      `possible_collapse` flips true at ~120 s.
- [ ] HTTP: radar telemetry @2 Hz reaches the Pi; dashboard Board-2 pills update.
- [ ] Danger chain: stand perfectly still >120 s → voice gate → escalation.

### Board 3 — Toilet (`toilet_node.ino`)
- [ ] Serial: breathe on MQ-135/MQ-3 → gas/voc rise; hold white paper then
      yellow-ish liquid sample over TCS3200 → turbidity proxy moves.
- [ ] HTTP: toilet telemetry every 2 s, no temp_c field.
- [ ] Dashboard Board-3 bars update. NOTE: toilet flags are Medium severity by
      design — they will NOT page anyone (Master Guide §7/§8).

### Board 4 — Audio (`audio_node.ino`)
- [ ] Serial: clap → impact_thud; pour water nearby → water; speak loudly →
      speech_or_cry; quiet room → silence. Three scenarios, three labels,
      reliably, before proceeding.
- [ ] HTTP: `{"label":..., "energy":...}` ~5 Hz reaches the audio telemetry feed.
- [ ] Cross-board check (Phase E pair): POST a manual trigger —
      ```
      curl -X POST http://<rpi>:8000/api/v1/event \
        -H 'Content-Type: application/json' \
        -d '{"contract_version":1,"device_id":"tof-01","module":"tof","firmware_version":"manual","boot_id":"deadbeef","sequence":1,"device_uptime_ms":0,"timestamp":0.0,"message_type":"event","sensor_status":"ok","payload":{"prompt":"are_you_ok"}}'
      ```
      → within 8 s Board 4 claims it (`GET /api/v1/voice/pending`) and POSTs a
      reply event (speak/shout/stay silent).

### Board 5 — ToF/Thermal/Voice (`tof_thermal_voice_node.ino`)
- [ ] Serial: distinct addresses 0x30/0x31/0x32 at boot; hand <30 cm over all
      three → collapse=true; warm mug near AMG8833 → avgTemp climbs.
- [ ] Bucket-and-tube rig ONLY (never real plumbing): warm water flow >
      ~50 pulses → relay clicks OFF, payload shows unattended_hot_fill=true.
      Power-cycle afterwards → relay back OPEN (default-safe).
- [ ] POST a manual voice trigger (curl in Board 4 above) → within ~1 s Board 5
      claims it (`role=play`) and the DFPlayer plays track 1 through the
      amplifier clearly. Record/deploy that prompt clip onto the SD card first.

---

## 3. Phase F — full integration checklist (Network Guide Section 2.5)

With all 5 boards powered and pointed at the Pi:

- [ ] All three edge services running on the Pi:
      `systemctl status safebath safebath-fusion safebath-worker`
      (fresh Pi: `deployment/raspberry_pi/install.sh`).
- [ ] `curl http://localhost:8000/api/v1/devices` lists all five device_ids
      (mat-01, radar-01, toilet-01, audio-01, tof-01).
- [ ] `curl http://localhost:8000/api/latest`: interleaved traffic from every
      module; no board dominating or silent. Rejected payloads show up loudly in
      `journalctl -u safebath -f` (that board drifted from the contract — tell me
      which one).
- [ ] Stop the mock publisher if it was running (`mock_publisher.py`) — real
      data replaces simulated data with zero code changes.
- [ ] Dashboard Live mode: all module cards show REAL values, heatmap animates
      from the actual Velostat scan, event log fills.
- [ ] One deliberate danger trigger end-to-end (e.g. cover all 3 ToF):
      card turns alert color → log entry → DFPlayer asks → say nothing →
      caretaker PWA gets a push (set `SUPABASE_URL` + `VAPID_*` keys in
      `/etc/safebath/safebath.env`; without them the alert stays local and
      nothing is lost).
- [ ] Cross-modal sanity: thud while standing still (no motion) does NOT
      escalate; stomp (motion confirmed) DOES.

## 4. Model training once TensorFlow is installed

- [x] `pip install tensorflow` (2.16.2 + `tf-keras` 2.16.0; the legacy-Keras
      env var is set automatically by every training/ and fusion_engine script —
      Keras 3 cannot convert or deserialize the recurrent layers).
- [x] Fall ConvLSTM trained on synthetic data (`hold-out accuracy 1.000`,
      DoD ≥ 0.85) → `models/fall_risk_model.h5` + quantized `.tflite`
      (34.4 KB) + firmware header in `firmware/shared/model_data/`.
      NOTE: the .tflite carries Flex ops (TF 2.16 cannot lower ConvLSTM loops
      to builtin kernels) — laptop/eval only until restructured for TFLite-Micro.
      Server-side inference via `fusion_engine.py` uses the .h5 and works.
- [x] Thermal CNN+LSTM trained (`1.000`) → `.h5` + `.tflite` (44 KB, same Flex
      caveat) + header. Backend keeps its avg-temp heuristic by design; wire the
      model in later if you want sequence-based scald detection server-side.
- [ ] After Board-1 calibration constants are updated, RETRAIN both:
      `python datasets/gen_mat_data.py && python training/train_fall_convlstm.py`
- [ ] `python datasets/fetch_speech_commands.py` (~2.4 GB) then
      `python training/train_voice_kws.py` → drop resulting
      `voice_kws_model.tflite` into `firmware/shared/model_data/` flow via
      `python training/convert_to_tflite.py`.
- [ ] Audio head (Module 4): `python training/train_audio_transfer.py` after
      real feature captures land in `data/real/` (synthetic-only recall is the
      known failure mode — evaluate_all flags it).
- [ ] After a few days of REAL PIR events:
      `python training/train_routine_isoforest.py` (it no-ops before ~50 visits
      by design — do not fake this one).
