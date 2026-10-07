"""
Fetches RAVDESS (emotional speech/song, CC-BY-NC-SA), used only for
'speech_or_cry' reference clips under the feature-based Module 4 plan.

OPTIONAL under the final analog-mic wiring (Master Guide Section 6): the
YAMNet-embedding approach this dataset was originally fetched for is retired;
clips are re-featurized via datasets/audio_features.py like everything else.
Source (Zenodo): https://zenodo.org/record/1188976 — cache locally ahead of
any offline demo day.
"""
import os, zipfile, urllib.request

URL = "https://zenodo.org/record/1188976/files/Audio_Speech_Actors_01-24.zip"
OUT_DIR = "data/raw/ravdess"
ZIP_PATH = "data/raw/ravdess.zip"


def fetch():
    os.makedirs(OUT_DIR, exist_ok=True)
    if not os.path.exists(ZIP_PATH):
        print("Downloading RAVDESS speech subset...")
        urllib.request.urlretrieve(URL, ZIP_PATH)
    with zipfile.ZipFile(ZIP_PATH) as z:
        z.extractall(OUT_DIR)
    print(f"Extracted to {OUT_DIR}. Use 'fearful' and 'sad' emotion codes as speech_or_cry proxies.")


if __name__ == "__main__":
    fetch()
