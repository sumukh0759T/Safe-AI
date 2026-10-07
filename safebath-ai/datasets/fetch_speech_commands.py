"""
Fetches the Google Speech Commands v0.02 dataset (official TensorFlow-hosted
mirror) for Module 10 Voice Bridge keyword spotting.
~2.4 GB tarball — cache once under data/raw/speech_commands/, it is not
re-downloaded if present.
"""
import os, tarfile, urllib.request

URL = ("https://storage.googleapis.com/download.tensorflow.org/data/"
       "speech_commands_v0.02.tar.gz")
OUT_DIR = "data/raw/speech_commands"
TAR_PATH = "data/raw/speech_commands_v0.02.tar.gz"


def fetch():
    os.makedirs(OUT_DIR, exist_ok=True)
    if not os.path.exists(TAR_PATH):
        print("Downloading Speech Commands v0.02 (~2.4 GB, one-time)...")
        urllib.request.urlretrieve(URL, TAR_PATH)
    print("Extracting...")
    with tarfile.open(TAR_PATH, "r:gz") as t:
        t.extractall(OUT_DIR)
    print(f"Extracted to {OUT_DIR}. Training relabels: yes->fine, stop->help, "
          "other words -> unknown (see training/train_voice_kws.py).")


if __name__ == "__main__":
    fetch()
