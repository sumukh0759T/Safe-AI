"""
Synthetic toilet-biomarker training data — 3 classes: normal / uti_risk /
dehydration_risk. Features per FINAL wiring (Master Guide Section 3):
gas_ppm (MQ-135), voc_ppm (MQ-3), turbidity (TCS3200 color proxy).
No temp_c — DS18B20 is not in the final connection list.

No public dataset exists for this sensor combination (novel setup — say so
proudly, per SOFTWARE_BUILD_SPEC Section 3.1 honesty note). Synthetic-only
is the correct approach, not a shortcut.
"""
import numpy as np
import pandas as pd

COLUMNS = ["gas_ppm", "voc_ppm", "turbidity", "hour_of_day", "label"]


def _class_stats(label, rng):
    if label == "uti_risk":
        # elevated leukocyte/nitrite signature -> murkier sample + mild gas rise
        return dict(gas=(95, 20), voc=(38, 10), turb=(7.5, 1.2))
    if label == "dehydration_risk":
        # concentrated waste -> stronger volatile signal, darker/murkier color
        return dict(gas=(120, 25), voc=(60, 12), turb=(6.2, 1.5))
    return dict(gas=(60, 15), voc=(25, 8), turb=(2.0, 0.8))


def generate(n_per_class=1000, seed=42):
    rng = np.random.default_rng(seed)
    rows = []
    for label in ("normal", "uti_risk", "dehydration_risk"):
        s = _class_stats(label, rng)
        for _ in range(n_per_class):
            # bathroom visits cluster morning/evening; biomarker signal slightly
            # stronger on first visit of the day
            hour = int(np.clip(rng.normal(8, 4) if rng.random() < 0.6
                               else rng.uniform(0, 24), 0, 23))
            boost = 1.15 if hour < 10 else 1.0
            rows.append({
                "gas_ppm": round(float(max(0, rng.normal(*s["gas"])) * boost), 2),
                "voc_ppm": round(float(max(0, rng.normal(*s["voc"])) * boost), 2),
                "turbidity": round(float(max(0, rng.normal(*s["turb"]))), 2),
                "hour_of_day": hour,
                "label": label,
            })
    df = pd.DataFrame(rows)[COLUMNS]
    return df.sample(frac=1.0, random_state=seed).reset_index(drop=True)


def main(out_dir="data/synthetic"):
    import os
    os.makedirs(out_dir, exist_ok=True)
    df = generate()
    path = os.path.join(out_dir, "toilet_synth_data.csv")
    df.to_csv(path, index=False)
    print(f"toilet: saved {len(df)} rows -> {path}")
    print(df["label"].value_counts().to_string())


if __name__ == "__main__":
    main()
