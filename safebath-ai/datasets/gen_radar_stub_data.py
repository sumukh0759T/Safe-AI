"""
Radar rule stub data — lets tests exercise the Module 2 stillness rule before
the physical C4001 arrives (SOFTWARE_BUILD_SPEC Section 3.3).
"""
import numpy as np


def synthetic_radar_stream(n=500, collapse_at=250):
    """Yields (presence, motion, stillness_ms) tuples simulating a stillness event."""
    for i in range(n):
        presence = True
        if i < collapse_at:
            motion = np.random.rand() > 0.3
            stillness = 0 if motion else np.random.randint(0, 5000)
        else:
            motion = False
            stillness = (i - collapse_at) * 500
        yield presence, motion, stillness


if __name__ == "__main__":
    for p, m, s in synthetic_radar_stream():
        flag = p and s > 120000
        if flag:
            print(f"possible_collapse flagged at stillness_ms={s}")
            break
