# Module 5 DFPlayer Mini setup

## Existing board and backend configuration

The firmware entry point is `tof_thermal_voice_node.ino`. Its current source
assignments are:

| Signal | ESP32-WROOM-32 pin | Notes |
| --- | ---: | --- |
| DFPlayer TX -> ESP32 RX2 | GPIO16 | UART2 receive |
| ESP32 TX2 -> DFPlayer RX | GPIO17 | UART2 transmit; use the level/interface protection required by the installed board |
| DFPlayer audio output | SPK_R | Existing board notes route this to the speaker/amplifier input; verify the installed amplifier wiring |
| YF-S201 pulse | GPIO34 | Existing input-only flow input; unchanged |
| Relay input | GPIO33 | Existing relay behavior is unchanged by this audio work |
| ToF XSHUT | GPIO25/26/27 | Unchanged |
| I2C SDA/SCL | GPIO21/22 | Unchanged |

The firmware already communicates with the Raspberry Pi through the shared
`SafeBathNode` HTTP client. The board ID remains `tof-01`. No MQTT, new device
ID, schema migration, or new route was introduced. Do not put the ESP32 pin or
DFPlayer speaker outputs directly across a speaker unless the module's
documented output arrangement supports it; verify common ground, supply,
amplifier input and installed wiring against the actual modules before power.

## SD card prompt files

Format the card as FAT32 and place these named files in the root `mp3`
directory:

| File | Existing prompt name | Use |
| --- | --- | --- |
| `/mp3/0001.mp3` | `are_you_ok` | Existing check-in request |
| `/mp3/0002.mp3` | `help` | Help prompt |
| `/mp3/0003.mp3` | `emergency_procedure` | Emergency procedure reminder |
| `/mp3/0004.mp3` | `safety_check` | Safety check |

The firmware calls DFRobot's `playMp3Folder(number)`, whose documented
convention is `SD:/MP3/000N.mp3`. It deliberately does not use `play(number)`,
which selects from the root file index and does not mean the same thing.
Prompt names are matched against a fixed allowlist in `Module5Prompt.h`;
unknown text and track numbers outside 1–4 are ignored.

## Backend command and authentication

The firmware uses the existing
`GET /api/v1/voice/pending?device_id=tof-01&role=play` queue. The response
shape remains `{"has_request":true,"prompt":"are_you_ok",...}`. An existing
confirmed/local event may enqueue `are_you_ok`; Gemini text is never parsed as
a command. Player status is reported over serial (card, command sent,
completion/error); it is not added to the telemetry contract and does not
claim acoustic playback was physically verified.

When the registered `tof-01` device has a token in `SAFEBATH_DEVICES`, the
existing route now checks the `X-Node-Token` header using the same per-device
token as telemetry. `SafeBathNode.getJson()` already sends that header when
`node_token` is configured. Copy
`Module5Credentials.example.h` to the git-ignored
`Module5Credentials.local.h`, then set the same private token in that local
header and the Pi's device configuration. Keep the local header out of Git.
For compatibility with existing development setups, a registered device with
no configured token remains unauthenticated; do not expose that configuration
to an untrusted network. Unknown/disabled device IDs receive HTTP 403.

The legacy queue marks a request played when it is claimed, not when sound is
measured. A command sent, a completion event, and sound heard from the
intended speaker are different verification levels. If the player is not
reporting an SD card online, firmware leaves queued prompts unclaimed and
continues sensor/network work. Reconnection attempts use a bounded backoff.

## Libraries and build environment

There is no PlatformIO project or Arduino CLI configuration checked in. The
sketch uses:

- Espressif ESP32 Arduino core (board FQBN `esp32:esp32:esp32` for a generic
  ESP32 Dev Module)
- `DFRobotDFPlayerMini`
- `Adafruit VL53L1X`
- `Adafruit AMG88xx`
- `Adafruit BusIO` (Adafruit dependency)

Install Arduino CLI and those board/library packages on Windows, then from the
`safebath-ai` directory compile:

```powershell
arduino-cli core update-index
arduino-cli core install esp32:esp32
arduino-cli lib install DFRobotDFPlayerMini
arduino-cli lib install "Adafruit VL53L1X"
arduino-cli lib install "Adafruit AMG88xx"
arduino-cli lib install "Adafruit BusIO"
arduino-cli compile --fqbn esp32:esp32:esp32 firmware\tof_thermal_voice_node
```

After inspecting wiring and choosing the correct COM port, upload manually:

```powershell
arduino-cli upload -p COM5 --fqbn esp32:esp32:esp32 firmware\tof_thermal_voice_node
```

Replace `COM5` with the port shown on your machine. Arduino IDE alternative:
install the same core/libraries, open
`firmware/tof_thermal_voice_node/tof_thermal_voice_node.ino`, select
**ESP32 Dev Module** and the correct COM port, then compile/upload from the IDE.

Firmware compilation and hardware playback have not been verified in this
workspace: Arduino CLI/PlatformIO are not installed here. The existing
firmware source has local network settings in its working-tree diff; keep that
private change out of any source commit.
