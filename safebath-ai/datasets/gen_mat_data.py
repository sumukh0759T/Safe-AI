"""
Synthetic pressure-mat training data — 12x6 grid (72 cells, row-major),
20-frame sequences, 3 classes: 0=normal, 1=unsteady, 2=fall.

Grid shape per final wiring (Master Guide Section 0/1): CD4067 = 12 rows
(channels 0-11), 74HC595 Q1-Q6 = 6 columns. Sequences are (20, 12, 6).

Physics-informed domain randomization:
 - normal   : slow gentle weight shifts, low spatial/temporal variance
 - unsteady : larger lateral sway of the center of pressure, higher variance
 - fall     : ~10 walking frames then rapid descent to a sustained broad
              high-pressure blob (body on floor) for the remaining frames
"""
import numpy as np

ROWS, COLS, T = 12, 6, 20
ADC_MAX = 4095

# TODO(calibration): replace with real Velostat ADC ranges from Board-1 bring-up
# (Master Guide Section 12 step 3). Baseline noise floor / gain applied here must
# match firmware/mat_node/mat_node.ino's CALIB_OFFSET / CALIB_GAIN.
CALIB_OFFSET = 120
CALIB_GAIN = 1.0


def _blob(grid_shape, row_c, col_c, row_sigma, col_sigma, peak):
    r, c = np.mgrid[0:grid_shape[0], 0:grid_shape[1]]
    g = np.exp(-(((r - row_c) ** 2) / (2 * row_sigma ** 2) +
                 ((c - col_c) ** 2) / (2 * col_sigma ** 2)))
    return g * peak


def make_sequence(label, rng):
    frames = np.zeros((T, ROWS, COLS), dtype=np.float32)
    base_peak = rng.uniform(1800, 3200)

    if label == 2:  # fall: brief steps then body-down, sustained
        n_walk = rng.integers(8, 12)
        rc = rng.uniform(2, 9)
        cc = rng.uniform(1.2, 4.2)
        for t in range(n_walk):
            sway = rng.normal(0, 0.35)
            frames[t] = _blob((ROWS, COLS), rc + sway, cc,
                              rng.uniform(1.2, 2.0), 0.9, base_peak)
        # rapid drop + spread (body hits floor between two adjacent frames)
        impact_t = n_walk
        floor_rc = min(rc + rng.uniform(2.0, 5.0), ROWS - 2)
        for t in range(impact_t, T):
            grow = 1.0 + 0.25 * (t - impact_t)
            frames[t] = _blob((ROWS, COLS), floor_rc, rng.uniform(1.2, 3.8),
                              2.6 * grow, 1.7 * grow, base_peak * 0.85)
    elif label == 1:  # unsteady: exaggerated slow sway, never settles
        phase = rng.uniform(0, 2 * np.pi)
        amp = rng.uniform(1.8, 3.6)
        for t in range(T):
            rc = 5.5 + amp * np.sin(phase + t * rng.uniform(0.5, 0.9))
            cc = 2.6 + 1.0 * np.sin(phase * 0.7 + t * 0.4)
            frames[t] = _blob((ROWS, COLS), rc, cc,
                              rng.uniform(1.4, 2.2), 0.95, base_peak * 0.9)
    else:  # normal
        rc, cc = rng.uniform(3, 9), rng.uniform(1.5, 4.2)
        drift = rng.normal(0, 0.18, size=(T, 2)).cumsum(axis=0)
        for t in range(T):
            frames[t] = _blob((ROWS, COLS),
                              np.clip(rc + drift[t, 0], 1.5, ROWS - 1.5),
                              np.clip(cc + drift[t, 1], 0.4, COLS - 0.4),
                              rng.uniform(1.2, 1.8), 0.9, base_peak)

    # sensor noise + calibration model + ADC clipping
    frames += rng.normal(0, CALIB_OFFSET * 0.25, size=frames.shape)
    frames = (frames + CALIB_OFFSET) * CALIB_GAIN
    return np.clip(frames, 0, ADC_MAX).astype(np.int16)


def generate(n_per_class=400, seed=42):
    rng = np.random.default_rng(seed)
    X = np.concatenate([[make_sequence(l, rng) for _ in range(n_per_class)]
                        for l in (0, 1, 2)])
    y = np.repeat([0, 1, 2], n_per_class)
    return X.astype(np.int16), y.astype(np.int64)


def main(out_dir="data/synthetic"):
    import os
    os.makedirs(out_dir, exist_ok=True)
    X, y = generate()
    # stored row-major so a flattened frame == the 72-int MQTT CSV payload order
    np.save(os.path.join(out_dir, "mat_synth_X.npy"), X)
    np.save(os.path.join(out_dir, "mat_synth_y.npy"), y)
    print(f"mat: saved {X.shape} frames, labels {np.bincount(y)} -> {out_dir}")


if __name__ == "__main__":
    main()
