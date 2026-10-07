# SafeBath AI — Final Hardware Wiring + Complete Software Master Guide
### Reflects actual confirmed connections across all 5 boards — supersedes every earlier wiring doc

---

## 0. What Changed From Earlier Plans (read this first)

| Change | Old plan | **Final, as wired** |
|---|---|---|
| Module 4 mic | 2× INMP441 (I2S digital) | **1× MAX4466 (analog)** — simpler, one board, different firmware sampling approach (Section 6) |
| Module 1 mux/shift-register pins | GPIO4/5/12/13 (mux), GPIO16/17/18 (shift reg) | **GPIO16/17/18/19 (mux select), GPIO23/25/26 (shift reg)** |
| Module 1 grid size | Assumed 8×8 (64 cells) via partial wiring | **Confirmed 16×4 = 64 cells** — CD4067's full 16 channels as rows, 74HC595's 4 outputs (Q1–Q4) as columns. Same total resolution as originally intended, different aspect ratio. |
| Module 3 | MQ-135, MQ-3, TCS3200, DS18B20 (temp) | **MQ-135, MQ-3, TCS3200 only — no DS18B20 in the final connection list.** Flagging this: `temp_c` drops out of the toilet model unless you're adding it separately later. I'm treating it as optional/removed below — say the word if it's still coming. |
| Module 5 relay/flow/DFPlayer pins | GPIO4 (relay), GPIO27 (flow), GPIO16/17 (DFPlayer) | **GPIO33 (relay), GPIO34 (flow), GPIO16/17 (DFPlayer, unchanged)** |
| Module 5 ToF XSHUT pins | GPIO13/14/15 | **GPIO25/26/27** |
| Module 5 | — | **+ separate amplifier module** boosting DFPlayer's audio output to the speaker |
| Status | — | **Relay: confirmed working, powered. DFPlayer: confirmed working.** |

Everything below (data contracts, AI models, alert thresholds, opencode task list) is rebuilt against these final pins.

---

## 1. Final Confirmed Wiring — All 5 Boards

### BOARD 1 — Pressure Mat (Fall Risk) + Entry Detection
*What this module solves: detects unsteady gait, sway, and collapse patterns from footstep pressure over time, plus flags when someone enters/exits the bathroom.*

| Component | Pin | ESP32 GPIO |
|---|---|---|
| PIR sensor | GND / VCC / OUT | GND / 3.3V / **GPIO27** |
| CD4067 mux | GND / VCC / EN | GND / 3.3V / GND (tied enable) |
| CD4067 mux | S0 / S1 / S2 / S3 | **GPIO16 / 17 / 18 / 19** |
| CD4067 mux | SIG | **GPIO34** (ADC1, input-only) |
| 74HC595 shift register | VCC / GND / OE / MR | 3.3V / GND / GND (tied low) / 3.3V (tied high) |
| 74HC595 | DS / SH_CP / ST_CP | **GPIO23 / GPIO25 / GPIO26** |
| 74HC595 | Q1–Q4 | → 4 column outputs (Velostat — Section 3) |

**Grid math:** CD4067 is a 16-channel mux → 16 row inputs. 74HC595 gives 4 usable outputs → 4 column drives. **16 × 4 = 64 sensing cells** — same total resolution the AI model expects, just a 16×4 rectangle instead of 8×8. Model input shape below is updated to match.

### BOARD 2 — Presence/Motion Radar (DFRobot C4001)
*Solves: detects whether someone is present and moving, independent of the mat — a second, non-contact confirmation signal for collapse detection.*

| Component | Pin | ESP32 GPIO |
|---|---|---|
| C4001 | VIN / GND | 3.3V / GND |
| C4001 | RX | **GPIO17 (TX2)** |
| C4001 | TX | **GPIO16 (RX2)** |

### BOARD 3 — Toilet Biomarker Sensing
*Solves: flags possible UTI or dehydration risk from urine color/turbidity and airborne chemical signature, without a camera.*

| Component | Pin | ESP32 GPIO |
|---|---|---|
| TCS3200 | GND / OE / VCC | GND / GND (tied low) / 3.3V |
| TCS3200 | S0 / S1 / S2 / S3 | **GPIO18 / 19 / 21 / 22** |
| TCS3200 | OUT | **GPIO23** |
| MQ-135 | A0 / GND / VCC | **GPIO35** / GND / VIN (5V) |
| MQ-3 | A0 / GND / VCC | **GPIO34** / GND / VIN (5V) |

### BOARD 4 — Acoustic Distress Detection
*Solves: listens for distress sounds (impact, cries, calls for help) and — after a Voice Bridge prompt from Board 5 — listens for the person's spoken reply.*

| Component | Pin | ESP32 GPIO |
|---|---|---|
| MAX4466 | VCC / GND | 3.3V / GND |
| MAX4466 | OUT | **GPIO34** (analog) |

### BOARD 5 — ToF Fall Detection + Thermal/Scald Safety + Voice Bridge Output
*Solves three things at once: (a) confirms a collapse volumetrically via 3 distance sensors, (b) shuts off water automatically if a hot tap is left running unattended, (c) plays the Voice Bridge's spoken check-in through the speaker.*

| Component | Pin | ESP32 GPIO |
|---|---|---|
| I2C bus (ToF ×3 + AMG8833) | SDA / SCL | **GPIO21 / GPIO22** |
| VL53L1X #1 | VIN/GND/SCL/SDA/XSHUT | 3.3V/GND/22/21/**GPIO25** |
| VL53L1X #2 | XSHUT | **GPIO26** |
| VL53L1X #3 | XSHUT | **GPIO27** |
| AMG8833 | VIN/GND/SCL/SDA | 3.3V/GND/22/21 |
| DFPlayer Mini | VCC / GND | VIN / GND |
| DFPlayer | RX / TX | ESP32 **TX2 (GPIO17)** / **RX2 (GPIO16)** |
| DFPlayer | SPK_R | → Speaker R / amplifier input |
| Relay module | V / G / IN | 3.3V / GND / **GPIO33** — ✅ confirmed working, powered |
| Water flow sensor | V / S / G | 5V(VIN) / **GPIO34** / GND |
| Amplifier module | + / − / R | POWER+ / GND / audio signal from DFPlayer — ✅ confirmed working |

**GPIO conflict check (Board 5):** 21, 22, 25, 26, 27, 16, 17, 33, 34 — nine pins, all distinct, none in the flash-reserved 6–11 range, none of the boot-sensitive 0/2/15 pins used. Clean.

---

## 2. Velostat — Complete Connection Guide

You have a **16-row × 4-column** matrix ready to receive it (64 total sensing points), not a small 4×4 grid — CD4067 gives you all 16 row channels, 74HC595's Q1–Q4 give you the 4 columns.

### 2.1 Physical Assembly

1. **Cut 16 row conductors** (copper tape or thin solid-core wire), long enough to span the mat's width, and **4 column conductors**, long enough to span its length.
2. On a rigid backing board, lay the **16 row strips** parallel to each other, evenly spaced, running in one direction. Tape/glue in place.
3. Cut a Velostat sheet to match the row layer's footprint and lay it directly over all 16 rows.
4. Lay the **4 column strips** on top of the Velostat, perpendicular to the rows, evenly spaced across the mat's other dimension. Each row/column pair now crosses at exactly one point through the Velostat — **16 × 4 = 64 pressure-sensing cells**.
5. Wire each of the 16 row leads to one CD4067 input channel (Y0–Y15 on the mux breakout).
6. Wire each of the 4 column leads to one 74HC595 output (Q1–Q4).
7. Confirm a **10kΩ pull-down resistor** sits between the mux `SIG` line and GND — required so an unselected/unpressed cell reads a clean near-zero baseline instead of floating noise.
8. Before sealing with a top protective layer, test every cell individually via Serial Monitor: press each of the 64 intersections one at a time and confirm only that cell's reading changes.

### 2.2 How It Reads Electrically

Same piezoresistive voltage-divider principle as before, now scanned across the full matrix:

1. Firmware drives **one column HIGH** at a time via the shift register (Q1→Q2→Q3→Q4).
2. For that column, firmware cycles the **mux through all 16 row channels**, reading `analogRead(GPIO34)` at each.
3. Velostat's resistance at each row/column crossing sets the divider ratio: unpressed = high resistance = low ADC reading; pressed = resistance drops = ADC reading rises.
4. One full scan = 4 columns × 16 rows = **64 ADC reads**, assembled into one frame.

### 2.3 Firmware Scan Loop

```cpp
#define MUX_S0 16
#define MUX_S1 17
#define MUX_S2 18
#define MUX_S3 19
#define MUX_SIG 34

#define SR_DS 23
#define SR_SHCP 25
#define SR_STCP 26

int frame[16][4];  // [row][col]

void selectMuxChannel(int ch) {
  digitalWrite(MUX_S0, bitRead(ch, 0));
  digitalWrite(MUX_S1, bitRead(ch, 1));
  digitalWrite(MUX_S2, bitRead(ch, 2));
  digitalWrite(MUX_S3, bitRead(ch, 3));
}

void driveColumn(int col) {
  // Shift out a single HIGH bit at position `col`, rest LOW
  digitalWrite(SR_STCP, LOW);
  for (int i = 3; i >= 0; i--) {
    digitalWrite(SR_SHCP, LOW);
    digitalWrite(SR_DS, (i == col) ? HIGH : LOW);
    digitalWrite(SR_SHCP, HIGH);
  }
  digitalWrite(SR_STCP, HIGH);
}

void scanMat() {
  for (int col = 0; col < 4; col++) {
    driveColumn(col);
    delayMicroseconds(50);
    for (int row = 0; row < 16; row++) {
      selectMuxChannel(row);
      delayMicroseconds(20);
      frame[row][col] = analogRead(MUX_SIG);
    }
  }
}
```

### 2.4 Showing Live Velostat Readings — Visualization Ideas

1. **Live heatmap grid** on the AI/Module Dashboard's Board 1 card: a 16×4 colored grid (blue = no pressure → red = high pressure), redrawn every scan cycle. Most useful for both debugging wiring and demoing to judges — they can literally see a footprint appear.
2. **Numeric debug overlay**: same grid but showing raw ADC values instead of color, toggled on for calibration sessions.
3. **Center-of-pressure trail**: compute a weighted centroid across all 64 cells each frame, plot its movement over the last few seconds as a short trail line — this is visually close to what the ConvLSTM model is actually learning from (sway trajectory), so it doubles as an "explainability" view.
4. **Physical demo option** (optional, not required): a WS2812 addressable LED strip taped under the mat, one LED per row, driven directly from the same firmware readings — lights up under someone's foot in real time. Good stage presence, doesn't touch the software pipeline.

I can build #1 and #3 straight into `SafeBathApp.jsx`'s Board 1 card once you confirm — say the word and I'll add it as a live canvas/SVG component reading from the same `sensor/mat/frame` data.

---

## 3. Updated Data Contract (MQTT)

| Board | Topic | Payload | Notes |
|---|---|---|---|
| 1 | `sensor/mat/frame` | CSV string, **64 ints** (16 rows × 4 cols, row-major) | was 64 ints assumed 8×8 before — same count, different shape |
| 1 | `sensor/mat/pir` | `{"entry": true}` | unchanged |
| 2 | `sensor/radar/status` | `{"presence": bool, "motion": bool, "stillness_ms": int, "possible_collapse": bool}` | unchanged |
| 3 | `sensor/toilet/reading` | `{"gas_ppm": float, "voc_ppm": float, "turbidity": float, "hour_of_day": int}` | **`temp_c` removed** — DS18B20 not in final wiring |
| 4 | `sensor/audio/event` | `{"label": "silence"\|"water"\|"impact_thud"\|"speech_or_cry", "energy": float}` | now includes raw energy level since MAX4466 is analog — useful for Voice Bridge reply detection (Section 8) |
| 5 | `sensor/tof_thermal/status` | `{"avg_thermal_c": float, "flow_pulses": int, "tof_collapse": bool, "unattended_hot_fill": bool}` | unchanged |
| 5 | `voice/trigger` (sub) / `voice/reply` (pub) | `{"prompt": "are_you_ok"}` / `{"reply": "fine"\|"help"\|"no_response"}` | unchanged |

---

## 4. What Each Module Is Actually Solving (plain language)

| Module | Problem it solves |
|---|---|
| 1 — Mat | *"Did they just lose their footing or collapse?"* — passive fall-risk sensing with zero wearables |
| 2 — Radar | *"Is someone in there but not moving?"* — a second, contactless confirmation that doesn't depend on the mat |
| 3 — Toilet | *"Is there an early sign of infection or dehydration?"* — the kind of thing families usually only find out about after a hospital visit |
| 4 — Audio | *"Did we just hear a fall, or a cry for help?"* — never stores raw audio, only classification labels, by design |
| 5 — ToF/Thermal | *"Did someone actually go down, and is a hot tap about to scald them unattended?"* — two safety functions bundled onto one board because they share the same physical vantage point (ceiling-mounted) |
| Fusion Engine | *"Is this one weird reading, or an actual pattern?"* — combines all of the above into one score with decay, so isolated blips don't cause alarm fatigue |
| Voice Bridge | *"Before we call someone, let's just ask."* — the differentiator: talks to the person first, only escalates on silence or "help" |

---

## 5. AI/ML Algorithms & Models (updated for final hardware)

| # | Module | Model | Framework | Input shape | Runs where |
|---|---|---|---|---|---|
| 1 | Fall Risk | ConvLSTM2D → Dense(3) | TFLite Micro | **(20, 16, 4, 1)** — updated for the confirmed 16×4 grid | On ESP32 (Board 1) |
| 2 | Presence/Motion | Rule: `presence AND stillness_ms > 120000` | Plain C++ | — | On ESP32 (Board 2) |
| 3 | Toilet Biomarkers | Random Forest, **3 features** (gas_ppm, voc_ppm, turbidity) | scikit-learn | tabular | Laptop |
| 4 | Acoustic Distress | Lightweight energy/spectral feature classifier (see 5.1 below) — **not** the YAMNet-embedding approach from earlier, since that assumed a digital I2S mic | TFLite Micro or plain rule-thresholds | 1D feature vector | On ESP32 (Board 4) |
| 5 | Volumetric Fall | Rule-based trilateration | Plain C++ | — | On ESP32 (Board 5) |
| 6 | Thermal Scald/Freeze | CNN + LSTM | TFLite (laptop) | (15, 8, 8, 1) | Laptop |
| 7 | Risk Fusion Engine | Weighted ensemble + severity decay | Python | — | Laptop |
| 8 | Alert Resolution | State machine | Python | — | Laptop |
| 9 | Routine Anomaly | Isolation Forest | scikit-learn | tabular, real data only | Laptop |
| 10 | Voice Bridge | Keyword spotting (fine/help/no_response) | TFLite Micro | MFCC features | On ESP32 (Board 5, triggered), **listens via Board 4** |

### 5.1 Why Module 4's model changed with MAX4466

INMP441 (the earlier plan) is a digital I2S MEMS mic — clean 16-bit samples, ideal for a pretrained model like YAMNet. **MAX4466 is analog** — you're reading raw voltage through the ESP32's ADC, which is noisier and can't be I2S-clocked the same way. The practical adaptation:

1. Sample continuously at ~8–16kHz using a hardware timer + `analogRead()` (or the ESP32's ADC continuous-read mode for less jitter).
2. Compute lightweight features per ~0.5s window instead of feeding raw samples to a large model: **RMS energy**, **zero-crossing rate**, and a coarse **spectral centroid** (small FFT, e.g. via the `arduinoFFT` library, 256-point is enough).
3. Classify with either simple thresholds (fast to build, good enough for silence/water/impact_thud) or a tiny dense network (3 features → Dense(8) → Dense(4)) if you want it trained rather than hand-tuned.
4. This is a legitimate, common approach for analog-mic embedded audio classification — not a downgrade in rigor, just matched to what the sensor can actually deliver.

**Definition of done:** `sensor/audio/event` still publishes one of the same 4 labels the rest of the system expects — Section 3's contract doesn't change, only how Board 4 arrives at the label internally.

---

## 6. Datasets (updated)

Same dual-track strategy as before (fetch real + generate synthetic), with one change:

| Module | Fetch | Generate |
|---|---|---|
| 1 — Mat | — | `gen_mat_data.py`, **update grid shape to (20, 16, 4)** |
| 3 — Toilet | — | `gen_toilet_data.py`, **drop `temp_c` from the feature set** |
| 4 — Audio | Optional: ESC-50 for "water"/ambient reference clips, downsampled and re-featurized (RMS/ZCR/centroid) to match what Board 4 actually computes — skip RAVDESS/YAMNet-embedding approach, it assumed the old mic | `gen_audio_data.py`, output raw waveforms → run through the **same RMS/ZCR/centroid feature extractor** the firmware uses, so training and inference compute identical features |
| 6 — Thermal | — | `gen_thermal_data.py`, unchanged (8×8 AMG8833 already matched) |
| 10 — Voice Bridge | Google Speech Commands (unchanged) | — |

**Definition of done:** every dataset used to train a model produces features through the *exact same code path* the firmware uses at inference time — this is the most common source of train/inference mismatch in TinyML projects, worth being strict about.

---

## 7. Danger Thresholds & Alert Mapping

This is the table that answers *"what counts as dangerous, and what happens when it does"* — every module maps to a specific condition and a specific response.

| Module | Danger condition | Severity | Response |
|---|---|---|---|
| 1 — Mat | ConvLSTM classifies "fall" for 2+ consecutive frames | **High** | Immediate Voice Bridge trigger |
| 1 — Mat | Sustained "unsteady" classification > 10s | Medium | Contributes to fused score, no direct Voice Bridge trigger alone |
| 2 — Radar | `possible_collapse = true` (presence + stillness > 120s) | **High** | Voice Bridge trigger |
| 3 — Toilet | RF classifies `uti_risk` 2+ readings in a row | Medium | Logged, surfaced on dashboard + user app, **not** an emergency alert (this is a "mention to your doctor" signal, not a 911-tier one) |
| 3 — Toilet | RF classifies `dehydration_risk` repeatedly within 24h | Medium | Same as above, plus a gentle reminder notification |
| 4 — Audio | `impact_thud` **and** Module 2 confirms presence+motion at same timestamp (cross-modal gate, Section 12.4 of the earlier spec) | **High** | Voice Bridge trigger |
| 4 — Audio | `speech_or_cry` sustained > 5s | Medium–High | Voice Bridge trigger |
| 5 — ToF | `tof_collapse = true` | **High** | Voice Bridge trigger |
| 5 — Thermal/Flow | `unattended_hot_fill = true` **and** mat shows no one standing there | **High** | **Immediate automatic action** — relay cuts solenoid, no waiting for Voice Bridge (water safety can't wait on a conversation) |
| Fusion score | Decayed score > 60 | **High** | Escalate past Voice Bridge to Telegram + user app push, if Voice Bridge got "help" or no reply |
| Fusion score | Decayed score 30–60 | Watch | Dashboard/user app shows "keeping an eye on things," no push notification |
| Fusion score | Decayed score < 30 | Normal | No action |

**Definition of done:** this exact table becomes `backend/alert_rules.py` — one function per row, each unit-tested, so "what counts as danger" lives in one reviewable place instead of scattered across `fusion_engine.py`.

---

## 8. Alert & Notification System

**Escalation order, every time:**
```
danger condition detected
   → Voice Bridge triggered first (except unattended hot fill — Section 7 — that's immediate/automatic)
   → DFPlayer (Board 5, through the amplifier) plays: "Are you alright? Say yes, or say help."
   → Board 4 (MAX4466) listens for ~8 seconds, computes RMS energy + speech features
   → classified as: fine / help / no_response
   → "fine" → stand down, log the event, nothing further happens
   → "help" or "no_response" → escalate to Telegram bot + push notification in the user app
```

**Notification channels:**
| Channel | Who sees it | When |
|---|---|---|
| Voice Bridge (in-bathroom speaker) | The person themselves | First response to any high-severity module trigger |
| Telegram bot message | Caregiver/family | Only after Voice Bridge escalates (help/no_response), or immediately for unattended hot fill |
| User app push + banner | Caregiver/family | Same trigger as Telegram, redundant channel |
| AI/Module Dashboard banner | Technician/judge/whoever's monitoring the console | Any module crossing its threshold, including Medium severity — this view is meant to show everything, not just what reaches a caregiver |
| Dashboard event log | Anyone with the AI dashboard open | Every event, all severities, for traceability |

**Why Medium-severity toilet biomarker events don't page anyone:** a UTI-risk signal isn't an emergency — it's the kind of thing that's useful to notice as a *trend*, not react to in real time. Paging a caregiver for it would be exactly the alarm-fatigue problem the Voice Bridge/severity-decay design is meant to avoid elsewhere.

---

## 9. User Ends & Perspectives

| Perspective | What they see | Where |
|---|---|---|
| **The elderly person, in the moment** | Nothing on a screen — just the spoken Voice Bridge check-in through the speaker. This is deliberate: no UI to interact with during a possible emergency. | Board 5 speaker only |
| **Caregiver/family** | Plain-language household status, recent activity in everyday terms, Telegram/push alerts only when something actually needs their attention | User View page + Telegram + push |
| **Technician/hackathon judge** | Full technical picture — fused risk score, every module's live reading, AI model used, accuracy, event log | AI/Module Dashboard page |
| **Whoever's doing hardware bring-up** | Per-board wiring reference, firmware snippets, wired/pending status | Hardware & Connections page |

---

## 10. Complete Software Build Plan for `opencode` — Module by Module

Your instruction was explicit: build and verify **one module at a time**, standalone, before wiring anything together — so when you connect all 5 boards later, each one's software is already proven correct in isolation.

```
opencode run "Read SafeBath_AI_Final_Master_Guide.md Section 10. Work through
Phase A through Phase F in order. Do not begin a phase until the previous
phase's Definition of Done is met. Each module's firmware must run and
publish correct MQTT data standalone, verified against Section 3's contract,
before touching fusion_engine.py."
```

### Phase A — Board 1 (Mat) standalone
- Implement the scan loop from Section 2.3.
- Publish `sensor/mat/frame` (64 CSV ints) at 10Hz.
- **Verify:** Serial Monitor shows all 64 values changing correctly per Section 2.1 step 8's per-cell test, *before* connecting to MQTT.
- Then confirm `mosquitto_sub -t sensor/mat/frame` on the laptop receives clean frames.

### Phase B — Board 2 (Radar) standalone
- Implement `DFRobot_C4001` library read + the stillness-duration rule (Section 5, Module 2).
- **Verify:** walk in and out of range, confirm `presence`/`motion` flip correctly in Serial Monitor; stand still for 2 minutes and confirm `possible_collapse` flips true at the 120s mark.

### Phase C — Board 3 (Toilet) standalone
- Read TCS3200 (interrupt pulse-counting on GPIO23) + both MQ sensors.
- Publish per Section 3's updated payload (no `temp_c`).
- **Verify:** breathe on the MQ sensors / shine different colors at the TCS3200, confirm readings respond.

### Phase D — Board 4 (Audio) standalone
- Implement the RMS/ZCR/spectral-centroid feature extraction from Section 5.1.
- Start with threshold-based classification (faster to verify than training a model first).
- **Verify:** clap near the mic, run water nearby, stay silent — confirm the 3 scenarios produce 3 different labels reliably before anything fancier.

### Phase E — Board 5 (ToF + Thermal + Voice Bridge) standalone
- Bring up I2C bus, assign unique ToF addresses via the XSHUT sequence (now on GPIO25/26/27).
- Confirm relay + DFPlayer + amplifier (already working per your note) integrate cleanly with the new ToF/thermal code without pin conflicts (checked in Section 1 — clean).
- **Verify:** trigger the ToF collapse rule manually (wave a hand close to all 3 sensors), confirm the relay logic fires correctly with the bucket-and-tube rig, confirm DFPlayer plays through the amplifier.

### Phase F — Integration (only after A–E each pass standalone)
- Bring up `mqtt_bridge.py`, `schemas.py` (updated per Section 3), `fusion_engine.py`, `alert_rules.py` (Section 7).
- Wire Voice Bridge's cross-board flow: Board 5 publishes `voice/trigger`, Board 4 must be listening and able to publish `voice/reply` — this is the one place two boards' firmware need to agree on timing (an ~8s listen window after the prompt plays).
- Run all 5 boards simultaneously for the first time only here — every module having already been individually verified means integration bugs are isolated to *interaction* issues, not "is this sensor even working" issues.

**Definition of done for Section 10:** each phase's individual verification step is captured as a short log/checklist entry (even a text file per board is fine) before opencode is allowed to move to Phase F — this is the actual mechanism that prevents the "worked alone, broke together" failure mode you're trying to avoid.

---

## 11. Fusion Engine Weights (updated)

```python
WEIGHTS = {
    "fall_risk": 0.26,          # Module 1
    "possible_collapse": 0.20,  # Module 2
    "tof_collapse": 0.20,       # Module 5
    "acoustic_distress": 0.18,  # Module 4
    "thermal_freeze": 0.11,     # Module 6
    "toilet_biomarker": 0.05,   # Module 3 (lowered — Medium severity only, see Section 7)
}
```

---

## 12. Immediate Next Steps

1. Confirm whether DS18B20 is still coming — if yes, I'll add it back into Section 3's contract and Module 3's model; if no, current `temp_c`-free version stands as final.
2. Say the word on the live Velostat heatmap (Section 2.4) and I'll add it to `SafeBathApp.jsx`.
3. Once Phase A (Board 1) is verified per Section 10, send me real ADC ranges from your Velostat so I can replace the placeholder pressure-normalization constants with calibrated ones.
