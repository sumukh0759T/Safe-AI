/*
 * SafeBath AI — Board 1: Pressure Mat (Fall Risk) + Entry Detection
 * Phase A standalone node.
 *
 * Wiring = SafeBath_AI_Final_Master_Guide.md Section 1 (Board 1):
 *   CD4067  S0/S1/S2/S3 -> GPIO16/17/18/19      SIG -> GPIO34 (ADC1)
 *   74HC595 DS/SH_CP/ST_CP -> GPIO23/25/26       Q1-Q6 -> mat columns
 *   PIR     OUT -> GPIO27
 * POSTs the Section 3 contract to the RPi over HTTP (no MQTT):
 *   POST /api/v1/telemetry  payload {"values":[72 ints, row-major]}  @10 Hz
 *   POST /api/v1/event      payload {"entry": true|false}            on change
 *
 * Grid: 12 rows x 6 cols = 72 cells. CD4067 channel N (0-11) selects row N;
 * 74HC595 shifts a single HIGH bit to light column C. Because the bits are
 * shifted MSB-first, column C appears on 74HC595 output Q(5-C) for the 6-bit
 * shift — verify that physical wiring maps Q1-Q6 to columns in that order.
 * (See scanMat()/driveColumn() below.)
 *
 * FOUR-ZONE MODE (MAT_ZONE_MODE=1): the prototype may switch to four large
 * Velostat zones (2x2). In that mode each zone's electrode pair is read on its
 * own CD4067 channel and the 72-cell scanner/shift register is not used. The
 * payload becomes {"sensor_type":"pressure_4zone","zones":[z1,z2,z3,z4]},
 * matching backend/schemas.py Pressure4ZonePayload. The legacy 12x6 scanner is
 * preserved untouched behind MAT_ZONE_MODE=0 (default).
 * !! The four-zone hardware circuit is NOT yet validated — confirm the channel
 * !! mapping and that a single read per zone tracks pressure before trusting it.
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
// 0 = legacy 12x6 Velostat grid (72 cells) — default, unchanged
// 1 = four-zone mat (2x2) — emits {"sensor_type":"pressure_4zone","zones":[...]}
#define MAT_ZONE_MODE 0

#define MAT_ZONE_COUNT 4
// PLACEHOLDER CD4067 channel mapping for zones 1..4. This MUST be changed to
// match the physical four-zone build before flashing — it is not validated.
static const int MAT_ZONE_CHANNELS[MAT_ZONE_COUNT] = {0, 1, 2, 3};

// --- mux / shift register / PIR (final confirmed pins) ---
#define MUX_S0 16
#define MUX_S1 17
#define MUX_S2 18
#define MUX_S3 19
#define MUX_SIG 34

#define SR_DS 23
#define SR_SHCP 25
#define SR_STCP 26

#define PIR_PIN 27

// TODO(calibration): placeholder Velostat normalization constants — replace with
// real ADC ranges from Phase-A bring-up (Master Guide Section 12 step 3). The
// synthetic generator (datasets/gen_mat_data.py CALIB_OFFSET/CALIB_GAIN) must be
// updated with the same values so training matches inference.
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

/* Four-zone scan (MAT_ZONE_MODE=1): one CD4067 channel per zone, no column
 * drive. UNVALIDATED — adjust MAT_ZONE_CHANNELS to the real build and confirm
 * each channel responds to pressure on its zone. */
void scanZones(int out[MAT_ZONE_COUNT]) {
  for (int i = 0; i < MAT_ZONE_COUNT; i++) {
    selectMuxChannel(MAT_ZONE_CHANNELS[i]);
    delayMicroseconds(50);
    out[i] = constrain((int)((analogRead(MUX_SIG) - CALIB_OFFSET) * CALIB_GAIN),
                       0, 4095);
  }
}

void printZones(const int zones[MAT_ZONE_COUNT]) {
  Serial.print("--- mat 4-zone");
  for (int i = 0; i < MAT_ZONE_COUNT; i++) {
    Serial.printf("  Z%d=%d", i + 1, zones[i]);
  }
  Serial.printf("  pir=%d\n", digitalRead(PIR_PIN));
}

/* Prints the full 12x6 frame to the Serial Monitor (115200 baud) so every cell
 * value can be checked on the bench without the dashboard/backend.
 * Layout: row-major, R0..R11 = CD4067 channel = physical row; C0..C5 = drive
 * order, i.e. 74HC595 output Q(5-C) — see driveColumn(). Pressing one of the
 * 72 intersections should make only its cell jump (Master Guide 2.1 step 8). */
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

/* -------------------- Setup -------------------- */
void setup() {
  Serial.begin(115200);
  Serial.println();
  Serial.println("=================================");
  Serial.println("SafeBath AI - Mat Node 1 (v3 HTTP)");
  Serial.println("=================================");

  pinMode(MUX_S0, OUTPUT); pinMode(MUX_S1, OUTPUT);
  pinMode(MUX_S2, OUTPUT); pinMode(MUX_S3, OUTPUT);
  pinMode(SR_DS, OUTPUT); pinMode(SR_SHCP, OUTPUT); pinMode(SR_STCP, OUTPUT);
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
  // backend's Pressure4ZonePayload (routed to sensor/mat/zones, no 72-cell check)
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
