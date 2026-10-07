"""
Cross-modal confirmation rules — SOFTWARE_BUILD_SPEC Section 12.3 / Master
Guide Section 7, implemented as testable functions (not prose).

fusion_engine.py imports and uses all three; tests/test_fusion_engine.py owns
their truth tables. Do not re-implement this logic inline elsewhere.
"""


def event_triggered_biomarker_sample(mat_unsteady: bool, tof_collapse: bool) -> bool:
    """Trigger an immediate high-priority toilet reading when Module 1 or 5 flags."""
    return mat_unsteady or tof_collapse


def acoustic_radar_gate(audio_class: str, radar_presence: bool, radar_motion: bool) -> bool:
    """Only escalate an 'impact_thud' if radar independently confirms a body-sized
    target was present and moving at that timestamp — attacks the 'running water
    masks distress sounds' false-positive mode."""
    return audio_class == "impact_thud" and radar_presence and radar_motion


def scald_shutoff_confirmed(unattended_hot_fill: bool, mat_pressure_present: bool) -> bool:
    """Cross-check Module 1 before Module 6 triggers the solenoid — reduces nuisance
    shutoffs when someone is actually standing at the tub/sink."""
    return unattended_hot_fill and not mat_pressure_present
