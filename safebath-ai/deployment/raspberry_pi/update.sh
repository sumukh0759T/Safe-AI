#!/usr/bin/env bash
#
# SafeBath AI — update the Raspberry Pi deployment.
#
#   sudo ./update.sh
#
# Sources install.sh for its helpers (no code duplication), so this needs
# the same constraints: run as root. It
#   1. reads the install-time config from /etc/safebath/install.conf,
#   2. git-fetches/pulls the project (ff-only; skipped if not a git checkout),
#   3. re-syncs files + re-runs pip install (adds/upgrades dependencies),
#   4. re-renders the systemd unit (in case the template changed),
#   5. restarts the service and runs health_check.sh.
#
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CONF_FILE="/etc/safebath/install.conf"

# ---- load shared helpers ------------------------------------------------
# shellcheck source=install.sh
source "${SCRIPT_DIR}/install.sh"

require_root

[[ -f "${CONF_FILE}" ]] || die "not installed yet — run install.sh first (missing ${CONF_FILE})"
# shellcheck source=install.conf
source "${CONF_FILE}"    # overrides SAFEBATH_* globals with install-time values

if [[ -d "${REPO_DIR}/.git" ]]; then
  log "pulling latest (${REPO_DIR})"
  before="$(git -C "${REPO_DIR}" rev-parse --short HEAD)"
  git -C "${REPO_DIR}" fetch --prune origin
  git -C "${REPO_DIR}" pull --ff-only
  after="$(git -C "${REPO_DIR}" rev-parse --short HEAD)"
  log "commit ${before} -> ${after}"
else
  log "repo is not a git checkout — syncing files as-is"
fi

detect_python_and_arch   # recomputes PI_32BIT used by install_packages below
sync_files
install_packages
render_and_install_services

log "restarting safebath safebath-fusion safebath-worker"
systemctl daemon-reload
systemctl restart safebath safebath-fusion safebath-worker
sleep 1
for u in safebath safebath-fusion safebath-worker; do
  systemctl is-active --quiet "${u}" \
    || die "${u} failed to start — check: journalctl -u ${u} -n 50"
done

log "health check:"
"${SCRIPT_DIR}/health_check.sh" --verbose || true