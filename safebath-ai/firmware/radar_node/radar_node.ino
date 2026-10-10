/*
 * SafeBath AI — Board 2: Presence/Motion Radar (DFRobot C4001)  [v3 self-contained]
 * ESP32 + DFRobot C4001 UART + HTTP
 *
 * v3 changes (why): The DFRobot library's sensorStop()/config path contains a
 * silent infinite `while(1)` that never returns unless the sensor literally
 * echoes back the text "sensorStop". Both v1 and v2 froze there. v3 drops the
 * library's command layer entirely and speaks the plain-ASCII protocol
 * directly ("sensorStop", "sensorStart", "setRunApp 1", "saveConfig") with a
 * millisecond bound on EVERY operation:
 *   - Handshake probes 115200 then 9600, dumping raw replies so a wiring or
 *     baud problem is visible immediately instead of hanging silently.
 *   - Report frames ($DFDMD speed-mode / $DFHPD presence-mode) are parsed
 *     with the exact same field mapping the official library uses.
 *   - If the sensor never answers, this node keeps publishing safe defaults
 *     over HTTP forever and retries the handshake periodically.
 *
 * Wiring:
 *   C4001 TX  -> ESP32 GPIO16 (RX2)
 *   C4001 RX  -> ESP32 GPIO17 (TX2)
 *   C4001 GND -> ESP32 GND
 *   C4001 VIN -> 5 V supply appropriate for the module
 *
 * HTTP endpoint : POST /api/v1/telemetry
 * Payload       : {"presence":true,"motion":false,"stillness_ms":1234,
 *                  "possible_collapse":false}
 */

#include <WiFi.h>
#include "../common/SafeBathNode.h"

// ============================================================
// WiFi / HTTP
// ============================================================

const char* device_id   = "radar-01";       // must match SAFEBATH_DEVICES
const char* ssid        = "YOUR_ROUTER_SSID";
const char* password    = "YOUR_ROUTER_PASSWORD";
const char* server_host = "192.168.1.50";   // RPi IP running the edge API
const uint16_t server_port = 8000;
const char* node_token  = "";               // optional; matches device token

safebath::Node node(device_id, "radar", "1.0.0");

// true = Serial output only, no HTTP
#define DEBUG_SERIAL_ONLY false

// ============================================================
// C4001 UART + protocol
// ============================================================

#define RADAR_RX 16
#define RADAR_TX 17

static const uint32_t BAUD_CANDIDATES[] = { 115200, 9600 };

HardwareSerial radarSerial(2);

// Plain-ASCII protocol words (same bytes the DFRobot lib sends)
const char* CMD_STOP    = "sensorStop";
const char* CMD_START   = "sensorStart";
const char* CMD_SPEED   = "setRunApp 1";   // speed/range mode
const char* CMD_SAVE    = "saveConfig";

// ============================================================
// SafeBath AI collapse rule
// ============================================================

const uint32_t STILLNESS_THRESHOLD_MS = 120000;

const uint32_t SENSOR_POLL_MS      = 250;    // parse cadence
const uint32_t HANDSHAKE_RETRY_MS  = 15000;  // re-probe offline sensor
const uint32_t HANDSHAKE_REPLY_MS  = 800;    // wait for sensorStop ack

// ============================================================
// Shared state (written by sensor task, read by publisher loop)
// ============================================================

volatile bool     g_sensorReady = false;
volatile uint32_t g_lastGoodMs  = 0;
volatile bool     g_presence    = false;
volatile bool     g_motion      = false;
volatile uint32_t g_stillnessMs = 0;
volatile bool     g_collapse    = false;

TaskHandle_t sensorTaskHandle = nullptr;

// ============================================================
// WiFi
// ============================================================

// WiFi bring-up is handled by safebath::Node::begin() in setup().

// Publishing is handled by safebath::Node (HTTP) in loop().

// ============================================================
// Raw C4001 helpers — every operation time-bounded
// ============================================================

static void c4001FlushInput() {
  while (radarSerial.available()) {
    radarSerial.read();
  }
}

static void c4001Send(const char* cmd) {
  radarSerial.write(cmd, strlen(cmd));
}

// Collect whatever arrives within `window_ms`; returns byte count.
static int c4001Collect(uint8_t* buf, int cap, uint32_t window_ms) {
  uint32_t t0 = millis();
  int i = 0;
  while (millis() - t0 < window_ms && i < cap) {
    while (radarSerial.available() && i < cap) {
      buf[i++] = radarSerial.read();
    }
  }
  return i;
}

static void dumpBytes(const uint8_t* buf, int n) {
  int shown = (n < 96) ? n : 96;
  Serial.print("[sensor] raw (");
  Serial.print(n);
  Serial.print(" bytes): ");
  for (int i = 0; i < shown; i++) {
    if (buf[i] >= 32 && buf[i] < 127) {
      Serial.write(buf[i]);
    } else {
      Serial.printf("<%02X>", buf[i]);
    }
  }
  Serial.println();
}

// One handshake attempt at a given baud.
// Returns true if the sensor acknowledged "sensorStop".
static bool c4001HandshakeAt(uint32_t baud) {

  Serial.printf("[sensor] probing @ %lu baud...\n", (unsigned long)baud);

  radarSerial.updateBaudRate(baud);
  c4001FlushInput();

  c4001Send(CMD_STOP);

  static uint8_t buf[256];
  int n = c4001Collect(buf, sizeof(buf) - 1, HANDSHAKE_REPLY_MS);
  buf[n] = 0;

  if (n <= 0) {
    Serial.println("[sensor] no bytes received");
    return false;
  }

  dumpBytes(buf, n);

  if (strstr((const char*)buf, CMD_STOP) != NULL ||
      strstr((const char*)buf, "Ok") != NULL) {
    return true;
  }

  Serial.println("[sensor] bytes arrived but no valid ack (baud/wiring?)");
  return false;
}

// Feed one complete report line into shared state.
static void c4001ParseLine(char* line, uint32_t nowMs,
                           uint32_t& stillnessStart) {

  if (strncmp(line, "$DFDMD,", 7) == 0) {

    // $DFDMD,<number>,<p2>,<range_m>,<speed_mps>,<energy>,...
    char* parts[10] = { 0 };
    int idx = 0;
    char* tok = strtok(line, ",");
    while (tok != NULL && idx < 10) {
      parts[idx++] = tok;
      tok = strtok(NULL, ",");
    }
    if (idx < 6) {
      return;
    }

    int   number   = atoi(parts[1]);
    float rangeM   = atof(parts[3]);
    float speedMps = atof(parts[4]);

    bool presence = (number > 0);
    bool motion   = (presence && fabs(speedMps) > 0.05f);

    if (motion || !presence) {
      stillnessStart = nowMs;
    }
    uint32_t stillnessMs =
      (presence && !motion) ? (nowMs - stillnessStart) : 0;

    g_presence    = presence;
    g_motion      = motion;
    g_stillnessMs = stillnessMs;
    g_collapse    = (presence && stillnessMs > STILLNESS_THRESHOLD_MS);
    g_lastGoodMs  = nowMs;
    g_sensorReady = true;

    Serial.printf("[sensor] presence=%d motion=%d speed=%.2f range=%.2f "
                  "stillness_ms=%lu collapse=%d\n",
                  presence, motion, speedMps, rangeM,
                  (unsigned long)stillnessMs,
                  (int)(presence && stillnessMs > STILLNESS_THRESHOLD_MS));

  } else if (strncmp(line, "$DFHPD,", 7) == 0) {

    // Presence mode: $DFHPD,<0|1>...
    bool presence = (line[7] == '1');

    if (!presence) {
      stillnessStart = nowMs;
    }
    uint32_t stillnessMs =
      presence ? (nowMs - stillnessStart) : 0;

    g_presence    = presence;
    g_motion      = false;   // exit-mode reports carry no speed field
    g_stillnessMs = stillnessMs;
    g_collapse    = (presence && stillnessMs > STILLNESS_THRESHOLD_MS);
    g_lastGoodMs  = nowMs;
    g_sensorReady = true;

    Serial.printf("[sensor] presence=%d (exit mode) stillness_ms=%lu "
                  "collapse=%d\n",
                  presence, (unsigned long)stillnessMs,
                  (int)(presence && stillnessMs > STILLNESS_THRESHOLD_MS));
  }
  // anything else: ignore silently (acks, config echoes)
}

// ============================================================
// Sensor task — owns ALL C4001 interaction (core 0)
// ============================================================

void sensorTask(void *pv) {

  Serial.println("[sensor] starting radarSerial @115200...");
  radarSerial.begin(115200, SERIAL_8N1, RADAR_RX, RADAR_TX);

  // ---- Handshake: probe supported bauds until the sensor answers ----
  bool alive = false;
  uint32_t workingBaud = BAUD_CANDIDATES[0];

  for (;;) {

    for (uint8_t b = 0; b < sizeof(BAUD_CANDIDATES) / sizeof(uint32_t); b++) {
      if (c4001HandshakeAt(BAUD_CANDIDATES[b])) {
        alive = true;
        workingBaud = BAUD_CANDIDATES[b];
        break;
      }
    }

    if (alive) {
      break;
    }

    Serial.println("[sensor] NO RESPONSE — check wiring:");
    Serial.println("         C4001 TX -> ESP32 GPIO16 (RX2)");
    Serial.println("         C4001 RX -> ESP32 GPIO17 (TX2)");
    Serial.println("         common GND, stable 5 V supply");
    Serial.printf("[sensor] retrying handshake in %lu s\n",
                  (unsigned long)(HANDSHAKE_RETRY_MS / 1000));
    delay(HANDSHAKE_RETRY_MS);
  }

  Serial.printf("[sensor] C4001 alive @ %lu baud\n",
                (unsigned long)workingBaud);

  // ---- Switch to speed/range mode (pure writes, no waiting on acks) ----
  c4001FlushInput();
  c4001Send(CMD_SPEED);
  delay(150);
  c4001FlushInput();

  c4001Send(CMD_SAVE);
  delay(500);
  c4001FlushInput();

  c4001Send(CMD_START);
  delay(150);
  c4001FlushInput();
  Serial.println("[sensor] speed-mode request sent; entering poll loop");

  // ---- Poll loop: bounded collects + line parsing ----
  static uint8_t rbuf[512];
  static char    line[160];
  int            lineLen = 0;
  uint32_t       stillnessStart = millis();
  uint32_t       lastBytesLog   = millis();
  uint32_t       rxTotal        = 0;

  for (;;) {

    int n = c4001Collect(rbuf, sizeof(rbuf), SENSOR_POLL_MS);
    rxTotal += n;

    for (int i = 0; i < n; i++) {

      char ch = (char)rbuf[i];

      if (ch == '\n' || ch == '\r') {
        if (lineLen > 0) {
          line[lineLen] = 0;
          c4001ParseLine(line, millis(), stillnessStart);
          lineLen = 0;
        }
      } else if (lineLen < (int)sizeof(line) - 1) {
        line[lineLen++] = ch;
      } else {
        lineLen = 0;   // overrun guard: drop absurdly long line
      }
    }

    // If bytes flow but nothing parses, say so once in a while
    if (millis() - lastBytesLog > 5000) {
      if (!g_sensorReady && rxTotal > 0) {
        Serial.printf("[sensor] %lu bytes received, none parsed yet "
                      "(wrong baud?)\n", (unsigned long)rxTotal);
      }
      lastBytesLog = millis();
      rxTotal = 0;
    }
  }
}

// ============================================================
// SETUP
// ============================================================

void setup() {

  Serial.begin(115200);
  delay(500);

  Serial.println();
  Serial.println("=================================");
  Serial.println("SafeBath AI - Radar Node 2 (v3)");
  Serial.println("DFRobot C4001");
  Serial.println("=================================");

  if (!DEBUG_SERIAL_ONLY) {

    // Network FIRST — never hostage to the sensor
    node.begin(ssid, password, server_host, server_port, node_token);
    Serial.println("[main] network stack ready");
  }

  BaseType_t ok = xTaskCreatePinnedToCore(
    sensorTask,
    "c4001",
    16384,
    nullptr,
    1,
    &sensorTaskHandle,
    0
  );

  Serial.printf("[main] sensor task %s\n",
                ok == pdPASS ? "started" : "FAILED TO START");
  Serial.println("[main] radar node ready.");
}

// ============================================================
// LOOP — HTTP publishing only (core 1)
// ============================================================

void publishRadar(bool presence, bool motion,
                  uint32_t stillnessMs, bool collapse) {

  String payload =
    String("{\"presence\":")  + (presence  ? "true" : "false") +
    ",\"motion\":"            + (motion    ? "true" : "false") +
    ",\"stillness_ms\":"      + String((unsigned long)stillnessMs) +
    ",\"possible_collapse\":" + (collapse  ? "true" : "false") +
    "}";

  int code = 0;
  bool ok = node.telemetry(payload.c_str(), &code);
  Serial.printf("HTTP %d -> %s\n", code, payload.c_str());
  (void)ok;
}

void loop() {

  static unsigned long lastPublish = 0;

  if (millis() - lastPublish < 500) {
    return;
  }
  lastPublish = millis();

  uint32_t age = millis() - g_lastGoodMs;

  if (!g_sensorReady || age > 3000) {
    // Sensor missing/hung/stale -> safe defaults so downstream logic
    // treats the room as unoccupied rather than trusting old data.
    Serial.printf("[main] sensor %s (age=%lus) — publishing safe defaults\n",
                  g_sensorReady ? "STALE" : "not ready",
                  (unsigned long)(age / 1000));
    publishRadar(false, false, 0, false);
    return;
  }

  publishRadar(g_presence, g_motion, g_stillnessMs, g_collapse);
}
