"""Synthetic generator shape/content tests (fast, small-n)."""
import numpy as np

from datasets.audio_features import extract_features
from datasets.gen_audio_data import make_clip
from datasets.gen_mat_data import generate as gen_mat, make_sequence as mat_seq
from datasets.gen_thermal_data import generate as gen_thermal
from datasets.gen_toilet_data import COLUMNS, generate as gen_toilet
from datasets.gen_radar_stub_data import synthetic_radar_stream


class TestMat:
    def test_shapes_and_labels(self):
        X, y = gen_mat(n_per_class=4, seed=1)
        assert X.shape == (12, 20, 12, 6)   # 12x6 grid per final wiring
        assert set(np.unique(y)) == {0, 1, 2}
        assert X.max() <= 4095 and X.min() >= 0   # 12-bit ADC range

    def test_fall_has_sustained_pressure_tail(self):
        rng = np.random.default_rng(7)
        seq = mat_seq(2, rng)
        tail = seq[-6:]
        assert tail.mean() > 400          # body-down blob persists
        assert seq[:8].mean() < tail.mean()


class TestToilet:
    def test_columns_exact(self):
        df = gen_toilet(n_per_class=10, seed=3)
        assert list(df.columns) == COLUMNS
        # no temp_c anywhere — DS18B20 removed from final wiring
        assert "temp_c" not in df.columns

    def test_three_classes(self):
        df = gen_toilet(n_per_class=10, seed=3)
        assert set(df["label"]) == {"normal", "uti_risk", "dehydration_risk"}


class TestThermal:
    def test_shape_binary(self):
        X, y = gen_thermal(n_per_class=3, seed=2)
        assert X.shape == (6, 15, 8, 8)
        assert set(np.unique(y)) == {0, 1}

    def test_hazard_hotter_than_safe(self):
        X, y = gen_thermal(n_per_class=20, seed=2)
        assert X[y == 1].mean() > X[y == 0].mean() + 2.0


class TestAudio:
    def test_clip_length_and_sr(self):
        rng = np.random.default_rng(5)
        clip = make_clip(1, rng)
        assert len(clip) == 8000           # 1 s @ 8 kHz like the firmware

    def test_feature_extractor_separates_classes(self):
        rng = np.random.default_rng(5)
        silence = make_clip(0, rng)
        thud = make_clip(2, rng)
        speech = make_clip(3, rng)
        r_sil, _, _ = extract_features(silence)
        r_thud, z_thud, c_thud = extract_features(thud)
        r_spk, _, _ = extract_features(speech)
        assert r_sil < 0.05 < r_thud < r_spk      # energy ordering holds
        assert c_thud < 500                        # thud centroid is low-freq


def test_radar_stub_flag_index():
    """possible_collapse fires exactly when stillness_ms exceeds 120000."""
    first_true = None
    for i, (p, m, s) in enumerate(synthetic_radar_stream()):
        if p and m is False and s > 120000:
            first_true = i
            break
    # stillness = (i - 250) * 500; first i with that > 120000 is i=491
    assert first_true == 491
