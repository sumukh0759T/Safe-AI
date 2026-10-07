/*
 * SafeBath AI — Board 5 Firmware (ToF + Thermal + Voice Bridge)
 * Phase E standalone node. Verbatim from SafeBath_AI_Board5_Firmware.md.
 *
 * Matches SafeBath_AI_Final_Master_Guide.md Section 1 (Board 5) exactly:
 *   I2C bus SDA/SCL -> 21/22
 *   VL53L1X #1/#2/#3 XSHUT -> 25/26/27
 *   Relay -> solenoid IN -> GPIO33
 *   Water flow sensor signal -> GPIO34
 *   DFPlayer RX/TX -> 17 (TX2) / 16 (RX2); amplifier from SPK_R
 *
 * Publishes the Section 3 contract ~3 Hz on sensor/tof_thermal/status and
 * handles the voice/trigger -> DFPlayer prompt flow.
 *
 * Libraries: PubSubClient, Adafruit_VL53L1X, Adafruit_AMG88xx,
 *            DFRobotDFPlayerMini
 */
#include <WiFi.h>
#include <PubSubClient.h>
#include <Wire.h>
#include <Adafruit_VL53L1X.h>
#include <Adafruit_AMG88xx.h>
#include <DFRobotDFPlayerMini.h>
#include <HardwareSerial.h>

const char* ssid = "YOUR_ROUTER_SSID";
const char* password = "YOUR_ROUTER_PASSWORD";
const char* mqtt_server = "192.168.1.20";   // laptop IP from step 0

WiFiClient espClient;
PubSubClient client(espClient);

// Network bring-up Step 1 (Network & Autonomous Build Guide Section 1):
// true = print readings to Serial only, no WiFi/MQTT.
#define DEBUG_SERIAL_ONLY false

// --- I2C bus: shared by all 3 VL53L1X + AMG8833 ---
#define I2C_SDA 21
#define I2C_SCL 22

// --- ToF XSHUT pins (final confirmed) ---
#define XSHUT_1 25
#define XSHUT_2 26
#define XSHUT_3 27

// --- Relay / solenoid ---
#define RELAY_PIN 33

// --- Water flow sensor ---
#define FLOW_PIN 34

// --- DFPlayer Mini on UART2 ---
#define DFPLAYER_RX 16   // ESP32 RX2 <- DFPlayer TX
#define DFPLAYER_TX 17   // ESP32 TX2 -> DFPlayer RX
HardwareSerial dfSerial(2);
DFRobotDFPlayerMini dfPlayer;

Adafruit_VL53L1X tof1 = Adafruit_VL53L1X();
Adafruit_VL53L1X tof2 = Adafruit_VL53L1X();
Adafruit_VL53L1X tof3 = Adafruit_VL53L1X();
Adafruit_AMG88xx amg;

volatile unsigned long flowPulses = 0;
void IRAM_ATTR flowISR() { flowPulses++; }

bool TOF_READY = false;

/* -------------------- WiFi / MQTT -------------------- */
void setup_wifi() {
  Serial.println();
  Serial.print("Connecting to WiFi: ");
  Serial.println(ssid);
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) { delay(300); Serial.print("."); }
  Serial.println();
  Serial.println("WiFi connected!");
  Serial.print("ESP32 IP: ");
  Serial.println(WiFi.localIP());
}

void reconnectMQTT() {
  while (!client.connected()) {
    Serial.print("Connecting to MQTT...");
    if (client.connect("TofThermalVoiceNode5")) {
      Serial.println("connected");
    } else {
      Serial.print("failed, rc=");
      Serial.print(client.state());
      Serial.println(" retrying...");
      delay(500);
    }
  }
  client.subscribe("voice/trigger");   // Section 8 — Voice Bridge cross-board flow
}

void onMqttMessage(char* topic, byte* payload, unsigned int length) {
  String msg;
  for (unsigned int i = 0; i < length; i++) msg += (char)payload[i];
  if (String(topic) == "voice/trigger") {
    playVoicePrompt();
  }
}

/* -------------------- ToF boot sequence -------------------- */
// All 3 sensors ship with the same default address (0x29). Bring them
// up one at a time via XSHUT, assign a unique address, then release
// the next — required exactly once at boot.
//
// NOTE: Adafruit_VL53L1X has no setAddress(); the inherited core method
// VL53L1X_SetI2CAddress(0xNN) takes the 7-bit address AND updates the
// object's internal device address, so later dataReady()/distance()
// calls keep working without re-beginning.
#define TOF_ADDR_1 0x30
#define TOF_ADDR_2 0x31
#define TOF_ADDR_3 0x32

// Raw I2C presence probe: ACK at addr means a live sensor is listening.
bool i2cPresent(uint8_t addr7) {
  Wire.beginTransmission(addr7);
  return (Wire.endTransmission() == 0);
}

// Read the VL53L1X model ID (16-bit reg 0x010F) raw over I2C.
// A healthy, booted sensor ALWAYS returns 0xEACC.
uint16_t tofModelId(uint8_t addr7) {
  Wire.beginTransmission(addr7);
  Wire.write(0x01); Wire.write(0x0F);          // register pointer 0x010F
  // plain write-then-read (STOP between phases) — some clones dislike
  // repeated-start reads
  if (Wire.endTransmission() != 0) return 0xFFFF;
  if (Wire.requestFrom((int)addr7, 2) != 2) return 0xFFFF;
  uint16_t hi = Wire.read();
  uint16_t lo = Wire.read();
  return (hi << 8) | lo;
}

bool initOneToF(int n, int xshutPin, Adafruit_VL53L1X &tof, uint8_t newAddr) {
  digitalWrite(xshutPin, HIGH);
  delay(50);                                    // allow full boot

  uint16_t id = tofModelId(0x29);
  Serial.printf("[tof] #%d model_id=0x%04X ", n, id);

  if (id == 0xFFFF) {
    Serial.println("-> NO ANSWER on bus (check XSHUT + VIN + SDA21/SCL22)");
    return false;
  }
  if (id != 0xEACC && id != 0xEEAC) {
    // clone chips answer coherently with their own id — still try init
    Serial.print("-> clone/alt chip, attempting init anyway... ");
  }

  for (int attempt = 1; attempt <= 3; attempt++) {
    if (tof.begin(0x29, &Wire)) {
      tof.VL53L1X_SetI2CAddress(newAddr); delay(5);
      if (tof.startRanging()) {
        Serial.printf("-> 0x%02X OK\n", newAddr);
        return true;
      }
      Serial.printf("-> ranging failed (attempt %d)", attempt);
    } else {
      Serial.printf("-> init failed (attempt %d)", attempt);
    }
    delay(30);
  }
  Serial.println("-> GAVE UP");
  return false;
}

void initToF() {
  pinMode(XSHUT_1, OUTPUT); pinMode(XSHUT_2, OUTPUT); pinMode(XSHUT_3, OUTPUT);
  digitalWrite(XSHUT_1, LOW); digitalWrite(XSHUT_2, LOW); digitalWrite(XSHUT_3, LOW);
  delay(10);

  int ok = 0;
  if (initOneToF(1, XSHUT_1, tof1, TOF_ADDR_1)) ok++;
  if (initOneToF(2, XSHUT_2, tof2, TOF_ADDR_2)) ok++;
  if (initOneToF(3, XSHUT_3, tof3, TOF_ADDR_3)) ok++;

  Serial.printf("[tof] %d/3 sensors online\n", ok);
  TOF_READY = true;
}

// Module 5 rule (Final Master Guide Section 5): all 3 sensors simultaneously
// report a sudden drop toward floor-level distance and hold there.
bool checkToFCollapse() {
  if (!TOF_READY) return false;
  int d1 = -1, d2 = -1, d3 = -1;
  if (tof1.dataReady()) { d1 = tof1.distance(); tof1.clearInterrupt(); }
  if (tof2.dataReady()) { d2 = tof2.distance(); tof2.clearInterrupt(); }
  if (tof3.dataReady()) { d3 = tof3.distance(); tof3.clearInterrupt(); }
  return (d1 > 0 && d1 < 300) && (d2 > 0 && d2 < 300) && (d3 > 0 && d3 < 300);   // mm, tune on-site
}

/* -------------------- Voice Bridge output -------------------- */
void playVoicePrompt() {
  // Track 1 on the DFPlayer's SD card should be a recorded/synthesized
  // "Are you alright? Say yes, or say help." clip, played through the
  // amplifier + speaker. Board 4 (MAX4466) is listening independently
  // and will publish voice/reply within ~8s (see Board 4 firmware).
  dfPlayer.play(1);
}

/* -------------------- Setup -------------------- */
void setup() {
  Serial.begin(115200);
  delay(500);

  Serial.println();
  Serial.println("=================================");
  Serial.println("SafeBath AI - ToF/Thermal/Voice Node 5");
  Serial.println("=================================");

  Wire.begin(I2C_SDA, I2C_SCL);

  initToF();
  if (amg.begin()) {
    Serial.println("[amg] AMG8833 OK");
  } else {
    Serial.println("[amg] AMG8833 NOT RESPONDING (addr 0x69)");
  }

  // GPIO34 is input-only (no internal pull-up exists — the boot warning
  // about gpio_pullup_en is harmless). Flow-sensor modules usually have an
  // onboard pull-up; add an external 10k to 3.3V if pulses never register.
  pinMode(FLOW_PIN, INPUT);
  attachInterrupt(digitalPinToInterrupt(FLOW_PIN), flowISR, RISING);

  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, HIGH);   // energized = valve OPEN by default (NC solenoid — confirmed working)

  dfSerial.begin(9600, SERIAL_8N1, DFPLAYER_RX, DFPLAYER_TX);
  if (dfPlayer.begin(dfSerial)) {
    Serial.println("[dfp] DFPlayer Mini OK");
  } else {
    Serial.println("[dfp] DFPlayer NOT RESPONDING (check RX16/TX17 + SD card)");
  }
  dfPlayer.volume(22);   // 0-30, tune against the amplifier's gain

  setup_wifi();
  client.setServer(mqtt_server, 1883);
  client.setCallback(onMqttMessage);
  Serial.println("[main] node ready.");
}

/* -------------------- Loop -------------------- */
void loop() {
  if (!client.connected()) reconnectMQTT();
  client.loop();

  float thermalFrame[64];
  amg.readPixels(thermalFrame);
  float avgTemp = 0;
  for (int i = 0; i < 64; i++) avgTemp += thermalFrame[i];
  avgTemp /= 64.0;

  bool collapseFlag = checkToFCollapse();
  unsigned long flowCount = flowPulses;
  flowPulses = 0;

  // Section 7 danger rule: unattended hot fill is checked here, but the
  // actual cross-check against Board 1's mat state happens in
  // backend/cross_modal.py (scald_shutoff_confirmed) — this firmware-only
  // version is the safe local fallback if MQTT/backend is briefly down.
  bool unattendedHotFill = (flowCount > 50 && avgTemp > 38.0);

  if (DEBUG_SERIAL_ONLY) {
    // wave a hand near all 3 ToF / move a warm object past the AMG8833 and
    // confirm readings respond (Phase E verification)
    int d1 = -1, d2 = -1, d3 = -1;
    if (TOF_READY) {
      if (tof1.dataReady()) { d1 = tof1.distance(); tof1.clearInterrupt(); }
      if (tof2.dataReady()) { d2 = tof2.distance(); tof2.clearInterrupt(); }
      if (tof3.dataReady()) { d3 = tof3.distance(); tof3.clearInterrupt(); }
    }
    Serial.printf("board5 tof=%d,%d,%dmm avgTemp=%.1fC flow=%lu "
                  "collapse=%d hotfill=%d\n",
                  d1, d2, d3, avgTemp, flowCount, collapseFlag, unattendedHotFill);
    delay(300);
    return;   // skip WiFi/MQTT entirely until Step 1 passes
  }

  if (unattendedHotFill) {
    digitalWrite(RELAY_PIN, LOW);   // de-energize -> solenoid closes
  }

  String payload = String("{\"avg_thermal_c\":") + avgTemp +
                    ",\"flow_pulses\":" + flowCount +
                    ",\"tof_collapse\":" + (collapseFlag ? "true" : "false") +
                    ",\"unattended_hot_fill\":" + (unattendedHotFill ? "true" : "false") + "}";

  client.publish("sensor/tof_thermal/status", payload.c_str());

  if (collapseFlag) {
    // High-severity per Section 7 — trigger Voice Bridge directly rather
    // than waiting on the laptop-side fusion engine's next poll cycle.
    client.publish("voice/trigger", "{\"prompt\":\"are_you_ok\"}");
  }

  delay(300);
}
