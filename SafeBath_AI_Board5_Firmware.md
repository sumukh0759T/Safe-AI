# SafeBath AI — Board 5 Firmware (ToF + Thermal + Voice Bridge)
### Complete `.ino` for the final confirmed pins — VL53L1X ×3, AMG8833, relay, flow sensor, DFPlayer + amplifier

Matches `SafeBath_AI_Final_Master_Guide.md` Section 1 (Board 5) exactly:

| Component | Pin | GPIO |
|---|---|---|
| I2C bus (ToF ×3 + AMG8833) | SDA / SCL | 21 / 22 |
| VL53L1X #1 / #2 / #3 | XSHUT | 25 / 26 / 27 |
| Relay → 12V solenoid | IN | 33 |
| Water flow sensor | Signal | 34 |
| DFPlayer Mini | RX / TX | 17 (TX2) / 16 (RX2) |
| Amplifier | audio in | from DFPlayer SPK_R |

This is **Phase E** of the module-by-module `opencode` build order — verify this board completely standalone (ToF readings, relay firing correctly on the bucket rig, DFPlayer audio through the amplifier) before it ever talks to `fusion_engine.py`.

---

## Required Libraries

Install via Arduino Library Manager: `PubSubClient`, `Adafruit_VL53L1X`, `Adafruit_AMG88xx`, `DFRobotDFPlayerMini`.

---

## `firmware/tof_thermal_voice_node/tof_thermal_voice_node.ino`

```cpp
#include <WiFi.h>
#include <PubSubClient.h>
#include <Wire.h>
#include <Adafruit_VL53L1X.h>
#include <Adafruit_AMG88xx.h>
#include <DFRobotDFPlayerMini.h>
#include <HardwareSerial.h>

const char* ssid = "SafeBath_Demo";
const char* password = "yourpassword";
const char* mqtt_server = "192.168.4.1";   // laptop's local IP

WiFiClient espClient;
PubSubClient client(espClient);

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
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) delay(300);
}

void reconnectMQTT() {
  while (!client.connected()) {
    client.connect("TofThermalVoiceNode5");
    delay(500);
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
void initToF() {
  pinMode(XSHUT_1, OUTPUT); pinMode(XSHUT_2, OUTPUT); pinMode(XSHUT_3, OUTPUT);
  digitalWrite(XSHUT_1, LOW); digitalWrite(XSHUT_2, LOW); digitalWrite(XSHUT_3, LOW);
  delay(10);

  digitalWrite(XSHUT_1, HIGH); delay(10);
  if (tof1.begin(0x29, &Wire)) { tof1.setAddress(0x30); tof1.startRanging(); }

  digitalWrite(XSHUT_2, HIGH); delay(10);
  if (tof2.begin(0x29, &Wire)) { tof2.setAddress(0x31); tof2.startRanging(); }

  digitalWrite(XSHUT_3, HIGH); delay(10);
  if (tof3.begin(0x29, &Wire)) { tof3.setAddress(0x32); tof3.startRanging(); }

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
  Wire.begin(I2C_SDA, I2C_SCL);

  initToF();
  amg.begin();   // AMG8833, address 0x69, shares the same I2C bus — no conflict with ToF

  pinMode(FLOW_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(FLOW_PIN), flowISR, RISING);

  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, HIGH);   // energized = valve OPEN by default (NC solenoid — confirmed working)

  dfSerial.begin(9600, SERIAL_8N1, DFPLAYER_RX, DFPLAYER_TX);
  dfPlayer.begin(dfSerial);
  dfPlayer.volume(22);   // 0-30, tune against the amplifier's gain

  setup_wifi();
  client.setServer(mqtt_server, 1883);
  client.setCallback(onMqttMessage);
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
```

---

## Standalone Verification Checklist (Phase E)

- [ ] Serial Monitor confirms all 3 ToF sensors report distinct addresses (`0x30`/`0x31`/`0x32`) at boot with no I2C address collision errors.
- [ ] Wave a hand within 30cm of all 3 sensors simultaneously — confirm `tof_collapse` flips `true` in the published MQTT payload (`mosquitto_sub -t sensor/tof_thermal/status`).
- [ ] Confirm AMG8833's `avg_thermal_c` responds to a warm object (hand, mug of warm water) moved near it.
- [ ] With the bucket-and-tube rig only (never real plumbing), simulate `flow_pulses > 50` and `avg_thermal_c > 38` together — confirm the relay audibly clicks and `unattended_hot_fill` publishes `true`.
- [ ] Publish a manual test message to `voice/trigger` and confirm the DFPlayer plays through the amplifier and speaker at an audible, clear volume.
- [ ] Confirm relay returns to energized/open state after a fresh boot (default-safe behavior) — don't leave it in the de-energized test state.
