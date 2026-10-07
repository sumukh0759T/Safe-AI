"""
Module 6 — Thermal Scald/Freeze CNN+LSTM on the AMG8833 (8x8).

Input : data/synthetic/thermal_synth_X.npy  (N, 15, 8, 8) °C frames, labels 0/1
Arch  : TimeDistributed(Conv2D(8,(3,3))) -> TimeDistributed(Flatten)
        -> LSTM(16) -> Dense(2 softmax)
Output: models/thermal_freeze_model.h5

DoD (SOFTWARE_BUILD_SPEC 5.6): validation accuracy >= 0.85.
"""
import os

# keep training/conversion consistent with convert_to_tflite.py (see there)
os.environ.setdefault("TF_USE_LEGACY_KERAS", "1")

import numpy as np

X_PATH = "data/synthetic/thermal_synth_X.npy"
Y_PATH = "data/synthetic/thermal_synth_y.npy"
OUT = "models/thermal_freeze_model.h5"
TARGET_ACC = 0.85


def build_model():
    import tensorflow as tf
    m = tf.keras.Sequential([
        tf.keras.layers.Input(shape=(15, 8, 8)),
        tf.keras.layers.Reshape((15, 8, 8, 1)),
        tf.keras.layers.TimeDistributed(
            tf.keras.layers.Conv2D(8, (3, 3), activation="relu", padding="same")),
        tf.keras.layers.TimeDistributed(tf.keras.layers.Flatten()),
        tf.keras.layers.LSTM(16),
        tf.keras.layers.Dense(2, activation="softmax"),
    ])
    m.compile(optimizer="adam", loss="sparse_categorical_crossentropy",
              metrics=["accuracy"])
    return m


def main():
    if not os.path.exists(X_PATH):
        raise SystemExit(f"{X_PATH} missing — run datasets/gen_thermal_data.py first")
    try:
        import tensorflow as tf
    except ImportError:
        raise SystemExit("tensorflow not installed — pip install tensorflow")

    X = np.load(X_PATH).astype(np.float32)
    y = np.load(Y_PATH)
    X = np.clip((X - 20.0) / 30.0, -1.0, 2.0)   # normalize °C to ~[-1, 2]

    model = build_model()
    model.summary()
    model.fit(X, y, validation_split=0.15, epochs=10, batch_size=32,
              callbacks=[tf.keras.callbacks.EarlyStopping(
                  patience=3, restore_best_weights=True)])

    n = int(len(X) * 0.85)
    loss, acc = model.evaluate(X[n:], y[n:], verbose=0)
    print(f"hold-out accuracy: {acc:.3f}")
    if acc < TARGET_ACC:
        print(f"WARNING: below {TARGET_ACC} DoD target")

    os.makedirs("models", exist_ok=True)
    model.save(OUT)
    print(f"saved {OUT}")


if __name__ == "__main__":
    main()
