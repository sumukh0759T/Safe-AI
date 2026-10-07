"""
Module 4 — Acoustic Distress head, feature-based per the FINAL analog-mic plan.

The original YAMNet-embedding transfer-learning approach is retired: Board 4
now carries an analog MAX4466 (Master Guide Sections 0/5.1), so training and
inference both consume RMS/ZCR/spectral-centroid features computed by
datasets/audio_features.py — identical math to firmware/audio_node.ino.

Input : data/synthetic/audio_synth_waves.npy + labels
      + data/real/audio_real_features_*.npy when fetch scripts have run
Arch  : Dense(64 relu) -> Dense(4 softmax) on standardized 3-feature input
Output: models/audio_head_model.h5 (+ feature scaler stats json beside it)

DoD (SOFTWARE_BUILD_SPEC 5.4): per-class recall for all 4 classes; flag any
class recall < 0.7 (real/synthetic imbalance is the likeliest failure mode).
"""
import json
import os

# keep training/conversion consistent with convert_to_tflite.py (see there)
os.environ.setdefault("TF_USE_LEGACY_KERAS", "1")

import numpy as np

sys_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys
sys.path.insert(0, sys_path)

from datasets.audio_features import features_matrix

WAVES = "data/synthetic/audio_synth_waves.npy"
LABELS = "data/synthetic/audio_synth_labels.npy"
REAL_X = "data/real/audio_real_features_X.npy"
REAL_Y = "data/real/audio_real_features_y.npy"
OUT = "models/audio_head_model.h5"
CLASS_NAMES = ["silence", "water", "impact_thud", "speech_or_cry"]
RECALL_FLOOR = 0.70


def load_data():
    X = np.load(WAVES)
    y = np.load(LABELS)
    Xf = features_matrix(X)

    parts_x, parts_y = [Xf], [y]
    if os.path.exists(REAL_X):
        rx, ry = np.load(REAL_X), np.load(REAL_Y)
        if len(rx) == len(ry) and len(rx) > 1:
            # pad/truncate real rows to 3 cols defensively
            rx = rx[:, :3] if rx.shape[1] >= 3 else \
                np.pad(rx, ((0, 0), (0, 3 - rx.shape[1])))
            parts_x.append(rx.astype(np.float32))
            parts_y.append(ry)
    Xall = np.concatenate(parts_x)
    yall = np.concatenate(parts_y)
    return Xall, yall


def main():
    try:
        import tensorflow as tf
    except ImportError:
        raise SystemExit("tensorflow not installed — pip install tensorflow")
    if not os.path.exists(WAVES):
        raise SystemExit(f"{WAVES} missing — run datasets/gen_audio_data.py first")

    X, y = load_data()
    print(f"training rows: {len(X)} "
          f"({dict(zip(*np.unique(y, return_counts=True)))})")

    mean, std = X.mean(axis=0), X.std(axis=0) + 1e-6
    Xn = (X - mean) / std

    model = tf.keras.Sequential([
        tf.keras.layers.Input(shape=(3,)),
        tf.keras.layers.Dense(64, activation="relu"),
        tf.keras.layers.Dense(4, activation="softmax"),
    ])
    model.compile(optimizer="adam", loss="sparse_categorical_crossentropy",
                  metrics=["accuracy"])
    model.fit(Xn, y, validation_split=0.15, epochs=20, batch_size=32,
              callbacks=[tf.keras.callbacks.EarlyStopping(
                  patience=4, restore_best_weights=True)])

    from sklearn.model_selection import train_test_split
    _, Xte, _, yte = train_test_split(Xn, y, test_size=0.2, stratify=y,
                                      random_state=42)
    pred = model.predict(Xte, verbose=0).argmax(axis=1)

    print("per-class recall:")
    flagged = False
    for ci, name in enumerate(CLASS_NAMES):
        mask = yte == ci
        if mask.sum() == 0:
            continue
        recall = float((pred[mask] == ci).mean())
        mark = ""
        if recall < RECALL_FLOOR:
            mark = "  << BELOW 0.7 — check real/synthetic class balance"
            flagged = True
        print(f"  {name:14s} {recall:.3f}{mark}")
    if flagged:
        print("WARNING: class recall below floor — rebalance before demo")

    os.makedirs("models", exist_ok=True)
    model.save(OUT)
    with open(OUT.replace(".h5", "_scaler.json"), "w") as f:
        json.dump({"mean": mean.tolist(), "std": std.tolist()}, f, indent=2)
    print(f"saved {OUT} + scaler stats")


if __name__ == "__main__":
    main()
