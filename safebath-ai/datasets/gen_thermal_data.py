"""
Synthetic thermal training data for the AMG8833 (8x8) — 15-frame sequences,
binary labels: 0 = safe, 1 = hazard (scald/freeze signature).

No usable public dataset exists at 8x8 AMG8833 resolution (published thermal
datasets are 32x24 MLX90640 or unreleased) — generated directly at 8x8 to
match the actual sensor, per SOFTWARE_BUILD_SPEC Section 3.1.

Hazard signature modeled: a hot region (running hot water / freshly filled
tub) that grows and pushes the frame-average temperature upward across the
window; safe sequences stay in ambient/person range.
"""
import numpy as np

T, H, W = 15, 8, 8


def _blob(h, w, r_c, c_c, sigma_r, sigma_c, peak):
    r, c = np.mgrid[0:h, 0:w]
    g = np.exp(-(((r - r_c) ** 2) / (2 * sigma_r ** 2) +
                 ((c - c_c) ** 2) / (2 * sigma_c ** 2)))
    return g * peak


def make_sequence(label, rng):
    ambient = rng.uniform(23.0, 28.0)
    frames = np.full((T, H, W), ambient, dtype=np.float32)
    frames += rng.normal(0, 0.4, size=frames.shape)          # AMG8833 noise
    person_peak = rng.uniform(29.0, 33.0)

    if label == 1:  # hazard: hot tap/fill ramps up over the window
        r_c, c_c = rng.integers(0, 3), rng.integers(2, 6)     # fixture corner-ish
        start = rng.uniform(33.0, 36.0)
        end = rng.uniform(40.0, 45.0)
        for t in range(T):
            frac = t / (T - 1)
            peak = start + (end - start) * frac
            frames[t] += _blob(H, W, r_c, c_c, 2.0, 2.2, peak - ambient)
            # person nearby sometimes, but the hot blob dominates
            if rng.random() < 0.4:
                frames[t] += _blob(H, W, rng.integers(4, 8), rng.integers(0, 8),
                                   1.2, 1.2, person_peak - ambient)
    else:  # safe: ambient + optional person-shaped warm blob, stable
        pr, pc = rng.integers(1, 7), rng.integers(0, 8)
        for t in range(T):
            drift_r = rng.normal(0, 0.5)
            drift_c = rng.normal(0, 0.5)
            frames[t] += _blob(H, W, np.clip(pr + drift_r, 0, 7),
                               np.clip(pc + drift_c, 0, 7),
                               1.3, 1.3, (person_peak - ambient) *
                               (0.9 + 0.1 * np.sin(t)))
    return np.clip(frames, ambient - 3.0, 60.0)


def generate(n_per_class=500, seed=42):
    rng = np.random.default_rng(seed)
    X = np.concatenate([[make_sequence(l, rng) for _ in range(n_per_class)]
                        for l in (0, 1)])
    y = np.repeat([0, 1], n_per_class)
    return X.astype(np.float32), y.astype(np.int64)


def main(out_dir="data/synthetic"):
    import os
    os.makedirs(out_dir, exist_ok=True)
    X, y = generate()
    np.save(os.path.join(out_dir, "thermal_synth_X.npy"), X)
    np.save(os.path.join(out_dir, "thermal_synth_y.npy"), y)
    print(f"thermal: saved {X.shape}, labels {np.bincount(y)} -> {out_dir}")


if __name__ == "__main__":
    main()
