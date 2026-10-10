/*
 * SafeBath AI — Node HTTP client (shared firmware header)
 * -------------------------------------------------------
 * Minimal WiFi+HTTP POST helper for the v1 device telemetry protocol.
 * Boards replace `client.publish(topic, payload)` with:
 *
 *   postMessage(msg, "192.168.1.50", 8000, token, timeout_ms, out_code);
 *
 * The RPi replies 200 on acceptance and 4xx/409 on rejection — `out_code`
 * lets the board distinguish "accepted / duplicate / rejected".
 *
 * Auth: optional per-device token sent as X-Node-Token (must match the token
 * configured in backend/device_manager.py's SAFEBATH_DEVICES config).
 *
 * NOTE: kept intentionally simple (one shot per message, new TCP connection).
 * Boards that publish at 10 Hz should hold one HTTPClient + WiFiClient and
 * reuse begin() to avoid connection churn.
 */
#ifndef SAFEBATH_NODE_HTTP_CLIENT_H
#define SAFEBATH_NODE_HTTP_CLIENT_H

#include <Arduino.h>

#include <HTTPClient.h>
#include <WiFi.h>

#include "SafeBathTelemetry.h"

#ifndef SAFEBATH_HTTP_TIMEOUT_MS
#define SAFEBATH_HTTP_TIMEOUT_MS 5000ul
#endif

namespace safebath {

/*
 * POST `body` (a full serialized envelope) to http://host:port/path.
 * Returns true if the server answered 2xx. `out_code` always receives the
 * HTTP status (0 if the request could not be completed).
 */
inline bool postJson(const char* host, uint16_t port, const char* path,
                     const char* token, const char* body,
                     unsigned long timeout_ms, int& out_code) {
  out_code = 0;
  if (!host || !path || !body) return false;
  if (WiFi.status() != WL_CONNECTED) {
    out_code = -1;
    return false;
  }

  HTTPClient http;
  http.setConnectTimeout(timeout_ms);
  http.setTimeout(timeout_ms);

  String url = String("http://") + host + ":" + String(port) + path;
  if (!http.begin(url)) {
    out_code = -1;
    return false;
  }

  http.addHeader("Content-Type", "application/json");
  if (token && token[0]) http.addHeader("X-Node-Token", token);

  int code = http.POST(body);
  http.end();
  out_code = code;
  return (code >= 200 && code < 300);
}

/*
 * Convenience wrapper: serialize a TelemetryMessage and post it to the right
 * /api/v1/<type> endpoint. Returns true on a 2xx response.
 */
inline bool postMessage(const TelemetryMessage& msg, const char* host,
                        uint16_t port, const char* token,
                        unsigned long timeout_ms, int& out_code) {
  char body[SAFEBATH_TELEMETRY_MAX_LEN];
  size_t n = serializeTelemetry(body, sizeof(body), msg);
  if (n == 0) {
    out_code = -1;
    return false;
  }
  return postJson(host, port, pathForMessageType(msg.message_type), token,
                  body, timeout_ms, out_code);
}

}  // namespace safebath

#endif  // SAFEBATH_NODE_HTTP_CLIENT_H