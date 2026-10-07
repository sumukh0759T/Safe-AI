"""
Synthetic 1-second audio windows for Module 4 (analog MAX4466 mic plan).

Output waveforms are featurized with datasets/audio_features.py — the exact
math firmware/audio_node runs at inference — so there is no train/inference
skew (Master Guide Section 6 definition of done).

Classes: 0=silence, 1=water, 2=impact_thud, 3=speech_or_cry
"""
import numpy as np

try:
    from .audio_features import SR, extract_features
except ImportError:                       # direct script execution
    from audio_features import SR, extract_features

COLUMNS = ["rms", "zcr", "centroid", "label"]


def _lowpass(x, alpha):
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):
        acc += alpha * (v - acc)
        y[i] = acc
    return y


def make_clip(label, rng):
    n = SR  # 1 second @ 8 kHz
    t = np.arange(n) / SR

    if label == 0:      # silence: quiet ADC noise floor
        return (rng.normal(0, 0.006, n)).astype(np.float32)

    if label == 1:      # water: broadband hiss, slowly modulated amplitude
        noise = rng.normal(0, 1, n)
        hiss = _lowpass(noise, 0.25)
        hiss -= hiss.mean()
        mod = 0.55 + 0.45 * np.abs(np.sin(2 * np.pi * rng.uniform(2, 5) * t))
        return ((hiss / (np.abs(hiss).max() + 1e-9)) * mod *
                rng.uniform(0.35, 0.8)).astype(np.float32)

    if label == 2:      # impact thud: damped low-frequency burst near start
        f = rng.uniform(60, 120)
        tau = rng.uniform(0.05, 0.12)
        start = int(rng.uniform(0.05, 0.15) * SR)
        env = np.exp(-t / tau) * (t >= t[start])
        thud = np.sin(2 * np.pi * f * t + rng.uniform(0, 6)) * env
        thud += _lowpass(rng.normal(0, 1, n), 0.05) * 0.3 * env
        peak = np.abs(thud).max() + 1e-9
        return ((thud / peak) * rng.uniform(0.7, 1.0)).astype(np.float32)

    # speech_or_cry: harmonic stack with pitch wobble + syllable envelope
    f0 = rng.uniform(160, 340)
    wob = 1 + 0.08 * np.sin(2 * np.pi * rng.uniform(3, 7) * t)
    sig = sum(np.sin(2 * np.pi * f0 * wob * h * t + rng.uniform(0, 6)) / h
              for h in (1, 2, 3))
    syllables = rng.uniform(2, 5)
    gate = (np.sin(2 * np.pi * syllables * t - np.pi / 2) > -0.2).astype(float)
    gate = _lowpass(gate, 0.2)
    sig = sig * gate
    return ((sig / (np.abs(sig).max() + 1e-9)) *
            rng.uniform(0.5, 0.95)).astype(np.float32)


def generate(n_per_class=300, seed=42):
    rng = np.random.default_rng(seed)
    waves = np.concatenate([[make_clip(l, rng) for _ in range(n_per_class)]
                            for l in range(4)])
    labels = np.repeat([0, 1, 2, 3], n_per_class)
    return waves.astype(np.float32), labels.astype(np.int64)


def main(out_dir="data/synthetic"):
    import os
    os.makedirs(out_dir, exist_ok=True)
    waves, labels = generate()
    np.save(os.path.join(out_dir, "audio_synth_waves.npy"), waves)
    np.save(os.path.join(out_dir, "audio_synth_labels.npy"), labels)

    feats = [extract_features(w) for w in waves]
    import pandas as pd
    df = pd.DataFrame(feats, columns=COLUMNS[:3])
    df["label"] = labels
    df.to_csv(os.path.join(out_dir, "audio_synth_features.csv"), index=False)
    print(f"audio: saved {waves.shape} windows + features -> {out_dir}")
    print(df.groupby("label")[["rms", "zcr", "centroid"]].mean().to_string())


if __name__ == "__main__":
    main()
