"""
Module 3 — Toilet Biomarker Random Forest.

Input : data/synthetic/toilet_synth_data.csv
        3 features (gas_ppm, voc_ppm, turbidity) per FINAL wiring — no temp_c
Model : RandomForestClassifier(n_estimators=100, max_depth=6)
Output: models/toilet_rf_model.pkl

DoD (SOFTWARE_BUILD_SPEC 5.3): test accuracy >= 0.80 + confusion matrix printed.
"""
import os

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import confusion_matrix, classification_report
from sklearn.model_selection import train_test_split

CSV = "data/synthetic/toilet_synth_data.csv"
OUT = "models/toilet_rf_model.pkl"
FEATURES = ["gas_ppm", "voc_ppm", "turbidity"]
TARGET_ACC = 0.80


def main():
    if not os.path.exists(CSV):
        raise SystemExit(f"{CSV} missing — run datasets/gen_toilet_data.py first")
    df = pd.read_csv(CSV)
    X, y = df[FEATURES].values, df["label"].values

    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2,
                                          stratify=y, random_state=42)
    clf = RandomForestClassifier(n_estimators=100, max_depth=6,
                                 random_state=42, n_jobs=-1)
    clf.fit(Xtr, ytr)

    acc = clf.score(Xte, yte)
    print(f"test accuracy: {acc:.3f}")
    print("confusion matrix (rows=true, cols=pred):")
    labels = sorted(clf.classes_)
    print(pd.DataFrame(confusion_matrix(yte, clf.predict(Xte), labels=labels),
                       index=labels, columns=labels).to_string())
    print(classification_report(yte, clf.predict(Xte), digits=2))
    if acc < TARGET_ACC:
        print(f"WARNING: below {TARGET_ACC} DoD target")

    os.makedirs("models", exist_ok=True)
    joblib.dump(clf, OUT)
    print(f"saved {OUT}")


if __name__ == "__main__":
    main()
