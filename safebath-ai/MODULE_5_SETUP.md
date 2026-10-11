# Module 5 audit and implementation status

## Findings from the checked-in source

- Board 4 samples MAX4466 analog audio at 8 kHz on GPIO34 and computes RMS,
  zero-crossing and spectral-centroid features. Normal telemetry sends only a
  sound label and energy. Its voice-reply path sends a heuristic
  `fine`/`help`/`no_response`; it does not upload samples or recognize words.
- Board 5 assigns GPIO34 to rising-edge flow pulse counting, GPIO33 to a relay,
  UART2 GPIO17/16 to the DFPlayer, and I2C GPIO21/22 with XSHUT 25/26/27. Its
  `dfPlayer.play(1)` call can play a prerecorded SD track. It cannot be treated
  as arbitrary AI audio playback. No dynamic audio output has been tested.
- Board 5 samples a pulse count per loop and publishes `flow_pulses`; there is
  no calibrated rate or volume. There are no physical calibration results.
- Source sets the relay output HIGH on startup and later sets it LOW on the
  hot-fill condition. These commands and source comments do not verify the
  installed relay, valve polarity, coil supply, protection, or closure.
  Do not connect or actuate the valve pending a wiring and policy review.
- The active repo paths are under `safebath-ai/firmware/`; there are no
  top-level `firmware/` directories. This implementation preserves the v1
  telemetry and device contracts.

## Implemented: opt-in text-only AI test

Added a provider adapter using Gemini's official `generateContent` REST
interface (the project already depends on `httpx`), an opt-in and bearer-token
protected API, bounded in-memory conversation context, timeout/error handling,
and an explicit end-session operation. Google documents this REST API and its
server-side key header in [Gemini text generation](https://ai.google.dev/gemini-api/docs/text-generation/).
The model ID is supplied by the operator; no model availability or free quota
is assumed. Google's current pricing page lists free-of-charge input/output
for eligible model/service tiers, but exact RPM/TPM/RPD quota is project- and
model-specific and must be checked in AI Studio. Google's pricing page also
states free-tier data may be used to improve products; paid-tier data is not.
Do not submit private or identifiable speech/text under free-tier settings.
See [current pricing and data-use terms](https://ai.google.dev/gemini-api/docs/pricing)
and [rate limits](https://ai.google.dev/gemini-api/docs/rate-limits). This path accepts text only. It has no hardware callbacks, cannot
send alerts, and returns `alert_sent: false`.

Set these private environment variables on the Pi before starting the API;
`.env.example` has placeholders only. Do not commit real values:

```text
SAFEBATH_VOICE_TEST_MODE=true
SAFEBATH_VOICE_TEST_TOKEN=<long-random-token>
GEMINI_API_KEY=<server-side-key>
GEMINI_MODEL=<model currently available to your API key>
GEMINI_TIMEOUT_SECONDS=12
```

Restart the API. Call `POST /api/v1/voice-test/turn` with
`Authorization: Bearer <token>` and JSON
`{"session_id":"bench1","text":"I am okay"}`. End with
`DELETE /api/v1/voice-test/bench1`. Missing configuration leaves the endpoint
unavailable. The key is never returned in responses or logged by this adapter.

## Remaining phases and hardware blockers

1. **Speech input:** build bounded audio capture/upload only after checking
   analog microphone signal quality, ESP32 sampling/memory, bandwidth, and
   explicit privacy consent. No raw audio path exists today; sound features
   cannot be turned back into speech.
2. **Speech output:** DFPlayer is only the prerecorded SD track path in current
   firmware; amplifier notes refer to that path. Confirm a separate dynamic
   audio interface and wiring before implementation. Generated speech has not
   been played through the speaker.
3. **Flow:** verify the YF-S201 output voltage and protection/pull-up at GPIO34
   and confirm interrupt behavior on the installed ESP32 core. Calibrate using
   at least three measured-volume trials at two or more rates, calculate
   pulses/litre and error, then test zero and continuous flow. Until then,
   pulse count is not an accurate volume measurement.
4. **Valve:** establish the exact valve/driver/relay/supply ratings, installed
   normal state, protection, and wiring. Before any supervised bench test,
   replace the current automatic hot-fill relay path with an authenticated,
   deterministic state machine and simulated transitions. Verify closure only
   from calibrated flow stopping. The current sketch does not meet that bar.
5. **Dashboard:** existing UI has audio labels and legacy pulse values, not
   provider status, real conversation/playback state, calibrated flow/volume,
   or independently verified valve state. Treat demo data as demo data.

## Next physical step

With the valve/relay disconnected from power, inspect the Board 5 component
markings and wiring, then measure the flow sensor signal voltage at the ESP32
input with water off. Record the sensor output and interface details. Do not
actuate the valve during this inspection.
