"""Collect model metrics for the performance matrix."""
import os
os.environ["TF_USE_LEGACY_KERAS"] = "1"

import numpy as np
import joblib
import pandas as pd

# Model 1: Fall ConvLSTM
import tensorflow as tf
m1 = tf.keras.models.load_model("models/fall_risk_model.h5")
p1 = m1.count_params()
print("=== FALL RISK ConvLSTM ===")
m1.summary()
print(f"Total params: {p1}")

# Model 2: Thermal CNN+LSTM
m2 = tf.keras.models.load_model("models/thermal_freeze_model.h5")
p2 = m2.count_params()
print("\n=== THERMAL FREEZE CNN+LSTM ===")
m2.summary()
print(f"Total params: {p2}")

# Model 3: Toilet RF
clf = joblib.load("models/toilet_rf_model.pkl")
print(f"\n=== TOILET RF ===")
print(f"Total trees: {clf.n_estimators}")
print(f"Max depth: {clf.max_depth}")
feats = dict(zip(["gas_ppm", "voc_ppm", "turbidity"],
                  [round(x, 3) for x in clf.feature_importances_]))
print(f"Feature importances: {feats}")
print(f"Num features: {clf.n_features_in_}")

# File sizes
for f in [
    "models/fall_risk_model.h5",
    "models/fall_risk_model.tflite",
    "models/thermal_freeze_model.h5",
    "models/thermal_freeze_model.tflite",
    "models/toilet_rf_model.pkl",
    "firmware/shared/model_data/fall_risk_model_model_data.h",
    "firmware/shared/model_data/thermal_freeze_model_model_data.h",
]:
    sz = os.path.getsize(f)
    print(f"{os.path.basename(f)}: {sz / 1024:.1f} KB")

# Data sizes
matX = np.load("data/synthetic/mat_synth_X.npy")
maty = np.load("data/synthetic/mat_synth_y.npy")
thermX = np.load("data/synthetic/thermal_synth_X.npy")
thermy = np.load("data/synthetic/thermal_synth_y.npy")
toilet = pd.read_csv("data/synthetic/toilet_synth_data.csv")
print(f"\nMat training samples: {len(matX)} | Classes: {dict(zip(*np.unique(maty, return_counts=True)))}")
print(f"Thermal training samples: {len(thermX)} | Classes: {dict(zip(*np.unique(thermy, return_counts=True)))}")
print(f"Toilet training samples: {len(toilet)} | Classes: {dict(toilet.label.value_counts())}")

# Evaluate all on holdout
from sklearn.model_selection import train_test_split

# Fall
Xf = np.load("data/synthetic/mat_synth_X.npy").astype(np.float32) / 4095.0
yf = np.load("data/synthetic/mat_synth_y.npy")
_, Xte_f, _, yte_f = train_test_split(Xf, yf, test_size=0.2, stratify=yf, random_state=42)
pred_f = m1.predict(Xte_f, verbose=0).argmax(axis=1)
acc_f = float((pred_f == yte_f).mean())
from sklearn.metrics import classification_report, confusion_matrix
labels_f = ["normal", "unsteady", "fall"]
print(f"\nFall holdout accuracy: {acc_f:.3f}")
print("Confusion matrix:")
print(confusion_matrix(yte_f, pred_f))
print(classification_report(yte_f, pred_f, target_names=labels_f, digits=3))

# Thermal
Xt = np.clip((np.load("data/synthetic/thermal_synth_X.npy") - 20.0) / 30.0, -1.0, 2.0).astype(np.float32)
yt = np.load("data/synthetic/thermal_synth_y.npy")
_, Xte_t, _, yte_t = train_test_split(Xt, yt, test_size=0.2, stratify=yt, random_state=42)
pred_t = m2.predict(Xte_t, verbose=0).argmax(axis=1)
acc_t = float((pred_t == yte_t).mean())
labels_t = ["safe", "thermal_hazard"]
print(f"\nThermal holdout accuracy: {acc_t:.3f}")
print("Confusion matrix:")
print(confusion_matrix(yte_t, pred_t))
print(classification_report(yte_t, pred_t, target_names=labels_t, digits=3))

# Toilet
dft = pd.read_csv("data/synthetic/toilet_synth_data.csv")
Xto = dft[["gas_ppm", "voc_ppm", "turbidity"]].values
yto = dft["label"].values
_, Xte_to, _, yte_to = train_test_split(Xto, yto, test_size=0.2, stratify=yto, random_state=42)
pred_to = clf.predict(Xte_to)
acc_to = float((pred_to == yte_to).mean())
labels_to = sorted(clf.classes_)
print(f"\nToilet holdout accuracy: {acc_to:.3f}")
print("Confusion matrix:")
print(confusion_matrix(yte_to, pred_to, labels=labels_to))
print(classification_report(yte_to, pred_to, target_names=labels_to, digits=3))
