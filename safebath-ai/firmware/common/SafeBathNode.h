/*
 * SafeBath AI — shared node runtime (firmware header)
 * ---------------------------------------------------
 * Small convenience layer over the v1 telemetry protocol + HTTP client so the
 * five node sketches share one WiFi/boot-id/sequence/posting implementation
 * instead of each re-declaring HTTP bookkeeping.
 *
 * Usage in a sketch:
 *
 *   #include "../common/SafeBathNode.h"
 *   safebath::Node node("mat-01", "mat", "1.0.0");
 *   ...
 *   node.begin(ssid, password, server_host, server_port, node_token);
 *   ...
 *   node.telemetry("{\"values\":[1,2,...]}");   // POST /api/v1/telemetry
 *   node.event("{\"entry\":true}");             // POST /api/v1/event
 *   String r = node.getJson("/api/v1/voice/pending?device_id=audio-01");
 *
 * The RPi never requires the board's clock: timestamp is sent as 0.0 and the
 * server stamps arrival time. Sequence is per-boot and monotonic.
 */
#ifndef SAFEBATH_NODE_H
#define SAFEBATH_NODE_H

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>

#include "SafeBathTelemetry.h"
#include "NodeHttpClient.h"

namespace safebath {

/* Blocking WiFi connect with a bounded wait so a bad SSID never wedges boot. */
inline bool connectWifi(const char* ssid, const char* password,
                        uint32_t timeout_ms = 20000ul) {
  if (WiFi.status() == WL_CONNECTED) return true;
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && (millis() - t0) < timeout_ms) {
    delay(300);
    Serial.print(".");
  }
  Serial.println();
  return WiFi.status() == WL_CONNECTED;
}

class Node {
 public:
  Node(const char* device_id, const char* module, const char* firmware_version)
      : device_id_(device_id), module_(module), fw_(firmware_version) {}

  void begin(const char* ssid, const char* password, const char* host,
             uint16_t port, const char* token) {
    host_ = host;
    port_ = port;
    token_ = token;

    Serial.print("[net] connecting to WiFi ");
    Serial.print(ssid);
    Serial.print(" ");
    if (connectWifi(ssid, password)) {
      Serial.print("[net] connected, ESP32 IP ");
      Serial.println(WiFi.localIP());
    } else {
      Serial.println("[net] WiFi NOT connected yet (each send will retry)");
    }

    makeBootId(boot_id_, sizeof(boot_id_));
    Serial.printf("[net] device=%s module=%s boot=%s -> http://%s:%u\n",
                  device_id_, module_, boot_id_, host_, port_);
  }

  bool wifiUp() {
    if (WiFi.status() == WL_CONNECTED) return true;
    // opportunistic reconnect (non-blocking-ish, short window)
    WiFi.reconnect();
    uint32_t t0 = millis();
    while (WiFi.status() != WL_CONNECTED && (millis() - t0) < 5000ul) {
      delay(200);
    }
    return WiFi.status() == WL_CONNECTED;
  }

  bool send(MessageType type, const char* payload, int* out_code = nullptr) {
    if (!wifiUp()) {
      if (out_code) *out_code = -1;
      return false;
    }
    TelemetryMessage m;
    m.device_id = device_id_;
    m.module = module_;
    m.firmware_version = fw_;
    m.boot_id = boot_id_;
    m.sequence = seq_.next();
    m.device_uptime_ms = (uint64_t)millis();
    m.timestamp = 0.0;             // let the RPi stamp arrival time
    m.message_type = type;
    m.sensor_status = "ok";
    m.payload = payload;
    int code = 0;
    bool ok = postMessage(m, host_, port_, token_, SAFEBATH_HTTP_TIMEOUT_MS, code);
    if (out_code) *out_code = code;
    return ok;
  }

  bool telemetry(const char* payload, int* out_code = nullptr) {
    return send(MESSAGE_TELEMETRY, payload, out_code);
  }
  bool event(const char* payload, int* out_code = nullptr) {
    return send(MESSAGE_EVENT, payload, out_code);
  }
  bool heartbeat(int* out_code = nullptr) {
    return send(MESSAGE_HEARTBEAT, "{}", out_code);
  }

  /* GET a JSON path (e.g. voice/pending). Returns "" on any failure. */
  String getJson(const char* path) {
    String out;
    if (!wifiUp()) return out;
    HTTPClient http;
    http.setConnectTimeout(SAFEBATH_HTTP_TIMEOUT_MS);
    http.setTimeout(SAFEBATH_HTTP_TIMEOUT_MS);
    String url = String("http://") + host_ + ":" + String(port_) + path;
    if (!http.begin(url)) return out;
    if (token_ && token_[0]) http.addHeader("X-Node-Token", token_);
    int code = http.GET();
    if (code >= 200 && code < 300) out = http.getString();
    http.end();
    return out;
  }

  /* Bounded raw JSON POST for additive control/status APIs outside telemetry. */
  bool postJson(const char* path, const char* body, unsigned long timeout_ms,
                int* out_code = nullptr) {
    int code = 0;
    const bool ok = safebath::postJson(host_, port_, path, token_, body,
                                       timeout_ms, code);
    if (out_code) *out_code = code;
    return ok;
  }

  const char* deviceId() const { return device_id_; }
  const char* bootId() const { return boot_id_; }

 private:
  const char* device_id_;
  const char* module_;
  const char* fw_;
  const char* host_ = "";
  uint16_t port_ = 8000;
  const char* token_ = "";
  char boot_id_[17] = {0};
  SequenceCounter seq_;
};

/* Tiny JSON string-escape for payloads that embed free text (voice replies). */
inline String jsonEscape(const String& in) {
  String out;
  out.reserve(in.length() + 8);
  for (size_t i = 0; i < in.length(); i++) {
    char c = in[i];
    if (c == '"' || c == '\\') {
      out += '\\';
      out += c;
    } else if (c == '\n') {
      out += "\\n";
    } else if (c == '\r') {
      out += "\\r";
    } else if (c == '\t') {
      out += "\\t";
    } else {
      out += c;
    }
  }
  return out;
}

}  // namespace safebath

#endif  // SAFEBATH_NODE_H
