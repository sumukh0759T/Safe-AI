"""
Converts fetched real audio (ESC-50 subsets) into the same RMS/ZCR/centroid
feature space Board 4 firmware computes, producing a training set that
concatenates cleanly with gen_audio_data.py's synthetic output.

RETIRED APPROACH (kept for traceability): this script previously produced
YAMNet embeddings — that assumed the digital I2S INMP441 mic. The final wiring
uses an analog MAX4466, so per Master Guide Sections 5.1/6 all clips are
re-featurized with datasets/audio_features.py instead.

Label space matches sensor/audio/event:
  0=silence, 1=water, 2=impact_thud(proxy: knock/glass), 3=speech_or_cry
"""
import csv
import glob
import os

import numpy as np

try:
    from .audio_features import extract_features
except ImportError:                       # direct script execution
    from audio_features import extract_features

try:
    import librosa
except ImportError:
    librosa = None

LABEL_MAP = {
    "water_drops": 1, "pouring_water": 1, "toilet_flush": 1,   # -> water
    "door_wood_knock": 2, "glass_breaking": 2,                  # -> thud proxies
}


def load_and_resample(path, sr=8000):
    if librosa is None:
        raise RuntimeError("librosa not installed — needed to decode real audio")
    y, _ = librosa.load(path, sr=sr, mono=True, duration=1.0)
    if len(y) < sr:
        y = np.pad(y, (0, sr - len(y)))
    return y


def build_from_esc50(esc50_dir):
    X, y = [], []
    meta_path = os.path.join(esc50_dir, "ESC-50-master", "meta", "esc50.csv")
    audio_dir = os.path.join(esc50_dir, "ESC-50-master", "audio")
    if not os.path.exists(meta_path):
        print(f"ESC-50 metadata not found at {meta_path} — skipping real data "
              "(synthetic-only training will proceed).")
        return np.empty((0, 3)), np.empty(0)
    with open(meta_path) as f:
        for row in csv.DictReader(f):
            cls = row["category"]
            if cls in LABEL_MAP:
                wav = load_and_resample(os.path.join(audio_dir, row["filename"]))
                X.append(extract_features(wav))
                y.append(LABEL_MAP[cls])
    return np.array(X), np.array(y)


def build_from_ravdess(ravdess_dir, label=3):  # 3 = speech_or_cry
    X, y = [], []
    for path in glob.glob(os.path.join(ravdess_dir, "**", "*.wav"), recursive=True):
        emotion_code = path.split("-")[2] if "-" in os.path.basename(path) else None
        if emotion_code in ("06", "04"):  # fearful, sad
            wav = load_and_resample(path)
            X.append(extract_features(wav))
            y.append(label)
    return np.array(X), np.array(y)


def main(esc50_dir="data/raw/esc50", ravdess_dir="data/raw/ravdess"):
    Xe, ye = build_from_esc50(esc50_dir)
    Xr, yr = build_from_ravdess(ravdess_dir)
    parts_x = [x for x in (Xe, Xr) if len(x)]
    parts_y = [y for y in (ye, yr) if len(y)]
    if parts_x:
        X = np.concatenate(parts_x)
        y = np.concatenate(parts_y)
    else:
        X = np.empty((0, 3))
        y = np.empty(0)
    os.makedirs("data/real", exist_ok=True)
    np.save("data/real/audio_real_features_X.npy", X)
    np.save("data/real/audio_real_features_y.npy", y)
    print(f"Saved {len(X)} real-audio feature rows (0 => synthetic-only fallback).")


if __name__ == "__main__":
    main()
