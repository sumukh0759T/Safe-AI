#!/usr/bin/env bash
#
# SafeBath AI — Raspberry Pi 3 deployment installer.
#
# Pipelines the FastAPI edge server onto a fresh Raspberry Pi OS (Debian)
# without the Arduino IDE, a Windows machine, or any assumption about a
# "pi" user, an IP address, or secrets being present in git.
#
# What it does (idempotent — safe to re-run):
#   1. Detects Python + CPU arch (armv7 vs aarch64/x86_64) and creates a
#      dedicated system user (default: "safebath").
#   2. rsyncs the app into $INSTALL_DIR (/opt/safebath-ai), excluding
#      firmware/, data/, datasets/, training/, tests/, frontend/, git, venvs —
#      firmware carries wifi_credentials.local and only needs a PC to flash.
#   3. Builds a venv. On 32-bit armv7 it uses --system-site-packages plus
#      apt python3-numpy/python3-sklearn (no pip wheels exist for armv7);
#      an explicit --only-binary pip pass fails fast if a wheel is missing.
#   4. Creates $DATA_DIR (/var/lib/safebath) — SQLite lives here, writable
#      only by the service user.
#   5. Writes /etc/safebath/safebath.env (0600) and /etc/safebath/devices.json
#      (optional per-device tokens). Never committed to git.
# 6. Renders + installs safebath.service, safebath-fusion.service and
#    safebath-worker.service, daemon-reloads, enables, and (unless --no-start)
#    starts them.
#
# Usage:
#   sudo ./install.sh            # install and start
#   sudo ./install.sh --no-start # install but leave the service stopped
#
# Overrides (env): SAFEBATH_INSTALL_DIR, SAFEBATH_DATA_DIR, SAFEBATH_ETC_DIR,
#                  SAFEBATH_USER, PYTHON
#
# After install, boards POST to http://<pi-ip>:8000/api/v1/telemetry; find the
# address with `hostname -I`. Boards themselves are flashed with the Arduino
# IDE on a PC — never on the Pi.

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"     # repo root shipping this script
INSTALL_DIR="${SAFEBATH_INSTALL_DIR:-/opt/safebath-ai}"
DATA_DIR="${SAFEBATH_DATA_DIR:-/var/lib/safebath}"
ETC_DIR="${SAFEBATH_ETC_DIR:-/etc/safebath}"
RUN_AS="${SAFEBATH_USER:-safebath}"
SERVICE_NAME="safebath"
VENV_DIR="${INSTALL_DIR}/venv"
CONF_FILE="${ETC_DIR}/install.conf"

log() { printf '[safebath] %s\n' "$*"; }
die() { printf '[safebath] ERROR: %s\n' "$*" >&2; exit 1; }

require_root() {
  [[ "$(id -u)" -eq 0 ]] || die "run with sudo:  sudo $0"
}

detect_python_and_arch() {
  PYTHON="${PYTHON:-python3}"
  command -v "$PYTHON" >/dev/null 2>&1 || die "python3 not found on PATH"
  ARCH="$(uname -m)"
  log "architecture: ${ARCH}"
  [[ "${ARCH}" == "armv7l" || "${ARCH}" == "armv6l" ]] && PI_32BIT=1 || PI_32BIT=0
  # python3-venv is split out of the base package on Debian / Raspberry Pi OS
  if ! "$PYTHON" -m venv --help >/dev/null 2>&1; then
    log "installing python3-venv"
    apt-get install -y python3-venv
  fi
}

create_user_and_dirs() {
  if id -u "${RUN_AS}" >/dev/null 2>&1; then
    log "user '${RUN_AS}' already exists"
  else
    useradd --system -U -M -d "${INSTALL_DIR}" -s /usr/sbin/nologin "${RUN_AS}"
    log "created system user '${RUN_AS}'"
  fi
  install -d -m 0755 "${INSTALL_DIR}"
  install -d -o "${RUN_AS}" -g "${RUN_AS}" -m 0750 "${DATA_DIR}"
  install -d -m 0700 "${ETC_DIR}"
  chmod 0700 "${ETC_DIR}"

  cat > "${CONF_FILE}" <<EOF
# Written by install.sh — source of truth for update.sh / health_check.sh.
SAFEBATH_INSTALL_DIR=${INSTALL_DIR}
SAFEBATH_DATA_DIR=${DATA_DIR}
SAFEBATH_ETC_DIR=${ETC_DIR}
SAFEBATH_USER=${RUN_AS}
SERVICE_NAME=${SERVICE_NAME}
PYTHON=${PYTHON}
EOF
  chmod 0600 "${CONF_FILE}"
}

sync_files() {
  command -v rsync >/dev/null 2>&1 || apt-get install -y rsync
  log "syncing ${REPO_DIR} -> ${INSTALL_DIR}"
  # firmware/ is excluded: it holds wifi_credentials.local (secret) and is
  # only needed on the flashing PC, never at runtime.
  rsync -a --delete \
    --exclude '.git' \
    --exclude 'venv' --exclude '.venv' \
    --exclude '__pycache__' --exclude '*.py[co]' \
    --exclude 'data/' \
    --exclude 'datasets/' --exclude 'training/' --exclude 'demo_scripts/' \
    --exclude 'tests/' \
    --exclude 'frontend/' \
    --exclude 'firmware/' \
    --exclude '.env' --exclude '*.local' \
    "${REPO_DIR}/" "${INSTALL_DIR}/"
  chown -R root:root "${INSTALL_DIR}"
}

make_venv() {
  if [[ "${PI_32BIT}" -eq 1 ]]; then
    VENV_ARGS=(--system-site-packages)
    log "32-bit ARM detected: system-site-packages venv (apt numpy/sklearn)"
  else
    VENV_ARGS=()
  fi
  if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    log "creating venv at ${VENV_DIR}"
    "$PYTHON" -m venv "${VENV_ARGS[@]}" "${VENV_DIR}"
  fi
  "${VENV_DIR}/bin/python" -m pip install --upgrade pip setuptools wheel
}

install_packages() {
  local -a PACKAGES PIP_EXTRA
  PACKAGES=(fastapi uvicorn "pydantic>=2" httpx requests joblib)
  if [[ "${PI_32BIT}" -eq 1 ]]; then
    apt-get install -y python3-numpy python3-sklearn
    PIP_EXTRA=(--only-binary=:all:)
  else
    PACKAGES+=(numpy scikit-learn)
    PIP_EXTRA=()
  fi
  log "pip installing: ${PACKAGES[*]}"
  if ! "${VENV_DIR}/bin/pip" install "${PIP_EXTRA[@]}" "${PACKAGES[@]}"; then
    die "pip install failed. 32-bit (armv7) Raspberry Pi OS lacks pip wheels for pydantic-core/numpy. Install 64-bit Raspberry Pi OS (recommended) or provide build deps."
  fi
  # pywebpush (caretaker web push) is optional and depends on cryptography,
  # whose armv7 wheels may be missing. Install best-effort; the sync worker
  # logs "push unavailable" and everything else keeps working without it.
  if ! "${VENV_DIR}/bin/pip" install "${PIP_EXTRA[@]}" pywebpush; then
    log "WARNING: pywebpush unavailable (web push disabled; caretaker PWA still works on the LAN)"
  fi
}

write_env() {
  local env_file="${ETC_DIR}/safebath.env"
  if [[ -f "${env_file}" ]]; then
    log "kept existing ${env_file}"
    return
  fi
  (umask 0077; cat > "${env_file}" <<EOF
# SafeBath AI runtime config (chmod 0600) — never commit this file.
# Edit and \`sudo systemctl restart safebath safebath-fusion safebath-worker\`.
SQLITE_PATH=${DATA_DIR}/events.db
API_PORT=8000
SAFEBATH_DEVICES=${ETC_DIR}/devices.json

# Local alarm output — launched IMMEDIATELY on a genuine emergency as:
#   <SAFEBATH_LOCAL_ALARM> <event_id> <trigger>
# Point this at a GPIO/buzzer/siren script. Empty = log only.
SAFEBATH_LOCAL_ALARM=

# Device ids the dashboard must clearly label as SIMULATED (mock data), never
# as live hardware. Empty = none. Comma-separated, e.g. all five nodes.
SAFEBATH_SIMULATED_DEVICES=

# --- Supabase cloud mirror (optional; local safety works without it) ---
# Uncomment and set to enable durable cloud sync of emergencies/acks:
# SUPABASE_URL=https://YOUR-PROJECT.supabase.co
# SUPABASE_SERVICE_ROLE_KEY=

# --- Web push (VAPID) for caretaker notifications (optional) ---
# Generate once on the Pi: cd ${INSTALL_DIR} && venv/bin/python -m backend.push_service --generate-keys
# VAPID_PUBLIC_KEY=
# VAPID_PRIVATE_KEY=
# PUSH_SUBJECT=mailto:caretaker@example.com
EOF
)
  chmod 0600 "${env_file}"
  log "wrote ${env_file} (add real Telegram/Supabase values as needed)"
}

write_devices() {
  local dev_file="${ETC_DIR}/devices.json"
  if [[ -f "${dev_file}" ]]; then
    log "kept existing ${dev_file}"
    return
  fi
  # Built-in defaults only — no tokens, so boards work out of the box.
  # Add "token": "<shared-secret>" per device to enable X-Node-Token auth.
  (umask 0077; cat > "${dev_file}" <<'EOF'
{
  "mat-01":    {"module": "mat",    "name": "Pressure Mat"},
  "radar-01":  {"module": "radar",  "name": "Presence Radar"},
  "toilet-01": {"module": "toilet", "name": "Toilet Biomarker"},
  "audio-01":  {"module": "audio",  "name": "Acoustic Distress"},
  "tof-01":    {"module": "tof",    "name": "ToF / Thermal / Voice"}
}
EOF
)
  chown root:"${RUN_AS}" "${dev_file}"
  chmod 0640 "${dev_file}"
  log "wrote ${dev_file}"
}

render_and_install_services() {
  local unit
  for unit in safebath safebath-fusion safebath-worker; do
    log "installing ${unit}.service"
    sed -e "s#@INSTALL_DIR@#${INSTALL_DIR}#g" \
        -e "s#@DATA_DIR@#${DATA_DIR}#g" \
        -e "s#@RUN_AS@#${RUN_AS}#g" \
        -e "s#@ETC_DIR@#${ETC_DIR}#g" \
        "${SCRIPT_DIR}/${unit}.service" > "/etc/systemd/system/${unit}.service"
    chmod 0644 "/etc/systemd/system/${unit}.service"
    systemctl enable "${unit}"
  done
  systemctl daemon-reload
}

print_summary() {
  local pi_ip
  pi_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  cat <<EOF

============================================================
 SafeBath AI installed
  - install dir : ${INSTALL_DIR}
  - data dir    : ${DATA_DIR}
  - env file    : ${ETC_DIR}/safebath.env  (0600)
  - service     : sudo systemctl status safebath safebath-fusion safebath-worker
  - logs        : journalctl -u safebath -f        (API / ingestion)
                  journalctl -u safebath-fusion -f (fusion -> emergencies)
                  journalctl -u safebath-worker -f (cloud sync + web push)
  - device reg  : ${ETC_DIR}/devices.json
EOF
  if [[ -n "${pi_ip}" ]]; then
    echo "  - board URL   : http://${pi_ip}:8000/api/v1/telemetry (get IP anytime with \`hostname -I\`)"
  else
    echo "  - board URL   : http://<this-pi-ip>:8000/api/v1/telemetry (\`hostname -I\`)"
  fi
  cat <<EOF

 Boards are flashed with the Arduino IDE on a PC using firmware/common;
 the Pi itself only needs the HTTP API, no Arduino toolchain.
============================================================
EOF
}

main() {
  require_root
  detect_python_and_arch
  create_user_and_dirs
  sync_files
  make_venv
  install_packages
  write_env
  write_devices
  render_and_install_services
  if [[ "${1:-}" != "--no-start" ]]; then
    systemctl restart safebath safebath-fusion safebath-worker
    sleep 1
    for u in safebath safebath-fusion safebath-worker; do
      systemctl is-active --quiet "${u}" || die "${u} failed to start — check: journalctl -u ${u} -n 50"
      log "service ${u} active"
    done
  else
    log "skipped start (--no-start)"
  fi
  print_summary
}

# Allow update.sh to source this file for its helper functions only.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi