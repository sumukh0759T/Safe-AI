"""
Module 10 — Voice Bridge keyword spotting (micro_speech-style).

Input : Google Speech Commands v0.02 (run datasets/fetch_speech_commands.py).
        Relabeling toward the 3-way intent set (SOFTWARE_BUILD_SPEC 5.10):
            yes  -> fine
            stop -> help          (closest command-word proxy for a distress call)
            any other word -> unknown
            _background_noise_ -> silence (4th class so the model can abstain)
        On-device vocabulary gets few-shot recalibrated with the actual elder's
        "fine"/"help" after Phase D/E bring-up — standard practice.
Arch  : small conv on log-mel spectrograms (micro_speech style), int8-quantized
Output: models/voice_kws_model.tflite (<20KB target) + voice_kws_labels.json
"""
import json
import os

# keep training/conversion consistent with convert_to_tflite.py (see there)
os.environ.setdefault("TF_USE_LEGACY_KERAS", "1")

import numpy as np

DATA_DIR = "data/raw/speech_commands"
WORDS = ["yes", "stop", "up", "down", "left", "right", "on", "off", "go",
         "no", "one", "two", "three", "four", "five", "nine", "tree", "bed",
         "bird", "cat", "dog", "happy", "house", "marvin", "sheila", "wow",
         "zero", "seven", "eight", "six"]
RELABEL = {"yes": "fine", "stop": "help"}
CLASSES = ["fine", "help", "unknown", "silence"]
SR = 16000
DURATION = 1.0
N_MEL = 40
MAX_FILES_PER_WORD = 400      # keeps training minutes-scale on a laptop
TARGET_KB = 20


def log_mel(wave):
    import tensorflow as tf
    wave = tf.cast(tf.reshape(wave, [-1]), tf.float32)
    stfts = tf.signal.stft(wave, frame_length=480, frame_step=320)
    spectrograms = tf.abs(stfts)
    num_spectrogram_bins = spectrograms.shape[-1]
    linear_to_mel_weight_matrix = tf.signal.linear_to_mel_weight_matrix(
        num_mel_bins=N_MEL, num_spectrogram_bins=num_spectrogram_bins,
        sample_rate=SR, lower_edge_hertz=80.0, upper_edge_hertz=7600.0)
    mel = tf.matmul(spectrograms, linear_to_mel_weight_matrix)
    return tf.math.log(mel + 1e-6)


def load_dataset():
    import tensorflow as tf
    X, y = [], []
    bg = os.path.join(DATA_DIR, "_background_noise_")
    if os.path.isdir(bg):
        for f in os.listdir(bg)[:2]:
            wav, _ = tf.audio.decode_wav(
                tf.io.read_file(os.path.join(bg, f)), desired_channels=1)
            w = wav.numpy().flatten()
            step = SR
            for i in range(0, len(w) - step, step * 5):
                X.append(log_mel(w[i:i + step]).numpy())
                y.append(CLASSES.index("silence"))
    for word in WORDS:
        d = os.path.join(DATA_DIR, word)
        if not os.path.isdir(d):
            continue
        label = RELABEL.get(word,
                            "unknown" if word not in RELABEL else word)
        li = CLASSES.index(label)
        files = os.listdir(d)[:MAX_FILES_PER_WORD]
        for f in files:
            try:
                wav, _ = tf.audio.decode_wav(
                    tf.io.read_file(os.path.join(d, f)), desired_channels=1,
                    desired_samples=SR)
                X.append(log_mel(wav.numpy().flatten()).numpy())
                y.append(li)
            except Exception:
                continue
    return np.array(X, dtype=np.float32)[..., None], np.array(y)


def build_model(input_shape):
    import tensorflow as tf
    m = tf.keras.Sequential([
        tf.keras.layers.Input(shape=input_shape),
        tf.keras.layers.Conv2D(8, (3, 3), strides=(2, 2), activation="relu"),
        tf.keras.layers.MaxPooling2D((2, 2)),
        tf.keras.layers.Conv2D(8, (3, 3), activation="relu"),
        tf.keras.layers.GlobalAveragePooling2D(),
        tf.keras.layers.Dense(len(CLASSES), activation="softmax"),
    ])
    m.compile(optimizer="adam", loss="sparse_categorical_crossentropy",
              metrics=["accuracy"])
    return m


def main():
    try:
        import tensorflow as tf
    except ImportError:
        raise SystemExit("tensorflow not installed — pip install tensorflow")
    if not os.path.isdir(DATA_DIR) or not os.listdir(DATA_DIR):
        raise SystemExit(f"{DATA_DIR} empty — run datasets/fetch_speech_commands.py")

    X, y = load_dataset()
    print(f"dataset: {X.shape}, classes {dict(zip(*np.unique(y, return_counts=True)))}")

    model = build_model(X.shape[1:])
    model.fit(X, y, validation_split=0.15, epochs=12, batch_size=64,
              callbacks=[tf.keras.callbacks.EarlyStopping(
                  patience=3, restore_best_weights=True)])

    from sklearn.model_selection import train_test_split
    _, Xte, _, yte = train_test_split(X, y, test_size=0.2, stratify=y,
                                      random_state=42)
    pred = model.predict(Xte, verbose=0).argmax(axis=1)
    acc = float((pred == yte).mean())
    print(f"hold-out accuracy: {acc:.3f}")
    for ci, name in enumerate(CLASSES):
        mask = yte == ci
        if mask.sum():
            print(f"  recall[{name:8s}] {(pred[mask] == ci).mean():.3f}")

    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]   # dynamic-range int8
    tflite = converter.convert()

    os.makedirs("models", exist_ok=True)
    out = "models/voice_kws_model.tflite"
    with open(out, "wb") as f:
        f.write(tflite)
    with open("models/voice_kws_labels.json", "w") as f:
        json.dump(CLASSES, f)
    kb = len(tflite) / 1024
    print(f"saved {out} ({kb:.1f} KB)")
    if kb > TARGET_KB:
        print(f"WARNING: above {TARGET_KB}KB target — shrink filters/stride")


if __name__ == "__main__":
    main()
