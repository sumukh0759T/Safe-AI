/*
 * SafeBath AI — Board 1: Pressure Mat (Fall Risk) + Entry Detection
 * Phase A standalone node.
 *
 * Wiring = SafeBath_AI_Final_Master_Guide.md Section 1 (Board 1):
 *   CD4067  S0/S1/S2/S3 -> GPIO16/17/18/19      SIG -> GPIO34 (ADC1)
 *   74HC595 DS/SH_CP/ST_CP -> GPIO23/25/26       Q1-Q4 -> mat columns
 *   PIR     OUT -> GPIO27
 * POSTs the Section 3 contract to the RPi over HTTP (no MQTT):
 *   POST /api/v1/telemetry  payload {"values":[64 ints, row-major]}  @10 Hz
 *   POST /api/v1/event      payload {"entry": true|false}            on change
 *
 * Libraries: none beyond the ESP32 core (HTTPClient + WiFi).
 */
#include "../common/SafeBathNode.h"

const char* ssid        = "YOUR_ROUTER_SSID";
const char* password    = "YOUR_ROUTER_PASSWORD";
const char* server_host = "192.168.1.50";   // RPi IP (edge server)
const uint16_t server_port = 8000;
const char* node_token  = "";               // must match SAFEBATH_DEVICES token (if any)

safebath::Node node("mat-01", "mat", "1.0.0");

// Network bring-up Step 1 (Network & Autonomous Build Guide Section 1):
// keep true until the Serial-only sanity check passes — firmware reads the
// sensors and prints instead of touching the network entirely.
#define DEBUG_SERIAL_ONLY false

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
  scanMat();

  if (DEBUG_SERIAL_ONLY) {
    // press each of the 64 intersections one at a time and confirm only that
    // cell's reading jumps (Master Guide Section 2.1 step 8)
    static unsigned long lastPrint = 0;
    if (millis() - lastPrint > 500) {
      lastPrint = millis();
      int mn = 4095, mx = 0, sum = 0, hotRow = -1, hotCol = -1;
      for (int r = 0; r < 16; r++)
        for (int c = 0; c < 4; c++) {
          int v = frame[r][c];
          sum += v;
          if (v < mn) mn = v;
          if (v > mx) { mx = v; hotRow = r; hotCol = c; }
        }
      Serial.printf("mat min=%d max=%d sum=%d peak@r%d,c%d pir=%d\n",
                    mn, mx, sum, hotRow, hotCol, digitalRead(PIR_PIN));
    }
    delay(50);
    return;   // skip WiFi entirely until Step 1 passes
  }

  // one flat 64-int array, row-major — exactly the schema's {"values":[...]}
  String payload = "{\"values\":[";
  char buf[8];
  for (int row = 0; row < 16; row++) {
    for (int col = 0; col < 4; col++) {
      int v = constrain((int)(((frame[row][col] - CALIB_OFFSET)) * CALIB_GAIN),
                        0, 4095);
      itoa(v, buf, 10);
      payload += buf;
      if (!(row == 15 && col == 3)) payload += ",";
    }
  }
  payload += "]}";
  int code = 0;
  bool ok = node.telemetry(payload.c_str(), &code);
  Serial.printf("[mat] frame -> %s (code=%d)\n", ok ? "ok" : "failed", code);

  bool pir = digitalRead(PIR_PIN);
  if (pir != lastPirState) {
    lastPirState = pir;
    node.event(pir ? "{\"entry\": true}" : "{\"entry\": false}");
  }

  delay(100);   // ~10 Hz frame rate
}
