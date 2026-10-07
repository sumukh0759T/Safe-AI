"""
Evaluates every trained artifact present in models/ against its Definition-of-
Done metric and prints one reviewable summary table. Missing artifacts are
reported as "not trained" — never crash, never fake numbers.
"""
import json
import os

# keep evaluation consistent with convert_to_tflite.py (see there)
os.environ.setdefault("TF_USE_LEGACY_KERAS", "1")

import numpy as np

CLASS_NAMES_AUDIO = ["silence", "water", "impact_thud", "speech_or_cry"]
FLOOR = {"audio_recall": 0.70, "fall_acc": 0.85, "toilet_acc": 0.80,
         "thermal_acc": 0.85}
results = []


def record(name, metric, value, target):
    ok = None if value is None else value >= target
    results.append((name, metric, value, target, ok))


def eval_toilet():
    import joblib
    from sklearn.model_selection import train_test_split
    if not os.path.exists("models/toilet_rf_model.pkl"):
        return record("toilet_rf", "accuracy", None, FLOOR["toilet_acc"])
    df = __import__("pandas").read_csv("data/synthetic/toilet_synth_data.csv")
    X, y = df[["gas_ppm", "voc_ppm", "turbidity"]].values, df["label"].values
    _, Xte, _, yte = train_test_split(X, y, test_size=0.2, stratify=y,
                                      random_state=42)
    clf = joblib.load("models/toilet_rf_model.pkl")
    record("toilet_rf", "accuracy", float(clf.score(Xte, yte)), FLOOR["toilet_acc"])


def eval_fall():
    from sklearn.model_selection import train_test_split
    if not os.path.exists("models/fall_risk_model.h5"):
        return record("fall_convlstm", "accuracy", None, FLOOR["fall_acc"])
    import tensorflow as tf
    X = np.load("data/synthetic/mat_synth_X.npy").astype(np.float32) / 4095.0
    y = np.load("data/synthetic/mat_synth_y.npy")
    _, Xte, _, yte = train_test_split(X, y, test_size=0.2, stratify=y,
                                      random_state=42)
    m = tf.keras.models.load_model("models/fall_risk_model.h5")
    pred = m.predict(Xte, verbose=0).argmax(axis=1)
    record("fall_convlstm", "accuracy", float((pred == yte).mean()),
           FLOOR["fall_acc"])


def eval_thermal():
    from sklearn.model_selection import train_test_split
    if not os.path.exists("models/thermal_freeze_model.h5"):
        return record("thermal_cnn_lstm", "accuracy", None, FLOOR["thermal_acc"])
    import tensorflow as tf
    X = np.clip((np.load("data/synthetic/thermal_synth_X.npy") - 20.0) / 30.0,
                -1.0, 2.0).astype(np.float32)
    y = np.load("data/synthetic/thermal_synth_y.npy")
    _, Xte, _, yte = train_test_split(X, y, test_size=0.2, stratify=y,
                                      random_state=42)
    m = tf.keras.models.load_model("models/thermal_freeze_model.h5")
    pred = m.predict(Xte, verbose=0).argmax(axis=1)
    record("thermal_cnn_lstm", "accuracy", float((pred == yte).mean()),
           FLOOR["thermal_acc"])


def eval_audio():
    from sklearn.model_selection import train_test_split
    if not os.path.exists("models/audio_head_model.h5"):
        return record("audio_head", "min_class_recall", None, FLOOR["audio_recall"])
    import tensorflow as tf
    sys_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    import sys
    if sys_path not in sys.path:
        sys.path.insert(0, sys_path)
    from datasets.audio_features import features_matrix
    Xf = features_matrix(np.load("data/synthetic/audio_synth_waves.npy"))
    y = np.load("data/synthetic/audio_synth_labels.npy")
    if os.path.exists("data/real/audio_real_features_X.npy"):
        rx = np.load("data/real/audio_real_features_X.npy")
        ry = np.load("data/real/audio_real_features_y.npy")
        if len(rx) > 1:
            Xf = np.concatenate([Xf, rx[:, :3].astype(np.float32)])
            y = np.concatenate([y, ry])
    with open("models/audio_head_model_scaler.json") as f:
        s = json.load(f)
    Xn = (Xf - np.array(s["mean"])) / np.array(s["std"])
    _, Xte, _, yte = train_test_split(Xn, y, test_size=0.2, stratify=y,
                                      random_state=42)
    m = tf.keras.models.load_model("models/audio_head_model.h5")
    pred = m.predict(Xte, verbose=0).argmax(axis=1)
    recalls = {CLASS_NAMES_AUDIO[ci]: float((pred[yte == ci] == ci).mean())
               for ci in range(4) if (yte == ci).sum()}
    worst_name = min(recalls, key=recalls.get)
    for k, v in recalls.items():
        print(f"  audio recall[{k}] = {v:.3f}")
    record("audio_head", f"min_class_recall ({worst_name})",
           recalls[worst_name], FLOOR["audio_recall"])


def main():
    print("=" * 72)
    print("SafeBath AI — evaluate_all")
    print("=" * 72)
    eval_toilet()
    eval_fall()
    eval_thermal()
    eval_audio()

    tflite_files = sorted(__import__("glob").glob("models/*.tflite"))
    print(f"\ntflite artifacts: {[os.path.basename(f) for f in tflite_files]}")

    print("\n{:<22s} {:<28s} {:>10s} {:>8s} {:>8s}".format(
        "model", "metric", "value", "target", "pass?"))
    print("-" * 72)
    for name, metric, value, target, ok in results:
        v = f"{value:.3f}" if isinstance(value, float) else ("—" if value is None else str(value))
        oks = {True: "PASS", False: "FAIL", None: "—"}[ok]
        print("{:<22s} {:<28s} {:>10s} {:>8.2f} {:>8s}".format(
            name, metric, v, target, oks))
    print("-" * 72)


if __name__ == "__main__":
    main()
