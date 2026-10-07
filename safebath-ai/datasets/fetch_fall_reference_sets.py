"""
Fetches SisFall (reference only — see SOFTWARE_BUILD_SPEC Section 3.1, NOT used
as direct training input for the pressure-mat model, different sensor modality).
Useful only for sanity-checking what collapse-trajectory timing/shape looks like.
Source: http://sistemic.udea.edu.co/en/investigacion/proyectos/english-falls/
This host has historically been slow/unstable — treat as optional, skip if
unavailable; the mat model does not depend on this file existing.
"""
import os, urllib.request

URL = "http://sistemic.udea.edu.co/wp-content/uploads/2017/04/SisFall_dataset.zip"
OUT_PATH = "data/raw/sisfall.zip"


def fetch():
    os.makedirs("data/raw", exist_ok=True)
    try:
        urllib.request.urlretrieve(URL, OUT_PATH)
        print("SisFall reference set downloaded (optional, reference-only).")
    except Exception as e:
        print(f"Skipping SisFall fetch (optional): {e}")


if __name__ == "__main__":
    fetch()
