#!/usr/bin/env bash
#
# SafeBath AI — health check for the Raspberry Pi deployment.
#
#   sudo ./health_check.sh             # brief: PASS/FAIL summary
#   sudo ./health_check.sh --verbose   # per-check detail
#
# Exit codes: 0 = healthy, 1 = unhealthy, 2 = not installed / misconfigured.
#
# Uses 127.0.0.1 (loopback) only — no IP address is hardcoded anywhere.
# Reads the install-time config and the 0600 env file, so run with sudo.
# Suited for a root cron/systemd timer:
#   */5 * * * *  root /opt/safebath-ai/deployment/raspberry_pi/health_check.sh \
#                    || logger -t safebath-health "unhealthy"

set -uo pipefail

CONF_FILE="/etc/safebath/install.conf"
VERBOSE=0
[[ "${1:-}" == "--verbose" ]] && VERBOSE=1

if [[ "${EUID}" -ne 0 ]]; then
  echo "safebath-health: run with sudo (needs to read ${CONF_FILE} / safebath.env)" >&2
  exit 2
fi

if [[ ! -f "${CONF_FILE}" ]]; then
  echo "safebath-health: not installed yet — run install.sh first (missing ${CONF_FILE})" >&2
  exit 2
fi

# shellcheck source=install.conf
source "${CONF_FILE}"   # INSTALL_DIR, DATA_DIR, ETC_DIR, SERVICE_NAME, PYTHON

failures=0
ok()  { [[ "${VERBOSE}" -eq 1 ]] && printf 'OK   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; failures=$((failures + 1)); }

# 1. systemd services active (API + fusion + sync worker)
for svc in "${SERVICE_NAME}" "${SERVICE_NAME}-fusion" "${SERVICE_NAME}-worker"; do
  if systemctl is-active --quiet "${svc}"; then
    ok "systemd service ${svc} active"
  else
    bad "systemd service ${svc} not active"
  fi
done

# 2. read API_PORT / SQLITE_PATH from the 0600 env file
API_PORT=8000
DB_PATH="${DATA_DIR}/events.db"
if [[ -r "${ETC_DIR}/safebath.env" ]]; then
  while IFS='=' read -r key val; do
    case "${key}" in
      API_PORT)     API_PORT="${val:-8000}" ;;
      SQLITE_PATH)  DB_PATH="${val:-}" ;;
    esac
  done < <(grep -E '^[A-Z_]+=' "${ETC_DIR}/safebath.env" || true)
fi

base="http://127.0.0.1:${API_PORT}"

if [[ -x "${INSTALL_DIR}/venv/bin/python" ]]; then
  PYTHON="${INSTALL_DIR}/venv/bin/python"
else
  PYTHON="python3"
fi

# 3. API + SQLite checks (stdlib urllib + sqlite3; venv python is guaranteed)
"${PYTHON}" - "$base" "$DB_PATH" "$VERBOSE" <<'PYEOF'
import json
import sqlite3
import sys
import time
import urllib.request

base, db_path, verbose = sys.argv[1], sys.argv[2], int(sys.argv[3])
fail = 0

def ok(msg):
    if verbose:
        print("OK   %s" % msg)

def bad(msg):
    global fail
    print("FAIL %s" % msg)
    fail += 1

for path in ("/api/v1/health", "/api/latest"):
    try:
        with urllib.request.urlopen(base + path, timeout=5) as r:
            body = json.loads(r.read() or b"{}")
        if path == "/api/v1/health":
            if r.status == 200 and body.get("status") == "ok":
                ok("GET %s (status ok)" % path)
            else:
                bad("GET %s -> HTTP %s %s" % (path, r.status, body))
        else:
            if r.status == 200 and "risk_score" in body:
                ok("GET %s (risk_score=%s, band=%s)" %
                   (path, body.get("risk_score"), body.get("band")))
            else:
                bad("GET %s -> HTTP %s (contract missing risk_score)" % (path, r.status))
    except Exception as e:
        bad("GET %s (%s)" % (path, e))

# system status: cloud is optional, so a configured-but-down cloud is a WARN
# (local safety must keep working); active emergencies are surfaced loudly.
try:
    with urllib.request.urlopen(base + "/api/system/status", timeout=5) as r:
        status = json.loads(r.read() or b"{}")
    cloud = status.get("cloud") or {}
    if not cloud.get("configured"):
        ok("cloud sync not configured (offline-only mode)")
    elif cloud.get("reachable"):
        ok("cloud reachable (last_check=%s)" % cloud.get("last_check"))
    else:
        print("WARN cloud configured but unreachable (%s)" % cloud.get("reason"))
    emg = (status.get("emergencies") or {}).get("counts") or {}
    active = emg.get("active", 0)
    if active:
        print("WARN %s active emergenc(ies) not yet acknowledged" % active)
    else:
        ok("no active emergencies")
except Exception as e:
    bad("/api/system/status (%s)" % e)

try:
    con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True, timeout=3)
    row = con.execute("PRAGMA quick_check").fetchone()
    if row and str(row[0]).startswith("ok"):
        ok("sqlite quick_check ok (%s)" % db_path)
    else:
        bad("sqlite quick_check -> %s" % (row,))
    if con.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='events'").fetchone()[0]:
        n = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        ok("events table (%d rows)" % n)
    if con.execute("SELECT COUNT(*) FROM sqlite_master "
                   "WHERE name='telemetry_heartbeats'").fetchone()[0]:
        hb = con.execute("SELECT MAX(ts) FROM telemetry_heartbeats").fetchone()[0]
        if hb:
            age = time.time() - hb
            if age > 24 * 3600:
                print("WARN no board heartbeat in %.0fh (boards offline?)" % (age / 3600.0))
            else:
                ok("latest heartbeat %.1fh ago" % (age / 3600.0))
    con.close()
except Exception as e:
    bad("sqlite (%s)" % e)
sys.exit(1 if fail else 0)
PYEOF
code=$?

if [[ "${failures}" -eq 0 && "${code}" -eq 0 ]]; then
  [[ "${VERBOSE}" -eq 1 ]] && echo "safebath-health: PASS"
  exit 0
fi
echo "safebath-health: check(s) failed"
exit 1