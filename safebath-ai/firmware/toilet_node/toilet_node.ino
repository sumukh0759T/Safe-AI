/*
 * SafeBath AI — Board 3: Toilet Biomarker Sensing
 * Phase C standalone node.
 *
 * Wiring = Master Guide Section 1 (Board 3), FINAL list — no DS18B20:
 *   TCS3200 S0/S1/S2/S3 -> GPIO18/19/21/22, OUT -> GPIO23
 *   MQ-135 A0 -> GPIO35 (ADC1), MQ-3 A0 -> GPIO34 (ADC1)
 *
 * Publishes the Section 3 contract every 2 s — note NO temp_c:
 *   sensor/toilet/reading :
 *   {"gas_ppm": float, "voc_ppm": float, "turbidity": float,
 *    "hour_of_day": int}
 *
 * Libraries: PubSubClient
 */
#include <WiFi.h>
#include <PubSubClient.h>
#include <time.h>

const char* ssid = "YOUR_ROUTER_SSID";
const char* password = "YOUR_ROUTER_PASSWORD";
const char* mqtt_server = "192.168.1.20";   // laptop IP from step 0

WiFiClient espClient;
PubSubClient client(espClient);

// Network bring-up Step 1 (Network & Autonomous Build Guide Section 1):
// true = print readings to Serial only, no WiFi/MQTT.
#define DEBUG_SERIAL_ONLY false

// --- pins (final confirmed) ---
#define TCS_S0 18
#define TCS_S1 19
#define TCS_S2 21
#define TCS_S3 22
#define TCS_OUT 23

#define MQ135_PIN 35
#define MQ3_PIN 34

// --- calibration (raw 12-bit ADC counts, 0..4095); recalibrate against
// known solutions after Phase-C bring-up (Master Guide Phase C) ---
const float GAS_ZERO_COUNTS = 1500.0f;   // clean-air MQ-135 baseline
const float VOC_ZERO_COUNTS = 1200.0f;   // clean-air MQ-3 baseline
const float GAS_SCALE = 0.20f;           // counts above baseline -> ppm
const float VOC_SCALE = 0.16f;

unsigned long lastPublish = 0;

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
    if (client.connect("ToiletNode3")) {
      Serial.println("connected");
    } else {
      Serial.print("failed, rc=");
      Serial.print(client.state());
      Serial.println(" retrying...");
      delay(1000);
    }
  }
}

void setup() {
  Serial.begin(115200);
  Serial.println();
  Serial.println("=================================");
  Serial.println("SafeBath AI - Toilet Node 3 (v2)");
  Serial.println("=================================");
  delay(500);

  pinMode(TCS_S0, OUTPUT); pinMode(TCS_S1, OUTPUT);
  pinMode(TCS_S2, OUTPUT); pinMode(TCS_S3, OUTPUT);
  pinMode(TCS_OUT, INPUT);
  Serial.printf("[tcs] OUT line at boot: %s\n",
                digitalRead(TCS_OUT) ? "HIGH" : "LOW");

  // TCS3200: scale output frequency to 20% for stable pulseIn timing
  digitalWrite(TCS_S0, HIGH);
  digitalWrite(TCS_S1, LOW);
  Serial.println("[tcs] TCS3200 pins configured (20% scaling)");

  configTime(0, 0, "pool.ntp.org");    // hour_of_day needs wall-clock time
  setup_wifi();
  client.setServer(mqtt_server, 1883);
  Serial.println("[main] toilet node ready.");
}

/*
 * Turbidity proxy from TCS3200: read red-filtered pulse width, compare to a
 * clear-water reference captured at boot. Murkier sample -> longer pulses ->
 * higher proxy value (0..10 scale). The Random Forest consumes this single
 * scalar exactly like gen_toilet_data.py's `turbidity` column.
 */
const unsigned long CLEAR_REF_US = 1200;   // TODO(calibrate): capture at install
float readTurbidityProxy() {
  digitalWrite(TCS_S2, LOW);               // S2/S3 = LOW/LOW -> red filter
  digitalWrite(TCS_S3, LOW);
  unsigned long pw = pulseIn(TCS_OUT, LOW, 50000);   // low-pulse width, µs
  if (pw == 0) {
    // Forensic: sample the OUT line for ~10 ms to classify the fault
    int hi = 0, lo = 0;
    for (int i = 0; i < 200; i++) {
      digitalRead(TCS_OUT) ? hi++ : lo++;
      delayMicroseconds(50);
    }
    const char* verdict =
      (hi == 200) ? "STUCK HIGH — output disabled (/OE pin?) or OUT wire dead" :
      (lo == 200) ? "STUCK LOW — sensor not oscillating (power/LED check)" :
                    "toggling but pulseIn missed it";
    Serial.printf("[tcs] OUT pulse TIMEOUT (%d hi / %d lo) — %s\n", hi, lo, verdict);
    return 0.0;
  }
  Serial.printf("[tcs] red_pulse=%lu us\n", pw);
  float ratio = (float)pw / (float)CLEAR_REF_US;
  return constrain((ratio - 1.0) * 5.0 + 2.0, 0.0, 10.0);
}

int hourOfDay() {
  time_t now = time(nullptr);
  struct tm* t = localtime(&now);
  return (t && t->tm_hour >= 0 && t->tm_hour <= 23) ? t->tm_hour : 0;
}

void loop() {
  if (!client.connected()) reconnectMQTT();
  client.loop();

  if (millis() - lastPublish < 2000) return;   // contract: every 2 s
  lastPublish = millis();

  int gasRaw = analogRead(MQ135_PIN);
  int vocRaw = analogRead(MQ3_PIN);
  float gasPpm = max(0.0f, (gasRaw - GAS_ZERO_COUNTS)) * GAS_SCALE;
  float vocPpm = max(0.0f, (vocRaw - VOC_ZERO_COUNTS)) * VOC_SCALE;
  float turbidity = readTurbidityProxy();
  Serial.printf("[mq] gasRaw=%d vocRaw=%d\n", gasRaw, vocRaw);

  if (DEBUG_SERIAL_ONLY) {
    // breathe on the MQ sensors / shine different colors at the TCS3200 and
    // confirm each number responds (Phase C verification)
    Serial.printf("toilet gasRaw=%d vocRaw=%d turbidity=%.2f hour=%d\n",
                  gasRaw, vocRaw, turbidity, hourOfDay());
    return;
  }

  String payload = String("{\"gas_ppm\":") + String(gasPpm, 2) +
                   ",\"voc_ppm\":" + String(vocPpm, 2) +
                   ",\"turbidity\":" + String(turbidity, 2) +
                   ",\"hour_of_day\":" + hourOfDay() + "}";
  bool ok = client.publish("sensor/toilet/reading", payload.c_str());
  Serial.println(ok ? ("MQTT -> " + payload) : "MQTT publish failed");
}
