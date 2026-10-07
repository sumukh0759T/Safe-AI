"""
Shared audio feature extractor — the single source of truth for Module 4.

Master Guide Section 5.1: Board 4 (MAX4466 analog mic) computes RMS energy,
zero-crossing rate and a coarse spectral centroid per ~1s window; training data
must go through the *exact same math* so there is no train/inference skew
(Master Guide Section 6 definition of done).

Firmware equivalent: firmware/audio_node/audio_node.ino — keep both in sync.
Both sides:
  - RMS over the whole window
  - ZCR = mean(sign changes) over the whole window
  - spectral centroid = mean of per-256-sample-chunk Hann-windowed FFT centroids
"""
import numpy as np

SR = 8000            # firmware samples at ~8 kHz via ADC
CHUNK = 256          # FFT chunk size (arduinoFFT-friendly power of two)


def extract_features(wave, sr=SR):
    """Returns (rms, zcr, centroid_hz) as plain floats."""
    wave = np.asarray(wave, dtype=np.float64)
    if wave.size == 0:
        return 0.0, 0.0, 0.0
    eps = 1e-9

    rms = float(np.sqrt(np.mean(wave ** 2)))

    zcr = float(np.mean(np.abs(np.diff(np.sign(wave))) > 0))

    n_chunks = max(1, len(wave) // CHUNK)
    centroids = []
    window = np.hanning(CHUNK)
    freqs = np.fft.rfftfreq(CHUNK, d=1.0 / sr)
    for c in range(n_chunks):
        seg = wave[c * CHUNK:(c + 1) * CHUNK]
        if len(seg) < CHUNK:
            break
        spec = np.abs(np.fft.rfft(seg * window))
        denom = spec.sum()
        if denom > eps:
            centroids.append(float((spec * freqs).sum() / denom))
    centroid = float(np.mean(centroids)) if centroids else 0.0

    return rms, zcr, centroid


def features_matrix(waves, sr=SR):
    """(N, sr) array -> (N, 3) feature matrix."""
    return np.array([extract_features(w, sr) for w in waves], dtype=np.float32)
