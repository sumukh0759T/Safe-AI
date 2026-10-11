/*
 * SafeBath AI — v1 Device Telemetry Protocol (shared firmware header)
 * ------------------------------------------------------------------
 * One versioned JSON contract for all five ESP32 boards. This header is the
 * firmware mirror of backend/ingestion_api.py — the JSON *key names, values
 * and message_type strings must stay byte-for-byte in sync with the Python
 * contract*, and the payload JSON fragment each board builds must match the
 * payload schemas in backend/schemas.py.
 * The mat board emits a four-zone frame by default
 * {"sensor_type":"pressure_4zone","zones":[4 readings]} (see
 * backend/schemas.py Pressure4ZonePayload, MAT_ZONE_ORDER). A legacy
 * 12x6 grid frame ({"values":[72 ints]}) is only built when the mat sketch is
 * compiled with MAT_ZONE_MODE=0.
 *
 * Each board builds a TelemetryMessage and serializes it to exactly:
 *
 *   {"contract_version":1,"device_id":"mat-01","module":"mat",
 *    "firmware_version":"1.0.0","boot_id":"A1B2C3D4E5F60718",
 *    "sequence":17,"device_uptime_ms":604800123,
 *    "timestamp":1699561234.5,"message_type":"telemetry",
 *    "sensor_status":"ok","payload":{...}}
 *
 * Endpoints (see NodeHttpClient.h):
 *   POST /api/v1/telemetry      message_type = "telemetry"
 *   POST /api/v1/heartbeat      message_type = "heartbeat"
 *   POST /api/v1/event          message_type = "event"
 *   POST /api/v1/diagnostic     message_type = "diagnostic"
 *
 * GPIO assignments are NOT part of this header — boards keep their own pins.
 */
#ifndef SAFEBATH_TELEMETRY_H
#define SAFEBATH_TELEMETRY_H

#include <Arduino.h>
#include <stdint.h>

#if defined(ARDUINO_ARCH_ESP32)
#include <esp_random.h>
#endif

#ifndef SAFEBATH_CONTRACT_VERSION
#define SAFEBATH_CONTRACT_VERSION 1u
#endif

// Longest serialized message: a legacy 12x6 mat frame (~480 chars at 72 ints)
// or a four-zone frame (~120 chars). Any board may use more; the serializer
// truncates gracefully to the buffer.
#ifndef SAFEBATH_TELEMETRY_MAX_LEN
#define SAFEBATH_TELEMETRY_MAX_LEN 1024u
#endif

namespace safebath {

enum MessageType : uint8_t {
  MESSAGE_TELEMETRY = 0,
  MESSAGE_HEARTBEAT = 1,
  MESSAGE_EVENT = 2,
  MESSAGE_DIAGNOSTIC = 3,
};

inline const char* messageTypeName(MessageType t) {
  switch (t) {
    case MESSAGE_TELEMETRY:  return "telemetry";
    case MESSAGE_HEARTBEAT:  return "heartbeat";
    case MESSAGE_EVENT:      return "event";
    case MESSAGE_DIAGNOSTIC: return "diagnostic";
  }
  return "telemetry";
}

inline const char* pathForMessageType(MessageType t) {
  switch (t) {
    case MESSAGE_TELEMETRY:  return "/api/v1/telemetry";
    case MESSAGE_HEARTBEAT:  return "/api/v1/heartbeat";
    case MESSAGE_EVENT:      return "/api/v1/event";
    case MESSAGE_DIAGNOSTIC: return "/api/v1/diagnostic";
  }
  return "/api/v1/telemetry";
}

/*
 * boot_id: one random 64-bit value drawn ONCE per boot session. Every message
 * in the same boot shares this id with a strictly increasing per-boot sequence;
 * the RPi rejects duplicate / out-of-order (boot_id, sequence) pairs. Drawn
 * from the hardware RNG (esp_random(), esp_random.h — the ESP32 RF RNG), NOT
 * the eFuse MAC — the MAC never changes between reboots, so using it would make
 * every reboot look like an out-of-order continuation of the previous session
 * and all frames would be rejected with HTTP 409.
 */
inline void makeBootId(char* out, size_t capacity) {
  if (capacity == 0) return;
#if defined(ARDUINO_ARCH_ESP32)
  /* esp_random() reads the hardware (RF) RNG. Two 32-bit draws are combined
     into one 64-bit per-boot session id. */
  uint64_t r = ((uint64_t)esp_random() << 32) ^ (uint64_t)esp_random();
  char tmp[17];
  snprintf(tmp, sizeof(tmp), "%016llX", (unsigned long long)r);
#elif defined(ARDUINO_ARCH_ESP8266)
  /* ESP8266 core has no esp_random(); EspClass::random() reads os_random. */
  char tmp[17];
  snprintf(tmp, sizeof(tmp), "%016llX",
           (((uint64_t)ESP.random() << 32) ^ (uint64_t)ESP.random()));
#else
  char tmp[17];
  snprintf(tmp, sizeof(tmp), "%08lX", (unsigned long)(millis() ^ 0x5AFEBA77u));
#endif
  size_t n = strlen(tmp);
  size_t m = (n < capacity - 1) ? n : capacity - 1;
  memcpy(out, tmp, m);
  out[m] = '\0';
}

/*
 * Per-boot monotonic counter. One counter per board, shared across all four
 * message types — firmware increments it by one for EVERY message it sends.
 * Duplicates / out-of-order sequences are rejected by the RPi.
 */
class SequenceCounter {
public:
  uint32_t next() { return sequence_++; }
  uint32_t peek() const { return sequence_; }
  void reset() { sequence_ = 0; }

private:
  uint32_t sequence_ = 0;
};

/*
 * One outbound message. `payload` must point at a complete JSON object string
 * (with braces) built by the board, e.g. "{\"presence\":true,...}".
 */
struct TelemetryMessage {
  const char* device_id;
  const char* module;              // "mat" | "radar" | "toilet" | "audio" | "tof"
  const char* firmware_version;
  const char* boot_id;             // caller fills via makeBootId()
  uint32_t sequence;
  uint64_t device_uptime_ms;
  double timestamp;                // unix seconds; 0.0 = use RPi clock
  MessageType message_type;
  const char* sensor_status;       // "ok" | "degraded" | "fault"
  const char* payload;             // pre-built JSON object string "{...}"
};

/*
 * Serialize m into out as a single JSON line. Returns bytes written
 * (excluding NUL), or 0 if the buffer is too small. Matches the Python
 * contract exactly — do not reorder keys.
 */
inline size_t serializeTelemetry(char* out, size_t capacity,
                                 const TelemetryMessage& m) {
  if (capacity == 0) return 0;
  int n = snprintf(
      out, capacity,
      "{\"contract_version\":%u,"
      "\"device_id\":\"%s\","
      "\"module\":\"%s\","
      "\"firmware_version\":\"%s\","
      "\"boot_id\":\"%s\","
      "\"sequence\":%lu,"
      "\"device_uptime_ms\":%llu,"
      "\"timestamp\":%.3f,"
      "\"message_type\":\"%s\","
      "\"sensor_status\":\"%s\","
      "\"payload\":%s}",
      (unsigned)SAFEBATH_CONTRACT_VERSION,
      m.device_id ? m.device_id : "",
      m.module ? m.module : "",
      m.firmware_version ? m.firmware_version : "",
      m.boot_id ? m.boot_id : "",
      (unsigned long)m.sequence,
      (unsigned long long)m.device_uptime_ms,
      m.timestamp,
      messageTypeName(m.message_type),
      m.sensor_status ? m.sensor_status : "ok",
      (m.payload && m.payload[0]) ? m.payload : "{}");
  if (n < 0 || (size_t)n >= capacity) {
    out[0] = '\0';
    return 0;
  }
  return (size_t)n;
}

}  // namespace safebath

#endif  // SAFEBATH_TELEMETRY_H