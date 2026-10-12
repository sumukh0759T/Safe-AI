# Caretaker dashboard and speaker test

The dashboard pages are served on port 8080; the API remains on port 8000. The
caretaker page derives the API host from the page host, so do not pin a previous
Pi address in the built assets. The static server denies hidden files,
`.env*`, and `config.local.js`.

## Build on Windows

From PowerShell in the repository:

```powershell
cd C:\Users\YATS\Desktop\major\safebath-ai\frontend
npm ci
npm test
npm run build
```

Open `http://<current-pi-ip>:8080/` for the dashboard and
`http://<current-pi-ip>:8080/caretaker.html` for the caretaker dashboard. Find
the current address on the Pi with `hostname -I`; do not rely on an old DHCP
address.

## Configure caretaker write authorization

Acknowledgement and resolution POSTs require `SAFEBATH_CARETAKER_TOKEN`. Generate
a random token on the Pi and store it in a root-readable file (do not commit it
or put it in frontend configuration):

```bash
sudo install -d -m 700 /etc/safebath
TOKEN="$(openssl rand -hex 32)"
printf 'SAFEBATH_CARETAKER_TOKEN=%s\n' "$TOKEN" | sudo tee /etc/safebath/caretaker-token >/dev/null
sudo chmod 600 /etc/safebath/caretaker-token
```

Add this systemd drop-in to each API service that serves caretaker actions
(`safebath.service`):

```bash
sudo systemctl edit safebath
```

Enter:

```ini
[Service]
EnvironmentFile=/etc/safebath/caretaker-token
```

Then restart only after reviewing the unit change:

```bash
sudo systemctl daemon-reload
sudo systemctl restart safebath
sudo systemctl status safebath --no-pager
```

Enter the same token in the caretaker page. It stays in that page's memory and
must be entered again after reloading. Without the server token, actions return `503`; a bad token
returns `401`. The event is first acknowledged, then a separate **Resolve
incident** action records who resolved it and when. Resolution is not inferred
from a quiet sensor or Gemini response.

The included Pi server uses plain HTTP. Use it only on a trusted private LAN:
the bearer token crosses that LAN without transport encryption, and browsers
deny geolocation from a Pi page opened over ordinary HTTP. Before using the
token outside a trusted LAN or enabling browser location there, put both the
static page and API behind a trusted HTTPS reverse proxy and set the app's API
base to that HTTPS endpoint. No TLS proxy or certificate is installed by this
change.

## Install the persistent port 8080 server

First verify the repository path and whether another service owns port 8080:

```bash
test -f /home/vinnu/Safe-AI/safebath-ai/frontend/caretaker.html
sudo ss -ltnp '( sport = :8080 )'
```

Build/deploy the current frontend files into that checkout, then install the
service file from `deployment/raspberry_pi/safebath-web.service`:

```bash
cd /home/vinnu/Safe-AI/safebath-ai/frontend
npm ci
npm run build
cd /home/vinnu/Safe-AI/safebath-ai
sudo cp deployment/raspberry_pi/safebath-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now safebath-web
sudo systemctl status safebath-web --no-pager
sudo journalctl -u safebath-web -n 50 --no-pager
```

If port 8080 is already occupied, identify the owning service before changing
it. Do not stop an unknown process. Then verify locally on the Pi:

```bash
curl -I http://127.0.0.1:8080/
curl -I http://127.0.0.1:8080/caretaker.html
curl -I http://127.0.0.1:8080/config.local.js
curl -fsS http://127.0.0.1:8000/api/v1/health
```

The first two should return `200`, `config.local.js` should return `404`, and
the API health request should return success. From another device on the same
LAN, open both pages using the Pi's current address. If a firewall is active,
allow TCP 8080 only on the trusted LAN interface/network.

## Simulated SOS and physical speaker check

Run the offline-only simulation from the repository on Windows:

```powershell
cd C:\Users\YATS\Desktop\major\safebath-ai
python -m pytest tests\test_emergency_pipeline.py tests\test_fusion_engine.py
```

These tests use temporary SQLite databases and do not contact notification
providers or emergency services. They do not prove a physical alarm, speaker,
or Pi service works. Before using **Remote speaker test**, verify that
`tof-01` is the actual ESP32 connected to the DFPlayer; its software identity
cannot establish the physical wiring. The firmware uses `/mp3/0001.mp3` through
`/mp3/0004.mp3` and the DFRobot `playMp3Folder` convention. The firmware's
existing `tof-01` / `tof` identity and backend routes remain unchanged.

Web Push delivery is supported when configured; this project currently does
not implement SMS, Firebase, or Telegram delivery. If the Pi/API is down,
remote alerts and remote speaker commands cannot be delivered. Local firmware
behavior depends on the physical device remaining powered and connected to its
own sensors/speaker.

## Pi-side deployment verification

This repository does not have access to the Pi, so the service has not been
started there and the live endpoints have not been verified. After the above
commands, capture `systemctl status safebath safebath-fusion safebath-worker
safebath-web`, `ss -ltnp '( sport = :8080 )'`, both `curl -I` results, and the
browser's API connection status to confirm the actual deployment.
