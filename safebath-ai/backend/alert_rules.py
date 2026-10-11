"""
Danger thresholds & alert mapping — one function per row of Master Guide
Section 7's table, so "what counts as danger" lives in exactly one reviewable,
unit-tested place instead of being scattered across fusion_engine.py.

Severity tiers referenced by callers:
  HIGH     -> Voice Bridge trigger (or immediate relay for scald)
  MEDIUM   -> dashboard/user-app only, never pages anyone
  WATCH/NORMAL -> fused-score bands
"""
from typing import Dict, Optional

from backend.cross_modal import acoustic_radar_gate, scald_shutoff_confirmed  # noqa: F401 (re-exported single source)

# ---- Module weights (Master Guide Section 11 — verbatim) -------------------
WEIGHTS: Dict[str, float] = {
    "fall_risk": 0.26,           # Module 1
    "possible_collapse": 0.20,   # Module 2
    "tof_collapse": 0.20,        # Module 5
    "acoustic_distress": 0.18,   # Module 4
    "thermal_freeze": 0.11,      # Module 6
    "toilet_biomarker": 0.05,    # Module 3 (lowered — Medium severity only)
}

# ---- fused score bands ------------------------------------------------------
SCORE_HIGH = 60.0     # > this          -> High: escalate past Voice Bridge
SCORE_WATCH = 30.0    # 30..this        -> Watch; below -> Normal


def fusion_band(score: float) -> str:
    if score > SCORE_HIGH:
        return "high"
    if score >= SCORE_WATCH:
        return "watch"
    return "normal"


# ---- Module 1 — Mat ---------------------------------------------------------
FALL_CLASS_STREAK_REQUIRED = 2          # consecutive "fall" frames
UNSTEADY_SUSTAINED_SECONDS = 10.0       # > this at "unsteady" -> Medium


def mat_fall_trigger(fall_class_streak: int) -> bool:
    """ConvLSTM classifies 'fall' for 2+ consecutive frames — High."""
    return fall_class_streak >= FALL_CLASS_STREAK_REQUIRED


def mat_unsteady_sustained(unsteady_seconds: float) -> bool:
    """Sustained 'unsteady' > 10s — Medium (score contribution only)."""
    return unsteady_seconds > UNSTEADY_SUSTAINED_SECONDS


# ---- Module 1 (interim) — RULE-BASED mat danger -----------------------------
# The ConvLSTM (fall_risk_model.h5) needs real training data; until then these
# physics rules provide the live fall/stumble signal. They feed the SAME
# 'fall_risk' flag as the model, so upgrading later changes nothing upstream.
MAT_SUSTAIN_HIGH_SUM = 67_500.0   # sum of all 72 cells above this (60k * 72/64)...
MAT_SUSTAIN_SECONDS = 2.0           # ...held for 2+ s straight -> body down
MAT_CENTROID_JUMP_CELLS = 5.0       # CoP shift between frames -> stumble


def mat_sustained_high_pressure(frame_sum: float,
                                seconds_at_high: float) -> bool:
    """Total pressure over threshold, sustained 2+s — someone down on the mat."""
    return (frame_sum > MAT_SUSTAIN_HIGH_SUM
            and seconds_at_high >= MAT_SUSTAIN_SECONDS)


def mat_centroid_cells(values):
    """Weighted center-of-pressure in cell units (row_f, col_f); None if empty.

    Same math as the dashboard heatmap's CoP trail so firmware, backend and UI
    agree on what 'position' means."""
    from backend.schemas import MAT_COLS, MAT_ROWS
    total = sum(values)
    if total <= 0:
        return None
    row_acc = col_acc = 0.0
    for r in range(MAT_ROWS):
        row_vals = values[r * MAT_COLS:(r + 1) * MAT_COLS]
        row_sum = sum(row_vals)
        row_acc += r * row_sum
        for c, v in enumerate(row_vals):
            col_acc += c * v
    return (row_acc / total, col_acc / total)


def mat_centroid_jump(prev_cop, curr_cop,
                      max_cells: float = MAT_CENTROID_JUMP_CELLS) -> bool:
    """CoP moved more than max_cells between consecutive frames — stumble."""
    if prev_cop is None or curr_cop is None:
        return False
    dr = curr_cop[0] - prev_cop[0]
    dc = curr_cop[1] - prev_cop[1]
    return (dr * dr + dc * dc) ** 0.5 > max_cells


# ---- Module 2 — Radar -------------------------------------------------------
RADAR_STILLNESS_MS = 120_000            # 120 s


def radar_possible_collapse(presence: bool, stillness_ms: int) -> bool:
    """presence AND stillness > 120s — High. Mirrored in radar_node firmware."""
    return bool(presence and stillness_ms > RADAR_STILLNESS_MS)


# ---- Module 3 — Toilet (Medium severity only — never pages anyone) ----------
UTI_STREAK_REQUIRED = 2                          # readings in a row
DEHYDRATION_REPEATS_24H = 3                      # readings within 24h


def toilet_uti_flag(uti_streak: int) -> bool:
    return uti_streak >= UTI_STREAK_REQUIRED


def toilet_dehydration_flag(dehydration_count_24h: int) -> bool:
    return dehydration_count_24h >= DEHYDRATION_REPEATS_24H


# ---- Module 4 — Audio -------------------------------------------------------
CRY_SUSTAINED_SECONDS = 5.0


def acoustic_thud_confirmed(audio_label: str, radar_presence: bool,
                            radar_motion: bool) -> bool:
    """impact_thud + independent radar confirmation — High (cross-modal gate)."""
    return acoustic_radar_gate(audio_label, radar_presence, radar_motion)


def acoustic_sustained_cry(speech_or_cry_seconds: float) -> bool:
    """speech_or_cry sustained > 5s — Medium-High."""
    return speech_or_cry_seconds > CRY_SUSTAINED_SECONDS


# ---- Module 5 — ToF / Thermal ----------------------------------------------
TOF_COLLAPSE_MM = 300                   # mm, all three sensors, sustained


def tof_collapse_trigger(tof_collapse: bool) -> bool:
    """All 3 VL53L1X < 300mm sustained — High. Mirrored in Board-5 firmware."""
    return bool(tof_collapse)


def scald_shutoff(unattended_hot_fill: bool, mat_pressure_present: bool) -> bool:
    """Immediate automatic action — relay cuts solenoid, no Voice Bridge wait."""
    return scald_shutoff_confirmed(unattended_hot_fill, mat_pressure_present)


VOICE_WAIT_SECONDS: Optional[float] = 12.0   # reply window after DFPlayer prompt
