"""
Module 1 — Fall Risk ConvLSTM on the confirmed 16x4 pressure grid.

Input : data/synthetic/mat_synth_X.npy  (N, 20, ROWS=16, COLS=4), labels 0/1/2
Arch  : Reshape -> ConvLSTM2D(8,(3,3)) -> Flatten -> Dense(16 relu) -> Dense(3 softmax)
Output: models/fall_risk_model.h5

DoD (SOFTWARE_BUILD_SPEC 5.1): validation accuracy >= 0.85 on synthetic hold-out,
converts cleanly via training/convert_to_tflite.py with tensor arena < 100KB.
"""
import os
import sys

# keep training/conversion consistent with convert_to_tflite.py (see there)
os.environ.setdefault("TF_USE_LEGACY_KERAS", "1")

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.schemas import MAT_COLS, MAT_ROWS  # grid geometry single source

X_PATH = "data/synthetic/mat_synth_X.npy"
Y_PATH = "data/synthetic/mat_synth_y.npy"
OUT = "models/fall_risk_model.h5"
TARGET_ACC = 0.85


def build_model():
    import tensorflow as tf
    m = tf.keras.Sequential([
        tf.keras.layers.Input(shape=(20, MAT_ROWS, MAT_COLS)),
        tf.keras.layers.Reshape((20, MAT_ROWS, MAT_COLS, 1)),
        tf.keras.layers.ConvLSTM2D(8, (3, 3), padding="same", activation="tanh"),
        tf.keras.layers.Flatten(),
        tf.keras.layers.Dense(16, activation="relu"),
        tf.keras.layers.Dense(3, activation="softmax"),
    ])
    m.compile(optimizer="adam", loss="sparse_categorical_crossentropy",
              metrics=["accuracy"])
    return m


def main():
    if not os.path.exists(X_PATH):
        raise SystemExit(f"{X_PATH} missing — run datasets/gen_mat_data.py first")
    X = np.load(X_PATH).astype(np.float32) / 4095.0   # normalize ADC range
    y = np.load(Y_PATH)

    try:
        import tensorflow as tf
    except ImportError:
        raise SystemExit("tensorflow not installed — pip install tensorflow")

    model = build_model()
    model.summary()
    model.fit(X, y, validation_split=0.15, epochs=12, batch_size=32,
              callbacks=[tf.keras.callbacks.EarlyStopping(
                  patience=3, restore_best_weights=True)])

    loss, acc = model.evaluate(X[int(len(X) * 0.85):],
                               y[int(len(y) * 0.85):], verbose=0)
    print(f"hold-out accuracy: {acc:.3f}")
    if acc < TARGET_ACC:
        print(f"WARNING: below {TARGET_ACC} DoD target — tune generators/model")

    os.makedirs("models", exist_ok=True)
    model.save(OUT)
    print(f"saved {OUT}")


if __name__ == "__main__":
    main()
