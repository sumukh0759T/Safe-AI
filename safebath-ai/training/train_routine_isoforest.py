"""
Module 9 — Routine Anomaly Isolation Forest.

Reads REAL sensor/mat/pir entry timestamps accumulated in data/events.db by the
running system. Explicitly NOT synthetic — faking this would defeat its purpose
(personal baseline). Fewer than ~50 real events => graceful no-op with a clear
log message (SOFTWARE_BUILD_SPEC 5.9 DoD).

Features: hour-of-day, gap-since-last-visit, visit-duration (entry->exit pairs).
Output: models/routine_isoforest.pkl + routine_baseline.json
"""
import json
import os

MIN_EVENTS = 50


def extract_visit_features(conn):
    rows = conn.execute(
        "SELECT ts, payload FROM events WHERE topic='sensor/mat/pir' "
        "ORDER BY ts").fetchall()
    entries = []
    for r in rows:
        try:
            import json as _json
            entry = bool(_json.loads(r["payload"]).get("entry"))
            entries.append((float(r["ts"]), entry))
        except Exception:
            continue

    feats, open_entry_ts, prev_exit_ts = [], None, None
    for ts, entry in entries:
        if entry:
            if open_entry_ts is None:
                open_entry_ts = ts
        else:
            if open_entry_ts is not None:
                duration = ts - open_entry_ts
                gap = (open_entry_ts - prev_exit_ts) \
                    if prev_exit_ts is not None else -1.0
                hour = ((open_entry_ts % 86400) / 3600.0)
                feats.append([hour, gap, duration])
                prev_exit_ts = ts
                open_entry_ts = None
    return feats


def main(db_path="data/events.db"):
    from sklearn.ensemble import IsolationForest
    import numpy as np

    from backend.db import get_conn, init_db
    init_db(db_path)
    conn = get_conn(db_path)

    n_events = conn.execute(
        "SELECT COUNT(*) FROM events WHERE topic='sensor/mat/pir'").fetchone()[0]
    feats = extract_visit_features(conn)
    print(f"[routine] pir events: {n_events}, completed visit pairs: {len(feats)}")
    if len(feats) < MIN_EVENTS:
        print(f"[routine] only {len(feats)} real visits so far (<{MIN_EVENTS}) — "
              "no-op. This module trains on live runtime data after a few days; "
              "do NOT substitute synthetic data.")
        return

    X = np.array(feats, dtype=np.float32)
    iso = IsolationForest(contamination=0.1, random_state=42)
    iso.fit(X)

    import joblib
    os.makedirs("models", exist_ok=True)
    joblib.dump(iso, "models/routine_isoforest.pkl")
    baseline = {
        "n_visits": int(len(X)),
        "hour_mean": float(X[:, 0].mean()),
        "gap_median_s": float(np.median(X[X[:, 1] >= 0][:, 1])) if (X[:, 1] >= 0).any() else None,
        "duration_median_s": float(np.median(X[:, 2])),
    }
    with open("models/routine_baseline.json", "w") as f:
        json.dump(baseline, f, indent=2)
    print(f"[routine] saved models/routine_isoforest.pkl + baseline {baseline}")


if __name__ == "__main__":
    main()
