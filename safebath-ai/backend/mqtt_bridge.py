"""
MQTT bridge -- subscribes to sensor/#, validates every payload against
backend/schemas.py BEFORE inserting into SQLite, and records malformed
payloads loudly in invalid_events instead of silently corrupting fusion.

Robustness features (v2):
  - Auto-reconnect with exponential backoff on disconnect
  - Compatible with paho-mqtt 1.x and 2.x
  - Graceful shutdown on SIGINT/SIGTERM
"""
import json
import os
import signal
import sqlite3
import ssl
import sys
import time
import traceback
from typing import Dict

# allow direct execution (python backend/mqtt_bridge.py) from any cwd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import schemas
from backend.db import DEFAULT_DB, get_conn, init_db

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None

# Reconnect settings
RECONNECT_DELAY_MIN = 1.0
RECONNECT_DELAY_MAX = 30.0
RECONNECT_BACKOFF = 1.5

_running = True


def _signal_handler(sig, frame):
    global _running
    _running = False
    print("\n[bridge] shutting down...")


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


def on_message(conn, topic: str, payload: str,
               _last_log: Dict[str, float] = {}, _counts: Dict[str, int] = {}
               ) -> bool:
    """Validate + insert. Returns True if accepted, False if rejected."""
    ts = time.time()
    try:
        schemas.parse_for_topic(topic, payload)
    except Exception as e:
        print(f"[bridge] REJECTED {topic}: {e}\n{traceback.format_exc()}",
              file=sys.stderr)
        try:
            conn.execute("INSERT INTO invalid_events(ts,topic,payload,error) "
                         "VALUES (?,?,?,?)", (ts, topic, payload, str(e)))
            conn.commit()
        except sqlite3.OperationalError:
            pass
        return False
    try:
        conn.execute("INSERT INTO events(ts,topic,payload) VALUES (?,?,?)",
                     (ts, topic, payload))
        conn.commit()
    except sqlite3.OperationalError as e:
        if "locked" in str(e):
            time.sleep(0.05)
            try:
                conn.execute("INSERT INTO events(ts,topic,payload) VALUES (?,?,?)",
                             (ts, topic, payload))
                conn.commit()
            except sqlite3.OperationalError:
                print(f"[bridge] DB locked, dropped {topic}", file=sys.stderr)
        else:
            raise

    _counts[topic] = _counts.get(topic, 0) + 1
    chatty = topic.startswith(("sensor/mat/", "sensor/audio/"))
    if not chatty or ts - _last_log.get(topic, 0.0) >= 2.0:
        _last_log[topic] = ts
        n = _counts[topic]
        extra = f" (+{n - 1} since last)" if chatty and n > 1 else ""
        print(f"[bridge] OK {topic}{extra} :: "
              f"{payload if len(payload) <= 120 else payload[:117] + '...'}")
    return True


def _make_client_v1(conn, host, port):
    """paho-mqtt 1.x or VERSION1 fallback."""
    client = mqtt.Client(client_id="safebath-bridge")

    def _on_connect(c, userdata, flags, rc):
        if rc == 0:
            print(f"[bridge] connected to {host}:{port}")
            c.subscribe("sensor/#")
            c.subscribe("voice/#")
        else:
            print(f"[bridge] connect failed rc={rc}, will retry...")

    def _on_disconnect(c, userdata, rc):
        if rc != 0:
            print(f"[bridge] disconnected (rc={rc}), reconnecting...")

    def _on_message(c, userdata, msg):
        try:
            text = msg.payload.decode("utf-8")
        except UnicodeDecodeError:
            text = msg.payload.decode("utf-8", errors="replace")
        for _attempt in range(5):
            try:
                on_message(conn, msg.topic, text)
                break
            except sqlite3.OperationalError as e:
                if "locked" in str(e):
                    time.sleep(0.05)
                    continue
                raise

    client.on_connect = _on_connect
    client.on_disconnect = _on_disconnect
    client.on_message = _on_message
    return client


def _make_client_v2(conn, host, port):
    """paho-mqtt 2.x modern API."""
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id="safebath-bridge",
    )

    def _on_connect(c, userdata, flags, reason_code, properties):
        if reason_code == 0:
            print(f"[bridge] connected to {host}:{port}")
            c.subscribe("sensor/#")
            c.subscribe("voice/#")
        else:
            print(f"[bridge] connect failed rc={reason_code}, will retry...")

    def _on_disconnect(c, userdata, flags, reason_code, properties):
        if reason_code != 0:
            print(f"[bridge] disconnected (rc={reason_code}), reconnecting...")

    def _on_message(c, userdata, msg):
        try:
            text = msg.payload.decode("utf-8")
        except UnicodeDecodeError:
            text = msg.payload.decode("utf-8", errors="replace")
        for _attempt in range(5):
            try:
                on_message(conn, msg.topic, text)
                break
            except sqlite3.OperationalError as e:
                if "locked" in str(e):
                    time.sleep(0.05)
                    continue
                raise

    client.on_connect = _on_connect
    client.on_disconnect = _on_disconnect
    client.on_message = _on_message
    return client


def make_client(conn, host: str, port: int):
    """Create MQTT client, auto-detecting paho-mqtt version."""
    try:
        return _make_client_v2(conn, host, port)
    except (TypeError, AttributeError):
        return _make_client_v1(conn, host, port)


def main() -> None:
    if mqtt is None:
        raise SystemExit("paho-mqtt not installed -- pip install paho-mqtt")
    host = os.environ.get("MQTT_BROKER_HOST", "localhost")
    port = int(os.environ.get("MQTT_BROKER_PORT", "1883"))
    db_path = os.environ.get("SQLITE_PATH", DEFAULT_DB)
    init_db(db_path)
    conn = get_conn(db_path)
    client = make_client(conn, host, port)

    # reconnect_delay_set for automatic backoff
    client.reconnect_delay_set(
        min_delay=RECONNECT_DELAY_MIN,
        max_delay=RECONNECT_DELAY_MAX,
    )

    print(f"[bridge] connecting to {host}:{port}, db={db_path}")

    while _running:
        try:
            client.connect(host, port, keepalive=60)
            client.loop_forever(timeout=1.0)
        except OSError as e:
            if not _running:
                break
            print(f"[bridge] connection error: {e}, retrying in 3s...")
            time.sleep(3)
        except Exception as e:
            if not _running:
                break
            print(f"[bridge] unexpected error: {e}")
            traceback.print_exc()
            time.sleep(3)

    print("[bridge] stopped")
    client.disconnect()


if __name__ == "__main__":
    main()
