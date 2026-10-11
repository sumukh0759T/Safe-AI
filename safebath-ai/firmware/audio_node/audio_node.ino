/*
 * SafeBath AI — Board 4: Acoustic Distress Detection (analog MAX4466)
 * Phase D standalone node.
 *
 * Wiring = Master Guide Section 1 (Board 4): MAX4466 OUT -> GPIO34 (ADC1).
 *
 * Publishes the Section 3 contract ~5 Hz:
 *   sensor/audio/event : {"label": "silence"|"background_noise"|
 *                         "impact_thud"|"cry_or_scream"|"ordinary_speech"|
 *                         "water_sound", "energy": float}
 *   The six labels MUST equal backend/schemas.py AudioLabel exactly — any
 *   other string is rejected by the edge with HTTP 400 invalid_payload.
 *   (The pre-migration 4-class set — "water"/"speech_or_cry" — is what makes an
 *   audio node that still runs old firmware look broken; reflash this sketch.)
 *
 * Feature extraction per Master Guide Section 5.1 — RMS, zero-crossing rate,
 * spectral centroid from a 256-point FFT. These MUST match
 * datasets/audio_features.py exactly (same chunk size 256, same Hann window,
 * same centroid definition) so training and inference compute identical
 * features. Threshold classification first (Phase D); the trained tiny dense
 * head can replace thresholds later without changing this contract.
 *
 * Voice Bridge reply flow (Master Guide Section 8): Board 5 posts a voice
 * trigger event; this board POLLS GET /api/v1/voice/pending?device_id=audio-01,
 * plays nothing itself (the DFPlayer is on Board 5) but listens ~8 s and POSTs
 * the reply event as fine / help / no_response.
 *
 * Libraries: arduinoFFT (no PubSubClient)
 */
#include <WiFi.h>
#include "../common/SafeBathNode.h"
#include <arduinoFFT.h>

const char* device_id = "audio-01";         // must match SAFEBATH_DEVICES
const char* ssid = "OnePlus 5";
const char* password = "Yatslove";
const char* server_host = "192.168.43.66";   // RPi IP running the edge API
const uint16_t server_port = 8000;
const char* node_token = "";                // optional; matches device token

safebath::Node node(device_id, "audio", "1.0.0");

// Network bring-up Step 1 (Network & Autonomous Build Guide Section 1):
// true = print features + label to Serial only, no network.
#define DEBUG_SERIAL_ONLY false

#define MIC_PIN 34
#define SAMPLE_RATE_HZ 8000      // must match datasets/audio_features.SR
#define FFT_CHUNK 256            // must match datasets/audio_features.CHUNK

// --- classification thresholds (Phase D starting points; recalibrate on-site)
const float TH_SILENCE_RMS = 0.03;
const float TH_THUD_CENTROID = 500.0;     // Hz — thuds are low-frequency
const float TH_WATER_ZCR = 0.15;          // broadband hiss -> high ZCR
const float TH_SPEECH_RMS = 0.20;
const float TH_REPLY_HELP_RMS = 0.45f;    // mirrors backend voice_bridge heuristic

// --- rolling feature accumulators ---
float rmsAccum = 0.0, zcrAccum = 0.0, centroidAccum = 0.0;
int featureWindows = 0;

// --- Voice Bridge reply window state ---
bool listenForReply = false;
unsigned long replyWindowEnd = 0;
float replyRmsSum = 0.0f;
int replySpeechChunks = 0, replyChunks = 0;

double vReal[FFT_CHUNK];
double vImag[FFT_CHUNK];
ArduinoFFT<double> FFT(vReal, vImag, FFT_CHUNK, SAMPLE_RATE_HZ);

/* -------------------- Voice Bridge (HTTP poll) -------------------- */
void finishReplyWindow();

// Ask the edge server for a pending voice prompt (listen role). When one is
// claimed we open the ~8 s listen window; the server leases it for voice_lease_s.
// A separate board (Board 5) claims the same request with role=play to speak it.
void pollVoiceRequest() {
  if (listenForReply) return;                 // already listening
  String body = node.getJson("/api/v1/voice/pending?device_id=audio-01&role=listen");
  if (body.length() == 0) return;
  String flat = body;
  flat.replace(" ", "");
  if (flat.indexOf("\"has_request\":true") < 0) return;
  listenForReply = true;
  replyWindowEnd = millis() + 8000;           // ~8 s listen window per contract
  replyRmsSum = 0.0f;
  replySpeechChunks = 0;
  replyChunks = 0;
  Serial.println("[voice] claimed prompt; listening 8 s");
}

/* -------- features: identical math to datasets/audio_features.py ------- */
void extractFeaturesChunk() {
  unsigned long nextTick = micros();
  const unsigned long tickUs = (unsigned long)(1000000UL / SAMPLE_RATE_HZ);
  float sumSq = 0.0f;
  int crossings = 0;
  float prev = analogRead(MIC_PIN) / 4095.0f - 0.5f;

  for (int i = 0; i < FFT_CHUNK; i++) {
    while ((unsigned long)(micros() - nextTick) < tickUs) { /* spin */ }
    nextTick += tickUs;
    float s = analogRead(MIC_PIN) / 4095.0f - 0.5f;   // center on bias point
    vReal[i] = s;
    vImag[i] = 0.0;
    sumSq += s * s;
    if ((s >= 0) != (prev >= 0)) crossings++;
    prev = s;
  }

  // Hann window -> FFT -> spectral centroid (matches np.hanning + rfft mean)
  FFT.windowing(FFT_WIN_TYP_HANN, FFT_FORWARD);
  FFT.compute(FFT_FORWARD);
  FFT.complexToMagnitude();

  double magSum = 0.0, freqSum = 0.0;
  for (int i = 1; i < FFT_CHUNK / 2; i++) {
    double f = (double)i * SAMPLE_RATE_HZ / FFT_CHUNK;
    magSum += vReal[i];
    freqSum += vReal[i] * f;
  }
  float centroid = magSum > 0 ? (float)(freqSum / magSum) : 0.0f;

  rmsAccum += sqrtf(sumSq / FFT_CHUNK);
  zcrAccum += (float)crossings / (FFT_CHUNK - 1);
  centroidAccum += centroid;
  featureWindows++;
}

/* ----------------------- classification -------------------------------- */
String classify(float rms, float zcr, float centroid) {
  if (rms < TH_SILENCE_RMS) return "silence";
  if (centroid < TH_THUD_CENTROID && zcr < TH_WATER_ZCR) return "impact_thud";
  if (zcr > TH_WATER_ZCR) return "water_sound";
  if (rms > TH_SPEECH_RMS * 1.2f && centroid > 350.0f) return "cry_or_scream";
  if (rms > TH_SPEECH_RMS) return "ordinary_speech";
  if (zcr > 0.08f) return "background_noise";
  return "background_noise";
}

/* ------------------- Voice Bridge reply heuristics ---------------------- */
// Mirrors backend/voice_bridge._heuristic_reply — keep in sync.
void finishReplyWindow() {
  listenForReply = false;
  float avgRms = replyChunks ? replyRmsSum / replyChunks : 0.0f;
  float speechRatio =
      replyChunks ? (float)replySpeechChunks / replyChunks : 0.0f;

  String reply;
  if (avgRms < 0.02f) reply = "no_response";
  else if (avgRms > TH_REPLY_HELP_RMS || speechRatio > 0.35f)
    reply = (avgRms > TH_REPLY_HELP_RMS) ? "help" : "fine";
  else reply = "no_response";

  String payload = String("{\"reply\":\"") + reply + "\"}";
  int code = 0;
  node.event(payload.c_str(), &code);
  Serial.printf("[voice] reply=%s -> HTTP %d\n", reply.c_str(), code);
}

/* --------------------------- Setup / Loop ------------------------------- */
void setup() {
  Serial.begin(115200);
  analogSetPinAttenuation(MIC_PIN, ADC_11db);   // full 0-3.3V range
  node.begin(ssid, password, server_host, server_port, node_token);
}

void loop() {
  static unsigned long lastVoicePoll = 0;
  if (millis() - lastVoicePoll > 1000) {        // voice_poll_interval_s = 1
    lastVoicePoll = millis();
    pollVoiceRequest();
  }

  extractFeaturesChunk();

  if (listenForReply) {
    float chunkRms = featureWindows ? rmsAccum / featureWindows : 0.0f;
    replyRmsSum += chunkRms;
    replyChunks++;
    if (chunkRms > TH_SPEECH_RMS * 0.6f) replySpeechChunks++;
    if (millis() > replyWindowEnd) finishReplyWindow();
  } else if (featureWindows >= 16) {           // ~0.5 s of audio aggregated
    float rms = rmsAccum / featureWindows;
    float zcr = zcrAccum / featureWindows;
    float centroid = centroidAccum / featureWindows;
    String label = classify(rms, zcr, centroid);

    if (DEBUG_SERIAL_ONLY) {
      // clap / run water / stay silent -> 3 different labels reliably
      // (Phase D verification)
      Serial.printf("audio rms=%.3f zcr=%.3f centroid=%.1f label=%s\n",
                    rms, zcr, centroid, label.c_str());
    } else {
      String payload = String("{\"label\":\"") + label +
                       "\",\"energy\":" + String(rms, 3) + "}";
      int code = 0;
      node.telemetry(payload.c_str(), &code);
      Serial.printf("[audio] %s -> HTTP %d\n", payload.c_str(), code);
    }

    rmsAccum = zcrAccum = centroidAccum = 0.0f;
    featureWindows = 0;
  }
}
