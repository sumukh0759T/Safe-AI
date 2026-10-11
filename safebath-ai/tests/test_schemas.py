"""Schema contract tests — malformed payloads must fail loudly."""
import pytest
from pydantic import ValidationError

from backend.schemas import (
    MAT_CELL_COUNT,
    MAT_COLS,
    MAT_ROWS,
    MAT_ZONE_TOPIC,
    AudioEventPayload,
    MatFramePayload,
    Pressure4ZonePayload,
    RadarStatusPayload,
    ToiletReadingPayload,
    TofThermalStatusPayload,
    VoiceReplyPayload,
    parse_for_topic,
)

VALID_CSV = ",".join(["100"] * MAT_CELL_COUNT)


class TestMatFrame:
    def test_valid_csv(self):
        p = MatFramePayload.from_csv(VALID_CSV)
        assert len(p.values) == MAT_CELL_COUNT == 72
        assert p.as_grid()[0][0] == 100

    def test_grid_geometry(self):
        assert (MAT_ROWS, MAT_COLS) == (12, 6)
        p = MatFramePayload.from_csv(VALID_CSV)
        grid = p.as_grid()
        assert len(grid) == 12 and len(grid[0]) == 6

    def test_wrong_length_fails(self):
        with pytest.raises((ValidationError, ValueError)):
            MatFramePayload.from_csv(",".join(["1"] * (MAT_CELL_COUNT - 1)))

    def test_out_of_range_adc_fails(self):
        bad = ",".join(["4095"] * (MAT_CELL_COUNT - 1) + ["5000"])
        with pytest.raises(ValidationError):
            MatFramePayload.from_csv(bad)

    def test_non_int_csv_fails(self):
        with pytest.raises((ValidationError, ValueError)):
            MatFramePayload.from_csv("a,b," + ",".join(["1"] * (MAT_CELL_COUNT - 2)))

    def test_json_form_accepted(self):
        import json
        raw = json.dumps({"values": [0] * MAT_CELL_COUNT})
        assert len(parse_for_topic("sensor/mat/frame", raw).values) == MAT_CELL_COUNT


class TestRadar:
    def test_valid(self):
        p = RadarStatusPayload.model_validate_json(
            '{"presence": true, "motion": false, "stillness_ms": 125000, '
            '"possible_collapse": true}')
        assert p.possible_collapse is True

    def test_negative_stillness_fails(self):
        with pytest.raises(ValidationError):
            RadarStatusPayload(presence=True, motion=False,
                               stillness_ms=-1, possible_collapse=False)

    def test_extra_field_fails(self):
        with pytest.raises(ValidationError):
            RadarStatusPayload(presence=True, motion=False, stillness_ms=0,
                               possible_collapse=False, surprise=1)


class TestToilet:
    def test_valid_no_temp_c(self):
        p = ToiletReadingPayload(gas_ppm=60, voc_ppm=25, turbidity=2.0,
                                 hour_of_day=9)
        assert p.temp_c is None   # DS18B20 removed — optional for the future

    def test_optional_temp_c_accepted_when_added_back(self):
        p = ToiletReadingPayload(gas_ppm=60, voc_ppm=25, turbidity=2.0,
                                 hour_of_day=9, temp_c=36.8)
        assert p.temp_c == 36.8

    def test_bad_hour_fails(self):
        with pytest.raises(ValidationError):
            ToiletReadingPayload(gas_ppm=60, voc_ppm=25, turbidity=2.0,
                                 hour_of_day=24)


class TestAudio:
    def test_valid_label(self):
        p = AudioEventPayload(label="impact_thud", energy=0.4)
        assert p.label == "impact_thud"

    def test_unknown_label_fails(self):
        with pytest.raises(ValidationError):
            AudioEventPayload(label="explosion", energy=0.4)

    def test_negative_energy_fails(self):
        with pytest.raises(ValidationError):
            AudioEventPayload(label="silence", energy=-0.01)


class TestTofVoice:
    def test_tof(self):
        p = TofThermalStatusPayload(avg_thermal_c=26.5, flow_pulses=3,
                                    tof_collapse=False,
                                    unattended_hot_fill=False)
        assert p.flow_pulses == 3

    def test_voice_reply_literal(self):
        assert VoiceReplyPayload(reply="fine").reply == "fine"
        with pytest.raises(ValidationError):
            VoiceReplyPayload(reply="maybe")


class TestPressure4Zone:
    def test_valid(self):
        p = Pressure4ZonePayload(sensor_type="pressure_4zone",
                                 zones=[1200, 2900, 600, 2100])
        assert p.zones == [1200, 2900, 600, 2100]
        assert p.sensor_type == "pressure_4zone"

    def test_sensor_type_defaults(self):
        assert Pressure4ZonePayload(zones=[0, 0, 0, 0]).sensor_type == \
            "pressure_4zone"

    def test_accepts_floats(self):
        assert Pressure4ZonePayload(zones=[1.5, 2, 3, 4.25]).zones[3] == 4.25

    def test_wrong_count_fails(self):
        with pytest.raises(ValidationError):
            Pressure4ZonePayload(zones=[1, 2, 3])
        with pytest.raises(ValidationError):
            Pressure4ZonePayload(zones=[1, 2, 3, 4, 5])

    def test_negative_fails(self):
        with pytest.raises(ValidationError):
            Pressure4ZonePayload(zones=[1, -2, 3, 4])

    def test_non_finite_fails(self):
        with pytest.raises(ValidationError):
            Pressure4ZonePayload(zones=[float("nan"), 2, 3, 4])
        with pytest.raises(ValidationError):
            Pressure4ZonePayload(zones=[float("inf"), 2, 3, 4])

    def test_from_csv(self):
        p = Pressure4ZonePayload.from_csv("1200,2900,600,2100")
        assert p.zones == [1200, 2900, 600, 2100]

    def test_parse_for_topic_json_and_csv(self):
        import json
        raw = json.dumps({"sensor_type": "pressure_4zone", "zones": [1, 2, 3, 4]})
        assert parse_for_topic(MAT_ZONE_TOPIC, raw).zones == [1, 2, 3, 4]
        assert parse_for_topic(MAT_ZONE_TOPIC, "1,2,3,4").zones == [1, 2, 3, 4]

    def test_extra_field_fails(self):
        with pytest.raises(ValidationError):
            Pressure4ZonePayload(zones=[1, 2, 3, 4], values=[0] * 72)


def test_parse_for_topic_unknown_topic():
    with pytest.raises(ValueError):
        parse_for_topic("sensor/unknown/topic", "{}")
