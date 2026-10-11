/*
 * SafeBath AI — Board 1: Four-Zone Velostat Pressure Mat (Fall Risk) + PIR
 * Phase A standalone node.
 *
 * PHYSICAL DESIGN (this is the active hardware, not the old scanned grid):
 *   A 30 cm x 30 cm Velostat sheet with four independent sensing zones in a
 *   2x2 layout. Each zone is its own electrode pair — one copper electrode on
 *   the top face and a corresponding one on the bottom face — with the Velostat
 *   sandwiched between them. Small insulating gaps separate adjacent zones so
 *   the four electrodes do not short together. Each zone has its own electrical
 *   connection, i.e. four independent resistive sensors, NOT a scanned
 *   row/column matrix.
 *
 *     +---------------------+---------------------+
 *     | Zone 1 (top-left)   | Zone 2 (top-right)  |
 *     +---------------------+---------------------+
 *     | Zone 3 (bottom-left)| Zone 4 (bottom-right)|
 *     +---------------------+---------------------+
 *
 * READOUT: each zone is a variable resistance that changes with pressure. Wire
 *   each zone as a voltage divider (zone element in series with a fixed
 *   resistor) and read the divider node on a dedicated ESP32 ADC1 input. A
 *   single analogRead per zone cannot be trusted for a real decision, so each
 *   zone is oversampled and averaged (see readZone()).
 *
 *   >>> HARDWARE BLOCKER — NOT YET VALIDATED <<<
 *   The four-zone mat has NOT been wired or measured. This sketch uses a
 *   PROPOSED, safe ADC1 pin map (below). Before this firmware is trusted you
 *   must:
 *     1. Confirm the four zone electrodes' actual GPIO connections and update
 *        MAT_ZONE_ADC_PINS to match. Do not assume this proposal is your build.
 *     2. Confirm the divider resistor arrangement. The reference image shows a
 *        10 kOhm resistor on the signal side, which is NOT a complete circuit
 *        spec: with a fixed 10 kOhm to GND the divider node voltage V = 3.3V *
 *        R_zone / (R_zone + 10k), so it RISES with pressure if R_zone rises and
 *        FALLS if R_zone falls (Velostat's resistance typically falls under
 *        pressure, i.e. "decrease" polarity). Measure yours and set
 *        MAT_ZONE_POLARITY_INCREASE accordingly.
 *     3. Never drive a high-current load from an ESP32 GPIO — the mat only ever
 *        sits on an ADC input.
 *
 *   Until (1) and (2) are done, no physical zone reading has been verified.
 *
 * TRANSPORT (unchanged, working): POSTs the v1 contract to the RPi over HTTP
 * (no MQTT). Four-zone frame:
 *   POST /api/v1/telemetry
 *   payload {"sensor_type":"pressure_4zone","zones":[z1,z2,z3,z4]}
 *   with the SAME device identity ("mat-01"), Wi-Fi config, server address,
 *   HTTP transport, boot-id and per-boot sequence validation as before.
 * The four readings are sent in the canonical order
 *   zone_1_top_left, zone_2_top_right, zone_3_bottom_left, zone_4_bottom_right
 * (backend/schemas.py MAT_ZONE_ORDER).
 *
 * LEGACY 12x6 GRID: the old scanned grid (CD4067 16-ch mux + 74HC595, 72 cells)
 * is retained ONLY for explicit backward compatibility. It is compiled out by
 * default; set MAT_ZONE_MODE to 0 to rebuild that path (POSTs {"values":[72]}).
 * Both paths share the same Wi-Fi/boot-id/sequence code.
 *
 * Libraries: none beyond the ESP32 core (HTTPClient + WiFi).
 */
#include "../common/SafeBathNode.h"

const char* ssid        = "OnePlus 5";
const char* password    = "Yatslove";
const char* server_host = "192.168.43.66";   // RPi IP (edge server)
const uint16_t server_port = 8000;
const char* node_token  = "";               // must match SAFEBATH_DEVICES token (if any)

safebath::Node node("mat-01", "mat", "1.0.0");

// Network bring-up Step 1 (Network & Autonomous Build Guide Section 1):
// keep true until the Serial-only sanity check passes — firmware reads the
// sensors and prints instead of touching the network entirely.
#define DEBUG_SERIAL_ONLY false

// --- mat sensing mode ------------------------------------------------------
// 1 = four-zone Velostat mat (2x2) — DEFAULT / active hardware
// 0 = legacy 12x6 scanned grid (72 cells, CD4067 + 74HC595) — explicit compat
#define MAT_ZONE_MODE 1
#define MAT_ZONE_COUNT 4

// PIR is unchanged (final confirmed wiring).
#define PIR_PIN 27

#if MAT_ZONE_MODE

/* Four independent zones -> four ESP32 ADC1 inputs.
 * PROPOSED pin map: {Z1,Z2,Z3,Z4} = {34,35,32,33}. These are all ADC1-safe on
 * an ESP32 DevKit (ADC2 shares the Wi-Fi radio and cannot be used while the
 * radio is up). INPUT-ONLY wiring: the mat only drives an ADC node through the
 * divider, never a load. CHANGE THIS ARRAY to match your physical build. */
static const int MAT_ZONE_ADC_PINS[MAT_ZONE_COUNT] = {34, 35, 32, 33};

// Oversampling: average N reads per zone to knock down ADC noise. Keep in sync
// with the calibration procedure (docs/mat_zone_calibration.md).
#define MAT_ZONE_SAMPLES 8

// ADC polarity: 1 if the raw reading RISES with pressure, 0 if it FALLS.
// Velostat resistance typically decreases under pressure, so a fixed
// resistor-to-GND divider usually reads FALLING voltage -> 0. MEASURE FIRST.
#define MAT_ZONE_POLARITY_INCREASE 0

// TODO(calibration): placeholder baseline/noise-model constants — replace with
// measured per-zone values from docs/mat_zone_calibration.md. These are NOT
// measured values and no calibrated pressure/force is claimed here.
#define CALIB_OFFSET 120
#define CALIB_GAIN   1.0

/* Read one zone: average MAT_ZONE_SAMPLES ADC samples on the zone's pin, then
 * clamp into the 12-bit ADC range. No unit conversion — the value is raw ADC
 * counts, matching the wire contract. */
int readZone(int pin) {
  long acc = 0;
  for (int i = 0; i < MAT_ZONE_SAMPLES; i++) {
    acc += analogRead(pin);
    delayMicroseconds(200);
  }
  int raw = (int)(acc / MAT_ZONE_SAMPLES);
  if (!MAT_ZONE_POLARITY_INCREASE) raw = 4095 - raw;   // normalize to rise-with-pressure
  return constrain((int)((raw - CALIB_OFFSET) * CALIB_GAIN), 0, 4095);
}

void scanZones(int out[MAT_ZONE_COUNT]) {
  for (int i = 0; i < MAT_ZONE_COUNT; i++) out[i] = readZone(MAT_ZONE_ADC_PINS[i]);
}

void printZones(const int zones[MAT_ZONE_COUNT]) {
  Serial.print("--- mat 4-zone");
  for (int i = 0; i < MAT_ZONE_COUNT; i++) {
    Serial.printf("  Z%d=%d", i + 1, zones[i]);
  }
  Serial.printf("  pir=%d\n", digitalRead(PIR_PIN));
}

#else  /* MAT_ZONE_MODE == 0 : LEGACY 12x6 SCANNED GRID (compat only) */

// Wiring = SafeBath_AI_Final_Master_Guide.md Section 1 (Board 1) — the OLD
// scanned-grid prototype. Kept for compatibility; not the active hardware.
#define MUX_S0 16
#define MUX_S1 17
#define MUX_S2 18
#define MUX_S3 19
#define MUX_SIG 34

#define SR_DS 23
#define SR_SHCP 25
#define SR_STCP 26

#define CALIB_OFFSET 120
#define CALIB_GAIN   1.0

int frame[12][6];  // [row][col] = 12 x 6 = 72 cells

void selectMuxChannel(int ch) {
  digitalWrite(MUX_S0, bitRead(ch, 0));
  digitalWrite(MUX_S1, bitRead(ch, 1));
  digitalWrite(MUX_S2, bitRead(ch, 2));
  digitalWrite(MUX_S3, bitRead(ch, 3));
}

void driveColumn(int col) {
  // Shift out a single HIGH bit at position `col`, rest LOW. MSB-first means
  // the HIGH bit lands on 74HC595 output Q(5-col) (6-bit shift). Keep the
  // physical Q1-Q6 -> column wiring consistent with that mapping.
  digitalWrite(SR_STCP, LOW);
  for (int i = 5; i >= 0; i--) {
    digitalWrite(SR_SHCP, LOW);
    digitalWrite(SR_DS, (i == col) ? HIGH : LOW);
    digitalWrite(SR_SHCP, HIGH);
  }
  digitalWrite(SR_STCP, HIGH);
}

void scanMat() {
  for (int col = 0; col < 6; col++) {
    driveColumn(col);
    delayMicroseconds(50);
    for (int row = 0; row < 12; row++) {
      selectMuxChannel(row);
      delayMicroseconds(20);
      frame[row][col] = analogRead(MUX_SIG);
    }
  }
}

/* Prints the full 12x6 frame to the Serial Monitor (115200 baud) so every cell
 * value can be checked on the bench without the dashboard/backend. */
void printMatGrid() {
  Serial.println("--- mat 12x6 (rows R0-R11 = CD4067 ch, cols C0-C5 = Q5..Q0) ---");
  for (int r = 0; r < 12; r++) {
    for (int c = 0; c < 6; c++) {
      if (c) Serial.print("  ");
      Serial.print(frame[r][c]);
    }
    Serial.println("");
  }
  int mn = 4095, mx = 0, sum = 0;
  int hotRow = -1, hotCol = -1;
  for (int r = 0; r < 12; r++)
    for (int c = 0; c < 6; c++) {
      int v = frame[r][c];
      sum += v;
      if (v < mn) mn = v;
      if (v > mx) { mx = v; hotRow = r; hotCol = c; }
    }
  Serial.printf("min=%d max=%d sum=%d peak@r%d,c%d pir=%d\n",
                mn, mx, sum, hotRow, hotCol, digitalRead(PIR_PIN));
}

#endif /* MAT_ZONE_MODE */

/* -------------------- Setup -------------------- */
void setup() {
  Serial.begin(115200);
  Serial.println();
  Serial.println("=================================");
  Serial.println("SafeBath AI - Mat Node 1 (v4 HTTP)");
  Serial.println("=================================");

#if MAT_ZONE_MODE
  // Four-zone dividers are read on ADC1 inputs; set a full 0-3.3 V attenuation
  // so the divider node spans the whole range.
  for (int i = 0; i < MAT_ZONE_COUNT; i++) {
    pinMode(MAT_ZONE_ADC_PINS[i], INPUT);
    analogSetPinAttenuation(MAT_ZONE_ADC_PINS[i], ADC_11db);
  }
  Serial.println("[mat] mode=four_zone (2x2) — NOT hardware-validated yet");
#else
  pinMode(MUX_S0, OUTPUT); pinMode(MUX_S1, OUTPUT);
  pinMode(MUX_S2, OUTPUT); pinMode(MUX_S3, OUTPUT);
  pinMode(SR_DS, OUTPUT); pinMode(SR_SHCP, OUTPUT); pinMode(SR_STCP, OUTPUT);
  Serial.println("[mat] mode=legacy 12x6 grid (compatibility)");
#endif
  pinMode(PIR_PIN, INPUT);

  node.begin(ssid, password, server_host, server_port, node_token);
  Serial.println("[main] mat node ready.");
}

/* -------------------- Loop -------------------- */
bool lastPirState = false;

void loop() {
#if MAT_ZONE_MODE
  int zones[MAT_ZONE_COUNT];
  scanZones(zones);
#else
  scanMat();
#endif

  if (DEBUG_SERIAL_ONLY) {
    // press each sensing element and confirm only its reading jumps
    // (Master Guide Section 2.1 step 8)
    static unsigned long lastPrint = 0;
    if (millis() - lastPrint > 500) {
      lastPrint = millis();
#if MAT_ZONE_MODE
      printZones(zones);
#else
      printMatGrid();
#endif
    }
    delay(50);
    return;   // skip WiFi entirely until Step 1 passes
  }

#if MAT_ZONE_MODE
  // {"sensor_type":"pressure_4zone","zones":[z1,z2,z3,z4]} — exactly the
  // backend's Pressure4ZonePayload (routed to sensor/mat/zones, no 72-cell
  // check). Order is canonical: Z1 TL, Z2 TR, Z3 BL, Z4 BR.
  String payload = "{\"sensor_type\":\"pressure_4zone\",\"zones\":[";
  char buf[8];
  for (int i = 0; i < MAT_ZONE_COUNT; i++) {
    itoa(zones[i], buf, 10);
    payload += buf;
    if (i != MAT_ZONE_COUNT - 1) payload += ",";
  }
  payload += "]}";
#else
  // one flat 72-int array, row-major — exactly the schema's {"values":[...]}
  String payload = "{\"values\":[";
  char buf[8];
  for (int row = 0; row < 12; row++) {
    for (int col = 0; col < 6; col++) {
      int v = constrain((int)(((frame[row][col] - CALIB_OFFSET)) * CALIB_GAIN),
                        0, 4095);
      itoa(v, buf, 10);
      payload += buf;
      if (!(row == 11 && col == 5)) payload += ",";
    }
  }
  payload += "]}";
#endif
  int code = 0;
  bool ok = node.telemetry(payload.c_str(), &code);
  Serial.printf("[mat] frame -> %s (code=%d)\n", ok ? "ok" : "failed", code);

  bool pir = digitalRead(PIR_PIN);
  if (pir != lastPirState) {
    lastPirState = pir;
    node.event(pir ? "{\"entry\": true}" : "{\"entry\": false}");
  }

  // keep the readings visible on the Serial Monitor while streaming too
  static unsigned long lastGrid = 0;
  if (millis() - lastGrid > 1000) {
    lastGrid = millis();
#if MAT_ZONE_MODE
    printZones(zones);
#else
    printMatGrid();
#endif
  }

  delay(100);   // ~10 Hz frame rate
}
