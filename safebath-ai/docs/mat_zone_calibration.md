# Four-zone Velostat mat — calibration & validation procedure

This document is the **the only sanctioned way** to obtain the numbers that go
into the four-zone mat configuration. Nothing here is measured yet: the values
below are procedure, not results. Do **not** claim calibrated pressure, force or
weight — every raw reading is a 12-bit ADC count (0–4095) until *you* measure it.

> **Hardware status: NOT WIRED / NOT VALIDATED.** The four-zone mat has not been
> assembled or measured. Until steps 1–7 below are run on real hardware, the
> firmware, the calibration file and every threshold are unverified.

## 0. What a zone is

The mat is a 30 cm × 30 cm Velostat sheet with four independent sensing zones in
a 2×2 layout. Each zone is its own electrode pair (one copper electrode on the
top face, a matching one on the bottom face) with the Velostat sandwiched
between them, and insulating gaps between neighbours. Each zone wires to a
**separate ESP32 ADC1 input** as a voltage divider — see
`firmware/mat_node/mat_node.ino`.

Canonical, positional zone order (never reorder these):

| Index | Name                  | Position     |
|-------|-----------------------|--------------|
| 0     | `zone_1_top_left`     | Top-left     |
| 1     | `zone_2_top_right`    | Top-right    |
| 2     | `zone_3_bottom_left`  | Bottom-left  |
| 3     | `zone_4_bottom_right` | Bottom-right |

A telemetry frame is exactly four finite, non-negative numbers in that order.
The canonical order is defined once in `backend/schemas.py` (`MAT_ZONE_ORDER`)
and mirrored in `frontend/matZones.mjs` (`ZONE_ORDER`); a test asserts they match.

## 1. Equipment

- The assembled four-zone mat, wired to the ESP32 board and powered.
- A known, repeatable press: a small weight / firm flat object (a mug or a stack
  of books works). Consistency matters more than the absolute value.
- USB serial monitor at 115200 baud.

## 2. Record each zone's unloaded baseline

1. Flash `firmware/mat_node/mat_node.ino` with `DEBUG_SERIAL_ONLY true` and
   `MAT_ZONE_POLARITY_INCREASE` set to your measured polarity (see step 3).
2. Leave the mat completely unloaded. Wait ~30 s for the ADC/ESP32 to settle.
3. Read the `Z1..Z4` values printed every 500 ms.
4. For each zone, record the **median** of at least 100 idle samples.
   These are the per-zone `baseline` values.

Idle readings from a floating/unconnected input are **invalid** — if a zone's
baseline drifts wildly or sits at 0/4095, the divider is mis-wired; fix it before
continuing.

## 3. Determine polarity

Velostat resistance usually falls under pressure, so a fixed resistor to GND
divider usually makes the ADC node voltage **fall**. Confirm on real hardware:

- Press a zone firmly and watch its raw value. If it **rises**, set
  `MAT_ZONE_POLARITY_INCREASE 1` and use `"polarity": "increase"` in the
  calibration JSON. If it **falls**, use `0` / `"decrease"`.

The firmware normalizes the raw ADC so that, after this flag, a larger number
always means *more* pressure. Nothing downstream guesses polarity.

## 4. Press each zone individually

For each zone Z1–Z4, in order:

1. Press only that zone with the repeatable weight.
2. Record the median pressed value over ≥50 samples → that zone's `max`.
3. Release fully and confirm the reading **returns toward the baseline**. A
   large residual offset means mechanical set/creep or a saturated divider.

## 5. Test adjacent zones for electrical crosstalk

- Press Zone 1 and record the response of Zones 2, 3 and 4.
- Repeat for each zone.
- **Pass:** the un-pressed neighbours stay within their idle noise band.
- **Fail (crosstalk):** a neighbour rises materially. Cause is almost always a
  short/overlap between adjacent electrodes (insulating gaps not clean) or a
  shared ground/divider node. Fix the hardware — do not "fix" it in software.

## 6. Repeat and quantify noise

- Repeat steps 4–5 **at least 5 times** per zone.
- Compute, per zone: mean, standard deviation, and min/max of both the idle and
  pressed readings.
- If the idle noise band is wide relative to the press response, increase
  `MAT_ZONE_SAMPLES` in the firmware (more oversampling) before setting
  thresholds.

## 7. Establish per-zone activation thresholds (from real data)

Pick thresholds **between** the measured idle band and the pressed band, not at
round numbers chosen in advance:

- **Activation / "pressure present"**: idle_mean + k·idle_std (k ≈ 3–5), and
  below pressed_mean. Record the exact per-zone value.
- **Qualitative bands** (Low / Medium / High) are percentages of each zone's
  measured `(max − baseline)` response and are configurable in
  `frontend/matZones.mjs` (`DEFAULT_BANDS`). These are **relative responses**,
  never physical units.

Feed the measured values to the backend as configuration (nothing is hard-coded):

```bash
# inline JSON ...
SAFEBATH_MAT_ZONE_CALIBRATION='{"polarity":"increase","zones":[
  {"baseline":<Z1 idle>,"max":<Z1 pressed>},
  {"baseline":<Z2 idle>,"max":<Z2 pressed>},
  {"baseline":<Z3 idle>,"max":<Z3 pressed>},
  {"baseline":<Z4 idle>,"max":<Z4 pressed>}]}'
# ... or point at a file
SAFEBATH_MAT_ZONE_CALIBRATION_FILE=/etc/safebath/mat_zones.json
```

With no calibration configured the dashboard shows **raw ADC only** and an
explicit "Uncalibrated" badge — it never invents a percentage.

## 8. Validation artefacts

Keep, for the record: the raw idle/pressed sample logs per zone, the crosstalk
matrix, and the final calibration JSON. These are the evidence that the mat was
validated; the thresholds are only as good as the samples behind them.
