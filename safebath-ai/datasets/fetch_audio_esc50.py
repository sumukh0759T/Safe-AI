"""
Fetches ESC-50 (environmental sound classification, CC-BY licensed).
Source: https://github.com/karoldvl/ESC-50  (mirrored at karolpiczak/ESC-50)

NOTE (Master Guide Section 6): under the analog-mic plan these clips are NOT
YAMNet-embedded — they are re-featurized with datasets/audio_features.py
(RMS/ZCR/centroid) to match what Board 4 actually computes.
Verify the URL resolves before relying on it in an offline/venue environment —
download once ahead of time and cache under data/raw/esc50/.
"""
import os, zipfile, urllib.request

URL = "https://github.com/karolpiczak/ESC-50/archive/refs/heads/master.zip"
OUT_DIR = "data/raw/esc50"
ZIP_PATH = "data/raw/esc50_master.zip"

RELEVANT_CLASSES = ["water_drops", "pouring_water", "toilet_flush", "footsteps", "door_wood_knock", "glass_breaking"]


def fetch():
    os.makedirs(OUT_DIR, exist_ok=True)
    if not os.path.exists(ZIP_PATH):
        print("Downloading ESC-50...")
        urllib.request.urlretrieve(URL, ZIP_PATH)
    with zipfile.ZipFile(ZIP_PATH) as z:
        z.extractall(OUT_DIR)
    print(f"Extracted to {OUT_DIR}. Filter meta/esc50.csv for: {RELEVANT_CLASSES}")


if __name__ == "__main__":
    fetch()
